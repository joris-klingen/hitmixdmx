"""Convert a hand-programmed "RGB" Ableton set into a pixel-based lightgen Spec.

The source format is a different rig than DMXIS-direct: a MIDI track whose clip
envelopes automate the Macro Controls of an Instrument Rack, and the rack is
mapped (inside Live, opaquely to us) to a DMX plugin. Each clip therefore
boils down to per-macro automation. The macros are named by the user:

    Macro 0  Master Dim       (ignored — driven from hardware knob)
    Macro 1  BAR Switch       → continuous L↔R pan across the two bars
    Macro 2  WASH Warm        (ignored)
    Macro 3  VOX SPOT white   → singer spots, warm-white
    Macro 4  Red              → bar pixel red
    Macro 5  Green            → bar pixel green
    Macro 6  Blue             → bar pixel blue
    Macro 7  Strobe           → wild random RGB chase on bars
    Macro 9,10,11             (ignored)

Macro values are 0..127 in the source; we normalise to 0..1.

Output: a `Spec` against the standard `hitmix` rig, expressing each segment of
constant macro values as `Fade` events with equal start/end (i.e. clean stepped
automation) on each pixel, plus per-clip seeded pattern masks so the pixel
distribution varies per clip.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .als_io import TemplateInfo
from .spec import Clip, ColorStab, Fade, Spec


MACRO_ROLES = {
    "BAR Switch": "bar_pan",
    "VOX SPOT white": "vox",
    "Red": "r",
    "Green": "g",
    "Blue": "b",
    "Strobe": "strobe",
    "barmode def 0 !": "barmode",
}
"""Mapping from MacroDisplayNames.N value → semantic role used by the converter."""

LIVE_PREROLL_TIME = -63072000.0
"""Sentinel time value Live writes for the "value at clip start" event."""

MACRO_FULL_SCALE = 127.0


@dataclass
class MacroEnvelope:
    """Sorted (time, value) list for one macro on one clip, in 0..1 space."""

    role: str
    points: list[tuple[float, float]] = field(default_factory=list)

    def sample(self, t: float) -> float:
        """Value at time `t` — rightmost point with point_time <= t (post-jump)."""
        if not self.points:
            return 0.0
        chosen = self.points[0][1]
        for pt, pv in self.points:
            if pt <= t + 1e-9:
                chosen = pv
            else:
                break
        return chosen


@dataclass
class LegacyClip:
    name: str
    slot: int
    length_beats: float
    color_index: int
    macros: dict[str, MacroEnvelope]
    source_xml: ET.Element | None = None
    """Deep-copy of the source <MidiClip> XML, kept so we can carry forward
    TimeSignature / Loop / CurrentStart / CurrentEnd into the rendered output."""

    def segment_boundaries(self) -> list[float]:
        """Union of all macro event times in [0, length_beats], inclusive of 0 and end."""
        times = {0.0, self.length_beats}
        for env in self.macros.values():
            for pt, _ in env.points:
                if 0.0 <= pt <= self.length_beats:
                    times.add(pt)
        return sorted(times)


def _read_macros(rack: ET.Element) -> tuple[dict[int, str], dict[str, float]]:
    """Return ({AT_id → role}, {role → manual_value_normalized}) for the role macros.

    The manual value is the rack's "current knob position" and is used by Live
    when a clip has no envelope for that macro. We capture it so clips that
    don't automate (say) BAR Switch still get a sensible default rather than 0.
    """
    roles: dict[int, str] = {}
    defaults: dict[str, float] = {}
    for idx in range(16):
        name_el = rack.find(f"MacroDisplayNames.{idx}")
        macro_el = rack.find(f"MacroControls.{idx}")
        if name_el is None or macro_el is None:
            continue
        display_name = name_el.get("Value", "")
        role = MACRO_ROLES.get(display_name)
        if role is None:
            continue
        at = macro_el.find("AutomationTarget")
        if at is None:
            continue
        roles[int(at.get("Id"))] = role
        manual = macro_el.find("Manual")
        if manual is not None:
            v = max(0.0, min(1.0, float(manual.get("Value")) / MACRO_FULL_SCALE))
            defaults[role] = v
    return roles, defaults


def _parse_clip_envelopes(
    clip_xml: ET.Element, roles: dict[int, str]
) -> dict[str, MacroEnvelope]:
    """Pull out the subset of envelopes whose target is one of our role macros."""
    out: dict[str, MacroEnvelope] = {}
    for env in clip_xml.findall(".//Envelopes/Envelopes/ClipEnvelope"):
        pid_el = env.find("EnvelopeTarget/PointeeId")
        if pid_el is None:
            continue
        at_id = int(pid_el.get("Value"))
        role = roles.get(at_id)
        if role is None:
            continue
        points: list[tuple[float, float]] = []
        for fe in env.findall("Automation/Events/FloatEvent"):
            t = float(fe.get("Time"))
            v = float(fe.get("Value")) / MACRO_FULL_SCALE
            v = max(0.0, min(1.0, v))
            # Live's preroll sentinel: treat as t=0 for our segmentation purposes.
            if t <= LIVE_PREROLL_TIME + 1:
                t = 0.0
            points.append((t, v))
        points.sort(key=lambda p: p[0])
        out[role] = MacroEnvelope(role=role, points=points)
    return out


def read_legacy_clips(path: str | Path, *, track_index: int = 0) -> list[LegacyClip]:
    """Read all populated clips from the given track in a legacy .als."""
    with gzip.open(Path(path), "rb") as f:
        root = ET.fromstring(f.read())
    tracks = list(root.find("LiveSet/Tracks"))
    track = tracks[track_index]
    rack = track.find(".//InstrumentGroupDevice")
    if rack is None:
        raise RuntimeError(
            f"track {track_index} has no InstrumentGroupDevice — "
            "is this the legacy macro-driven track?"
        )
    roles, defaults = _read_macros(rack)
    missing = set(MACRO_ROLES.values()) - set(roles.values())
    if missing:
        raise RuntimeError(
            f"could not resolve macros for roles {sorted(missing)} — "
            f"check that MacroDisplayNames match: {sorted(MACRO_ROLES)}"
        )
    clips: list[LegacyClip] = []
    for slot_idx, slot in enumerate(track.findall(".//ClipSlotList/ClipSlot")):
        clip_xml = slot.find("ClipSlot/Value/MidiClip")
        if clip_xml is None:
            continue
        name = clip_xml.find("Name").get("Value")
        length = float(clip_xml.find("Loop/LoopEnd").get("Value"))
        color = int(clip_xml.find("Color").get("Value"))
        macros = _parse_clip_envelopes(clip_xml, roles)
        for role in MACRO_ROLES.values():
            if role not in macros:
                macros[role] = MacroEnvelope(
                    role=role, points=[(0.0, defaults.get(role, 0.0))]
                )
        clips.append(
            LegacyClip(
                name=name,
                slot=slot_idx,
                length_beats=length,
                color_index=color,
                macros=macros,
                source_xml=copy.deepcopy(clip_xml),
            )
        )
    return clips


CLIP_PROPS_TO_COPY = ("TimeSignature", "Loop", "CurrentStart", "CurrentEnd")
"""Top-level <MidiClip> sub-elements we replace with the source's copy after
render, so the output preserves the user's time signature and loop layout
instead of inheriting them from the template's clone source."""


