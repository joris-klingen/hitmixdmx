"""MIDI clip → DMX automation translator.

Reads MIDI notes from clips on a `DMX_notes` MidiTrack and writes per-channel
DMX automation as MidiClips on a `DMX_plugin` MidiTrack (the one that hosts the
DMX plugin). Both tracks live in the same `.als` file.

MIDI mapping (Ableton numbering, C3 = MIDI 60):

  Octave 1 — utilities (C-2 to B-2, MIDI 0..11)
    0..3   spot notes: (L WW, L Sec, R WW, R Sec)
    4..11  bar selectors — vel >= 64 → primary, < 64 → secondary
  Octave 2 — pixel statics (C-1 to B-1, MIDI 12..23) — same vel routing
  Octave 3 — dynamics (C0 to B0, MIDI 24..35) — TODO, currently ignored
  Octaves 4-5 — primary colors  (C1..B2,  MIDI 36..59), velocity = intensity
  Octaves 6-7 — secondary colors (C3..B4, MIDI 60..83), velocity = intensity
  MIDI 84 — master blackout (TODO)

Layer composition: each of utility / static / dynamic acts as a MASK; lit
pixels are the intersection of all three layers that have any active note at
time t. A layer with no held notes defaults to "all pixels lit". Per-pixel
color route follows the most-specific layer covering it (pixel > dynamic >
bar; default primary). Two overlapping color notes in the same palette
crossfade linearly.
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .als_io import TemplateInfo, load_template, save
from .fixtures import RGBStrip, RGBWSpot, Rig
from .fixtures_extended import HITMIX_EXTENDED_RIG
from .renderer import _build_clip, _group_normalize
from .spec import Clip


# --- mapping tables --------------------------------------------------------

SPOT_NOTES: dict[int, tuple[int, str]] = {
    0: (0, "ww"),
    1: (0, "sec"),
    2: (1, "ww"),
    3: (1, "sec"),
}

BAR_SELECTORS: dict[int, tuple[int, ...]] = {
    4: (0, 1, 2, 3),
    5: (0,),
    6: (1,),
    7: (2,),
    8: (3,),
    9: (0, 1),
    10: (2, 3),
    11: (0, 3),
}

# 1-based pixel indices for a 9-pixel bar
PIXEL_STATICS: dict[int, tuple[int, ...]] = {
    12: (1,),
    13: (2, 3),
    14: (4,),
    15: (5, 6),
    16: (7, 8),
    17: (9,),
    18: (1, 2, 3),
    19: (4, 5, 6),
    20: (7, 8, 9),
    21: (1, 9),
    22: (1, 3, 5, 7, 9),
    23: (2, 5, 8),
}

VEL_THRESHOLD = 64.0

SAMPLES_PER_BEAT = 32
"""Time resolution for dynamic-effect sampling. 32/beat = 16ms at 120 BPM —
captures strobe (~8 cycles/beat) cleanly and gives sine/sweep visible smoothness.
Outside dynamic-active intervals we only emit at note transitions."""


# --- dynamics recipes ------------------------------------------------------

def _sparkle_phase(bar_idx: int, pixel_idx: int) -> float:
    """Deterministic per-pixel phase offset in [0, 1)."""
    h = ((bar_idx * 31 + pixel_idx) * 2654435761) & 0xFFFFFFFF
    return h / 0x100000000


def _chase_up(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    return 1.0 if pix == int((t * n_pix) % n_pix) + 1 else 0.0


def _chase_down(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    return 1.0 if pix == n_pix - int((t * n_pix) % n_pix) else 0.0


def _ping_pong(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    phase = t % 2.0
    if phase < 1.0:
        pos = int(phase * n_pix) + 1
    else:
        pos = n_pix - int((phase - 1.0) * n_pix)
    return 1.0 if pix == pos else 0.0


def _snake(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    total = n_bars * n_pix
    pos = int((t / n_bars) * total) % total
    cb, cp = pos // n_pix, (pos % n_pix) + 1
    return 1.0 if (bar == cb and pix == cp) else 0.0


def _sine_wave(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    phase = 2 * math.pi * (t - (pix - 1) / n_pix)
    return (1.0 + math.sin(phase)) / 2.0


def _sparkle(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    rate = 4.0
    val = math.sin(2 * math.pi * (t * rate + _sparkle_phase(bar, pix)))
    return 1.0 if val > 0.6 else 0.0


def _breathe(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    return (1.0 + math.sin(2 * math.pi * t / 4.0)) / 2.0


def _sweep_up(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    fill = int((t % 1.0) * n_pix) + 1
    return 1.0 if pix <= fill else 0.0


def _sweep_down(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    fill = n_pix - int((t % 1.0) * n_pix)
    return 1.0 if pix >= fill else 0.0


def _strobe(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    return 1.0 if int(t * 16) % 2 == 0 else 0.0


def _kick_pulse(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    phase = t % 1.0
    return max(0.0, 1.0 - phase * 4.0) if phase < 0.25 else 0.0


def _alt_swap(t: float, bar: int, pix: int, n_pix: int, n_bars: int) -> float:
    beat_even = int(t) % 2 == 0
    bar_even = bar % 2 == 0
    return 1.0 if beat_even == bar_even else 0.0


DYNAMIC_RECIPES = {
    24: _chase_up,
    25: _chase_down,
    26: _ping_pong,
    27: _snake,
    28: _sine_wave,
    29: _sparkle,
    30: _breathe,
    31: _sweep_up,
    32: _sweep_down,
    33: _strobe,
    34: _kick_pulse,
    35: _alt_swap,
}


PRIMARY_PALETTE_START = 36
SECONDARY_PALETTE_START = 60
BLACKOUT_NOTE = 84

PALETTE: list[tuple[float, float, float]] = [
    (0.000, 0.000, 0.000),  # Black
    (1.000, 0.000, 0.000),  # Red
    (1.000, 0.235, 0.000),  # Orange-red
    (1.000, 0.471, 0.000),  # Orange
    (1.000, 0.706, 0.000),  # Amber
    (1.000, 0.902, 0.000),  # Yellow
    (0.706, 1.000, 0.000),  # Lime
    (0.000, 1.000, 0.000),  # Green
    (0.000, 1.000, 0.471),  # Mint
    (0.000, 0.784, 0.706),  # Teal
    (0.000, 0.863, 1.000),  # Cyan
    (0.000, 0.549, 1.000),  # Sky
    (0.000, 0.000, 1.000),  # Blue
    (0.235, 0.000, 0.902),  # Royal
    (0.392, 0.000, 0.784),  # Indigo
    (0.627, 0.000, 0.863),  # Violet
    (0.745, 0.000, 0.745),  # Purple
    (1.000, 0.000, 0.784),  # Magenta
    (1.000, 0.392, 0.706),  # Pink
    (1.000, 0.157, 0.471),  # Hot pink
    (0.706, 0.000, 0.157),  # Crimson
    (1.000, 0.706, 0.431),  # Warm white (palette)
    (0.863, 0.902, 1.000),  # Cool white
    (0.784, 0.706, 1.000),  # Lavender
]
assert len(PALETTE) == 24


EPS = 1e-9


# --- model -----------------------------------------------------------------

@dataclass(frozen=True)
class NoteEvent:
    pitch: int
    start: float
    end: float
    velocity: float


def read_midi_clip(clip_xml: ET.Element) -> tuple[str, float, list[NoteEvent]]:
    """Parse a <MidiClip> element into (name, length_beats, events)."""
    name_el = clip_xml.find("Name")
    name = name_el.get("Value") if name_el is not None else ""
    length = float(clip_xml.find("Loop/LoopEnd").get("Value"))
    events: list[NoteEvent] = []
    for kt in clip_xml.findall("Notes/KeyTracks/KeyTrack"):
        mk = kt.find("MidiKey")
        if mk is None:
            continue
        pitch = int(mk.get("Value"))
        for ne in kt.findall("Notes/MidiNoteEvent"):
            start = float(ne.get("Time"))
            dur = float(ne.get("Duration"))
            vel = float(ne.get("Velocity"))
            events.append(NoteEvent(pitch=pitch, start=start, end=start + dur, velocity=vel))
    events.sort(key=lambda e: (e.start, e.pitch))
    return name, length, events


def _find_track_by_name(root: ET.Element, name: str) -> ET.Element:
    for track in root.findall("LiveSet/Tracks/MidiTrack"):
        eff = track.find("Name/EffectiveName")
        if eff is not None and eff.get("Value") == name:
            return track
    raise RuntimeError(f"no MidiTrack named {name!r}")


def read_source_clips(root: ET.Element, track_name: str) -> list[tuple[int, ET.Element]]:
    """Return [(slot_index, MidiClip element)] for every populated slot on the named track."""
    track = _find_track_by_name(root, track_name)
    out: list[tuple[int, ET.Element]] = []
    for i, slot in enumerate(track.findall(".//ClipSlotList/ClipSlot")):
        clip = slot.find("ClipSlot/Value/MidiClip")
        if clip is not None:
            out.append((i, clip))
    return out


# --- semantics -------------------------------------------------------------

def _active(events: list[NoteEvent], t: float, *, after: bool) -> list[NoteEvent]:
    """Notes active at t (just-before if after=False, just-after if after=True)."""
    if after:
        return [e for e in events if e.start <= t + EPS and t + EPS < e.end]
    return [e for e in events if e.start < t and t <= e.end]


def _color_state(
    active: list[NoteEvent], palette_start: int, t: float
) -> tuple[tuple[float, float, float], float]:
    """(rgb, intensity 0..1) for given color notes active at t. Linear crossfade
    when 2+ overlap (uses the two most-recent starts)."""
    if not active:
        return (0.0, 0.0, 0.0), 0.0
    if len(active) == 1:
        e = active[0]
        return PALETTE[e.pitch - palette_start], e.velocity / 127.0
    sorted_act = sorted(active, key=lambda e: e.start)
    a, b = sorted_act[-2], sorted_act[-1]
    fade_start = b.start
    fade_end = min(a.end, b.end)
    if fade_end - fade_start < EPS:
        w = 1.0
    else:
        w = max(0.0, min(1.0, (t - fade_start) / (fade_end - fade_start)))
    ra, ga, ba = PALETTE[a.pitch - palette_start]
    rb, gb, bb = PALETTE[b.pitch - palette_start]
    ia = a.velocity / 127.0
    ib = b.velocity / 127.0
    return (
        (
            ra * (1 - w) + rb * w,
            ga * (1 - w) + gb * w,
            ba * (1 - w) + bb * w,
        ),
        ia * (1 - w) + ib * w,
    )


def _compute_state(events: list[NoteEvent], t: float, *, after: bool, rig: Rig) -> dict[int, float]:
    """{dmx_channel: value 0..1} at t."""
    act = _active(events, t, after=after)

    # Blackout: while held, all channels are 0 regardless of anything else.
    if any(e.pitch == BLACKOUT_NOTE for e in act):
        return {ch: 0.0 for ch in range(1, rig.total_channels + 1)}

    primary_notes = [e for e in act if PRIMARY_PALETTE_START <= e.pitch < SECONDARY_PALETTE_START]
    secondary_notes = [e for e in act if SECONDARY_PALETTE_START <= e.pitch < BLACKOUT_NOTE]
    primary_rgb, primary_int = _color_state(primary_notes, PRIMARY_PALETTE_START, t)
    secondary_rgb, secondary_int = _color_state(secondary_notes, SECONDARY_PALETTE_START, t)
    pri = (primary_rgb[0] * primary_int, primary_rgb[1] * primary_int, primary_rgb[2] * primary_int)
    sec = (secondary_rgb[0] * secondary_int, secondary_rgb[1] * secondary_int, secondary_rgb[2] * secondary_int)

    # Categorize active notes by layer
    bar_notes = [e for e in act if e.pitch in BAR_SELECTORS]
    static_notes = [e for e in act if e.pitch in PIXEL_STATICS]
    dynamic_notes = [e for e in act if e.pitch in DYNAMIC_RECIPES]
    spot_notes = [e for e in act if e.pitch in SPOT_NOTES]

    # Bar layer: highest-vel utility note wins the color route for each bar
    bar_route: dict[int, str] = {}
    bar_vel: dict[int, float] = {}
    for e in bar_notes:
        route = "pri" if e.velocity >= VEL_THRESHOLD else "sec"
        for b in BAR_SELECTORS[e.pitch]:
            if b not in bar_vel or e.velocity > bar_vel[b]:
                bar_vel[b] = e.velocity
                bar_route[b] = route

    # Pixel layer: same routing approach
    pixel_route: dict[int, str] = {}
    pixel_vel: dict[int, float] = {}
    for e in static_notes:
        route = "pri" if e.velocity >= VEL_THRESHOLD else "sec"
        for p in PIXEL_STATICS[e.pitch]:
            if p not in pixel_vel or e.velocity > pixel_vel[p]:
                pixel_vel[p] = e.velocity
                pixel_route[p] = route

    # Dynamic layer: separate primary/secondary recipes
    primary_dynamics: list = []
    secondary_dynamics: list = []
    for e in dynamic_notes:
        recipe = DYNAMIC_RECIPES[e.pitch]
        (primary_dynamics if e.velocity >= VEL_THRESHOLD else secondary_dynamics).append(recipe)

    bar_layer_held = bool(bar_notes)
    pixel_layer_held = bool(static_notes)
    dynamic_layer_held = bool(dynamic_notes)

    # Spot layer (independent of bars)
    spot_ww = [False, False]
    spot_sec = [False, False]
    for e in spot_notes:
        spot_idx, src = SPOT_NOTES[e.pitch]
        if src == "ww":
            spot_ww[spot_idx] = True
        else:
            spot_sec[spot_idx] = True

    values: dict[int, float] = {}

    bar_fixtures: list[RGBStrip] = [rig["bar_1"], rig["bar_2"], rig["bar_3"], rig["bar_4"]]  # type: ignore[list-item]
    n_bars = len(bar_fixtures)
    for bar_idx, bar in enumerate(bar_fixtures):
        # Bar layer contribution: default lit when layer not held; otherwise only if covered
        if bar_layer_held:
            bar_b = 1.0 if bar_idx in bar_route else 0.0
            b_route = bar_route.get(bar_idx)
        else:
            bar_b = 1.0
            b_route = None

        for pixel in range(1, bar.pixels + 1):
            if pixel_layer_held:
                pix_b = 1.0 if pixel in pixel_route else 0.0
                p_route = pixel_route.get(pixel)
            else:
                pix_b = 1.0
                p_route = None

            if dynamic_layer_held:
                dyn_pri = 0.0
                for recipe in primary_dynamics:
                    v = recipe(t, bar_idx, pixel, bar.pixels, n_bars)
                    if v > dyn_pri:
                        dyn_pri = v
                dyn_sec = 0.0
                for recipe in secondary_dynamics:
                    v = recipe(t, bar_idx, pixel, bar.pixels, n_bars)
                    if v > dyn_sec:
                        dyn_sec = v
                if dyn_pri >= dyn_sec:
                    dyn_b, d_route = dyn_pri, "pri"
                else:
                    dyn_b, d_route = dyn_sec, "sec"
            else:
                dyn_b = 1.0
                d_route = None

            brightness = bar_b * pix_b * dyn_b
            cr, cg, cb = bar.channels_for(pixel)
            if brightness <= 0:
                values[cr] = 0.0
                values[cg] = 0.0
                values[cb] = 0.0
                continue

            # Color route: most-specific layer wins (pixel > dynamic > bar)
            route = p_route or d_route or b_route or "pri"
            color = pri if route == "pri" else sec
            values[cr] = color[0] * brightness
            values[cg] = color[1] * brightness
            values[cb] = color[2] * brightness

    spot_fixtures: list[RGBWSpot] = [rig["spot_l"], rig["spot_r"]]  # type: ignore[list-item]
    for spot_idx, spot in enumerate(spot_fixtures):
        dim = 0.0
        r = g = b = 0.0
        w = 0.0
        if spot_ww[spot_idx]:
            dim = 1.0
            w = 1.0
            # Tint W with R + a touch of G to push toward incandescent warm white
            r = max(r, 0.4)
            g = max(g, 0.15)
        if spot_sec[spot_idx]:
            dim = 1.0
            r = max(r, sec[0]); g = max(g, sec[1]); b = max(b, sec[2])
        values[spot.dimmer] = dim
        values[spot.red] = r
        values[spot.green] = g
        values[spot.blue] = b
        values[spot.white] = w
        values[spot.strobe] = 0.0

    return values


def translate(
    events: list[NoteEvent], length_beats: float, rig: Rig
) -> dict[int, list[tuple[float, float]]]:
    """Per-channel (time, value) automation events covering [0, length_beats].

    Emits events at every note start/end (the "breakpoints"). At each breakpoint
    we sample the state both just-before and just-after — the renderer's dedupe
    drops the duplicate side when the value doesn't change, and Live's linear
    interpolation between events naturally yields color crossfades during
    constant-mask intervals.
    """
    bps: set[float] = {0.0, length_beats}
    for e in events:
        bps.add(max(0.0, min(length_beats, e.start)))
        bps.add(max(0.0, min(length_beats, e.end)))
        if e.pitch in DYNAMIC_RECIPES:
            step = 1.0 / SAMPLES_PER_BEAT
            tt = e.start
            while tt < e.end:
                bps.add(max(0.0, min(length_beats, tt)))
                tt += step
    breakpoints = sorted(bps)

    by_channel: dict[int, list[tuple[float, float]]] = {}
    for i, t in enumerate(breakpoints):
        before_state = None if i == 0 else _compute_state(events, t, after=False, rig=rig)
        after_state = None if i == len(breakpoints) - 1 else _compute_state(events, t, after=True, rig=rig)
        if before_state is not None:
            for ch, v in before_state.items():
                by_channel.setdefault(ch, []).append((t, v))
        if after_state is not None:
            for ch, v in after_state.items():
                by_channel.setdefault(ch, []).append((t, v))
    return by_channel


# --- top-level orchestration ----------------------------------------------

def render_midi_to_dmx(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    notes_track: str = "DMX_notes",
    plugin_track: str = "DMX_plugin",
    rig: Rig = HITMIX_EXTENDED_RIG,
    color_index: int = 23,
) -> Path:
    """Read MIDI clips from `notes_track`, render DMX automation onto `plugin_track`,
    save to `output_path` (defaults to overwriting input)."""
    src = Path(input_path)
    out = Path(output_path) if output_path is not None else src

    # Find the plugin track's index so we can target it
    template = load_template(src)
    tracks = list(template.root.find("LiveSet/Tracks"))
    plugin_track_idx = next(
        (i for i, tr in enumerate(tracks)
         if tr.tag == "MidiTrack"
         and tr.find("Name/EffectiveName") is not None
         and tr.find("Name/EffectiveName").get("Value") == plugin_track),
        None,
    )
    if plugin_track_idx is None:
        raise RuntimeError(f"no MidiTrack named {plugin_track!r}")

    # If load_template picked a different plugin track, re-load with the right one
    if template.midi_track.find("Name/EffectiveName").get("Value") != plugin_track:
        template = load_template(src, track_index=plugin_track_idx)

    if rig.total_channels > template.plugin.channel_count:
        raise ValueError(
            f"rig requires {rig.total_channels} channels, plugin exposes "
            f"only {template.plugin.channel_count}"
        )

    source_clips = read_source_clips(template.root, notes_track)
    if not source_clips:
        raise RuntimeError(f"no clips found on {notes_track!r} track")

    rendered = 0
    for slot_idx, clip_el in source_clips:
        name, length, events = read_midi_clip(clip_el)
        if not events:
            continue
        by_channel = translate(events, length, rig)
        normalized = _group_normalize(_flatten(by_channel), length)
        if slot_idx >= len(template.clip_slots):
            print(f"  warn: source slot {slot_idx} ({name!r}) out of range on {plugin_track!r}, skipping")
            continue
        clip_spec = Clip(
            name=name,
            slot=slot_idx,
            length_beats=length,
            color_index=color_index,
        )
        clip_xml = _build_clip(template, clip_spec, normalized)
        _replace_slot_clip(template.clip_slots[slot_idx], clip_xml)
        rendered += 1
        print(f"  rendered {name!r} -> slot {slot_idx}: {len(normalized)} channels, {length} beats")

    if not rendered:
        raise RuntimeError("no clips rendered (all empty?)")

    save(
        template,
        out,
        validate_track_indices={plugin_track_idx},
        tolerate=("slot/scene Id mismatch",),
    )
    return out


def _flatten(by_channel: dict[int, list[tuple[float, float]]]):
    """Re-emit per-channel events as a flat (channel, time, value) list for _group_normalize."""
    out = []
    for ch, evs in by_channel.items():
        for t, v in evs:
            out.append((ch, t, v))
    return out


def _replace_slot_clip(slot: ET.Element, new_clip: ET.Element) -> None:
    value = slot.find("ClipSlot/Value")
    if value is None:
        raise RuntimeError(f"slot {slot.get('Id')!r} missing ClipSlot/Value")
    for child in list(value):
        value.remove(child)
    value.append(new_clip)