def patch_clip_properties(
    template: TemplateInfo,
    legacy_clips: list[LegacyClip],
    *,
    slot_offset: int = 0,
) -> None:
    """Carry source-clip properties (time sig, loop) into the rendered output."""
    for lc in legacy_clips:
        if lc.source_xml is None:
            continue
        slot = template.clip_slots[slot_offset + lc.slot]
        out_clip = slot.find("ClipSlot/Value/MidiClip")
        if out_clip is None:
            continue
        for tag in CLIP_PROPS_TO_COPY:
            src_el = lc.source_xml.find(tag)
            out_el = out_clip.find(tag)
            if src_el is None or out_el is None:
                continue
            idx = list(out_clip).index(out_el)
            out_clip.remove(out_el)
            out_clip.insert(idx, copy.deepcopy(src_el))


# --- Pattern masks --------------------------------------------------------

PIXELS_PER_BAR = 18
"""Number of pixels per bar in the hitmix rig — matches HITMIX_RIG.left_bar.pixels."""

PATTERN_NAMES = [
    "solid",
    "blocks_3",
    "blocks_6",
    "alternating",
    "every_third",
    "every_fourth",
    "halves",
    "thirds",
    "edges",
    "center",
    "random_50",
    "random_30",
    "ramp_up",
    "ramp_down",
]


def _pattern_mask(name: str, rng: random.Random) -> list[float]:
    """Per-pixel multiplier in [0, 1] for the named pattern. Length 18."""
    n = PIXELS_PER_BAR
    if name == "solid":
        return [1.0] * n
    if name == "blocks_3":
        return [1.0 if ((p - 1) // 3) % 2 == 0 else 0.0 for p in range(1, n + 1)]
    if name == "blocks_6":
        return [1.0 if ((p - 1) // 6) % 2 == 0 else 0.0 for p in range(1, n + 1)]
    if name == "alternating":
        return [1.0 if (p - 1) % 2 == 0 else 0.0 for p in range(1, n + 1)]
    if name == "every_third":
        return [1.0 if (p - 1) % 3 == 0 else 0.0 for p in range(1, n + 1)]
    if name == "every_fourth":
        return [1.0 if (p - 1) % 4 == 0 else 0.0 for p in range(1, n + 1)]
    if name == "halves":
        return [1.0 if p <= n // 2 else 0.0 for p in range(1, n + 1)]
    if name == "thirds":
        return [1.0 if (p - 1) < n // 3 or (p - 1) >= 2 * n // 3 else 0.0 for p in range(1, n + 1)]
    if name == "edges":
        return [1.0 if p <= 3 or p > n - 3 else 0.0 for p in range(1, n + 1)]
    if name == "center":
        return [1.0 if (n // 2 - 3) < p <= (n // 2 + 3) else 0.0 for p in range(1, n + 1)]
    if name == "random_50":
        return [1.0 if rng.random() < 0.5 else 0.0 for _ in range(n)]
    if name == "random_30":
        return [1.0 if rng.random() < 0.3 else 0.0 for _ in range(n)]
    if name == "ramp_up":
        return [p / n for p in range(1, n + 1)]
    if name == "ramp_down":
        return [(n - p + 1) / n for p in range(1, n + 1)]
    raise ValueError(f"unknown pattern {name!r}")


def _seeded_rng(clip_name: str) -> random.Random:
    """Deterministic per-clip RNG. Same name → same pattern."""
    h = hashlib.sha1(clip_name.encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "big")
    return random.Random(seed)


# --- Conversion -----------------------------------------------------------

# Warm-white tint for the singer spots — paired with the white channel at full.
WARM_R = 0.4
WARM_G = 0.15
SINGER_FIXTURES = ("singer_left", "singer_right")
BAR_FIXTURES = ("left_bar", "right_bar")
STROBE_COLOR: tuple[float, float, float] = (1.0, 1.0, 1.0)
"""Strobe is always white — the user's lights handle hue elsewhere."""
STROBE_MAX_RATE_PER_BEAT = 24.0
STROBE_FLASH_DUR = 0.04

BARMODE_THRESHOLD = 0.05
"""barmode macro value above which the red chase replaces the static bar color."""
BARMODE_CHASE_STEP = 0.08
"""Beats between adjacent pixels in the chase sweep."""
BARMODE_CHASE_DURATION = 0.18
"""Beats each pixel stays lit during the chase — slight overlap for a smooth trail."""
BARMODE_CHASE_PERIOD = 1.5
"""Beats between consecutive sweep starts."""
BARMODE_RED_TINT: tuple[float, float, float] = (1.0, 0.15, 0.0)
"""Predominantly red, mild orange — multiplied by the live barmode value for intensity."""


def _bar_gains(bar_pan: float) -> tuple[float, float]:
    """Linear L↔R pan: 0 → full left, 0.5 → both at 0.5, 1 → full right."""
    return 1.0 - bar_pan, bar_pan


COLOR_SWITCH_COSINE = 0.7
"""Cosine similarity below this triggers a new pattern — a "large color switch"."""

PAN_SWITCH_THRESHOLD = 0.15
"""BAR Switch delta in normalised (0..1) space that counts as a flip."""

INTENSITY_PULSE_THRESHOLD = 0.8
"""Single-step brightness delta that counts as a "pulse" even within one color."""


def _color_similarity(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Cosine similarity between two RGB triples. Returns 1.0 if either is off,
    so on↔off transitions don't count as a color switch — the same hue pulsing
    keeps its pattern."""
    ma = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5
    mb = (b[0] * b[0] + b[1] * b[1] + b[2] * b[2]) ** 0.5
    if ma < 1e-6 or mb < 1e-6:
        return 1.0
    return (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (ma * mb)


def _clip_to_events(clip: LegacyClip) -> list:
    boundaries = clip.segment_boundaries()
    rng = _seeded_rng(clip.name)

    # Pattern re-rolls on any of:
    #  - BAR Switch swing
    #  - large color-identity change (cosine-similarity drop)
    #  - large brightness pulse (delta > threshold) even within one color
    # Smooth modulation of a single hue at steady intensity keeps the same LEDs.
    mask_left: list[float] | None = None
    mask_right: list[float] | None = None
    last_nonzero_color: tuple[float, float, float] | None = None
    last_pan: float | None = None
    last_brightness = 0.0

    events: list = []
    for t0, t1 in zip(boundaries, boundaries[1:]):
        if t1 <= t0:
            continue
        r = clip.macros["r"].sample(t0)
        g = clip.macros["g"].sample(t0)
        b = clip.macros["b"].sample(t0)
        bar_pan = clip.macros["bar_pan"].sample(t0)
        vox = clip.macros["vox"].sample(t0)
        strobe = clip.macros["strobe"].sample(t0)
        barmode = clip.macros["barmode"].sample(t0)
        color = (r, g, b)
        brightness = max(r, g, b)
        color_is_on = brightness >= 0.02

        trigger = mask_left is None
        if not trigger and last_pan is not None and abs(bar_pan - last_pan) > PAN_SWITCH_THRESHOLD:
            trigger = True
        if (
            not trigger
            and color_is_on
            and last_nonzero_color is not None
            and _color_similarity(color, last_nonzero_color) < COLOR_SWITCH_COSINE
        ):
            trigger = True
        if not trigger and abs(brightness - last_brightness) > INTENSITY_PULSE_THRESHOLD:
            trigger = True

        if trigger:
            pattern_name = rng.choice(PATTERN_NAMES)
            mask_left = _pattern_mask(pattern_name, rng)
            mask_right = _pattern_mask(pattern_name, rng)

        if color_is_on:
            last_nonzero_color = color
        last_pan = bar_pan
        last_brightness = brightness

        if barmode > BARMODE_THRESHOLD:
            # Suppress the static bar color and overlay a red chase. The 0-fades
            # ensure the previous segment's color doesn't bleed through.
            events.extend(_bar_segment_events(t0, t1, 0.0, 0.0, 0.0, bar_pan, mask_left, mask_right))
            events.extend(_barmode_chase_events(t0, t1, barmode))
        else:
            events.extend(_bar_segment_events(t0, t1, r, g, b, bar_pan, mask_left, mask_right))
        events.extend(_spot_segment_events(t0, t1, vox))
        if strobe > 0:
            events.extend(_strobe_segment_events(t0, t1, strobe, rng))
    return events


def _bar_segment_events(
    t0: float,
    t1: float,
    r: float,
    g: float,
    b: float,
    bar_pan: float,
    mask_left: list[float],
    mask_right: list[float],
) -> list:
    left_gain, right_gain = _bar_gains(bar_pan)
    out: list = []
    for fixture, gain, mask in (
        ("left_bar", left_gain, mask_left),
        ("right_bar", right_gain, mask_right),
    ):
        for p in range(1, PIXELS_PER_BAR + 1):
            m = mask[p - 1] * gain
            color = (r * m, g * m, b * m)
            out.append(
                Fade(
                    type="fade",
                    fixture=fixture,
                    pixel=p,
                    component="rgb",
                    t_start=t0,
                    t_end=t1,
                    color_start=color,
                    color_end=color,
                )
            )
    return out


def _spot_segment_events(t0: float, t1: float, vox: float) -> list:
    """Warm-white singers, brightness scaled by vox. Color channels held at warm tint."""
    out: list = []
    for fixture in SINGER_FIXTURES:
        for component, value in (
            ("dimmer", vox),
            ("white", 1.0 if vox > 0 else 0.0),
            ("red", WARM_R if vox > 0 else 0.0),
            ("green", WARM_G if vox > 0 else 0.0),
        ):
            out.append(
                Fade(
                    type="fade",
                    fixture=fixture,
                    component=component,
                    t_start=t0,
                    t_end=t1,
                    value_start=value,
                    value_end=value,
                )
            )
    return out


def _barmode_chase_events(t0: float, t1: float, intensity: float) -> list:
    """Predominantly-red sweep across both bars while barmode is on.

    Emits per-pixel `color_stab`s staggered by `BARMODE_CHASE_STEP` beats and
    repeating every `BARMODE_CHASE_PERIOD` beats. Stabs are bounded to the
    segment so they don't leak into adjacent (barmode-off) segments.
    """
    tr, tg, tb = BARMODE_RED_TINT
    color = (tr * intensity, tg * intensity, tb * intensity)
    out: list = []
    sweep_t = t0
    while sweep_t < t1 - 1e-6:
        for p in range(1, PIXELS_PER_BAR + 1):
            stab_t = sweep_t + (p - 1) * BARMODE_CHASE_STEP
            if stab_t >= t1 - 1e-6:
                break
            dur = min(BARMODE_CHASE_DURATION, t1 - stab_t - 1e-6)
            if dur <= 0:
                continue
            for fixture in BAR_FIXTURES:
                out.append(
                    ColorStab(
                        type="color_stab",
                        fixture=fixture,
                        pixel=p,
                        time=stab_t,
                        duration=dur,
                        color=color,
                    )
                )
        sweep_t += BARMODE_CHASE_PERIOD
    return out


def _strobe_segment_events(
    t0: float, t1: float, strobe: float, rng: random.Random
) -> list:
    """Wild random RGB chase across bar pixels. Density scales with strobe value."""
    duration = t1 - t0
    n_stabs = max(1, int(round(strobe * STROBE_MAX_RATE_PER_BEAT * duration)))
    out: list = []
    for _ in range(n_stabs):
        t = t0 + rng.uniform(0.0, duration)
        fixture = rng.choice(BAR_FIXTURES)
        pixel = rng.randint(1, PIXELS_PER_BAR)
        dur = min(STROBE_FLASH_DUR, t1 - t - 1e-6)
        if dur <= 0:
            continue
        out.append(
            ColorStab(
                type="color_stab",
                fixture=fixture,
                pixel=pixel,
                time=t,
                duration=dur,
                color=STROBE_COLOR,
            )
        )
    return out


def convert_to_spec(
    legacy_clips: list[LegacyClip], *, limit: int | None = None, slot_offset: int = 0
) -> Spec:
    """Build a Spec from legacy clips, optionally limited to the first `limit`.

    Output slot indices preserve the source's slot positions (shifted by
    `slot_offset`), so empty source slots remain empty in the destination —
    keeping the grid layout intact for paste-back into the original set.
    """
    selected = legacy_clips[:limit] if limit is not None else legacy_clips
    spec_clips: list[Clip] = []
    for lc in selected:
        events = _clip_to_events(lc)
        spec_clips.append(
            Clip(
                name=lc.name or f"legacy_{lc.slot}",
                slot=slot_offset + lc.slot,
                length_beats=lc.length_beats,
                color_index=lc.color_index if 0 <= lc.color_index <= 69 else 1,
                events=events,
            )
        )
    return Spec(version=1, rig="hitmix", clips=spec_clips)
