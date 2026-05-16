"""Load, introspect, validate, and save Ableton Live (.als) project files.

An .als is gzipped XML. This module wraps the I/O and exposes a `TemplateInfo`
that captures the bits of the template the renderer needs: the DMXIS plugin's
channel-to-AutomationTarget mapping, the clip-slot list of the DMXIS track,
a clone-source clip, and the next free Id for new XML elements.

Constraints enforced by `validate`:
  - times must be monotone non-decreasing
  - no event time may exceed the clip's LoopEnd
  - all FloatEvent values must be in [0, 1]
  - every envelope's PointeeId must reference a configured plugin parameter
  - position-matched ClipSlot/Scene Id alignment
"""

from __future__ import annotations

import copy
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginInfo:
    """Channel <-> AutomationTarget Id mapping for a sequentially configured DMXIS instance."""

    base_at_id: int
    stride: int
    channel_count: int
    configured_at_ids: frozenset[int]

    def at_id(self, channel: int) -> int:
        if not 1 <= channel <= self.channel_count:
            raise ValueError(
                f"channel {channel} out of range [1, {self.channel_count}]"
            )
        return self.base_at_id + self.stride * (channel - 1)


@dataclass
class TemplateInfo:
    root: ET.Element
    plugin: PluginInfo
    midi_track: ET.Element
    clip_slots: list[ET.Element]
    clone_source: ET.Element
    scene_count: int
    next_pointee_id: int = field(repr=False)

    def new_id(self) -> int:
        v = self.next_pointee_id
        self.next_pointee_id += 1
        return v

    def commit_next_pointee_id(self) -> None:
        self.root.find("LiveSet/NextPointeeId").set("Value", str(self.next_pointee_id))


def _find_dmxis_track(
    root: ET.Element, track_index: int | None = None
) -> tuple[ET.Element, ET.Element]:
    """Return (MidiTrack, PluginDevice).

    If `track_index` is given, target that specific track in LiveSet/Tracks
    (used when a file has multiple plugin-bearing tracks and you need to pick).
    Otherwise return the first MidiTrack that has any PluginDevice.
    """
    tracks = list(root.find("LiveSet/Tracks"))
    if track_index is not None:
        track = tracks[track_index]
        if track.tag != "MidiTrack":
            raise RuntimeError(
                f"track {track_index} is {track.tag}, not a MidiTrack"
            )
        plugin = track.find(".//PluginDevice")
        if plugin is None:
            raise RuntimeError(f"track {track_index} has no PluginDevice")
        return track, plugin
    for track in tracks:
        if track.tag != "MidiTrack":
            continue
        plugin = track.find(".//PluginDevice")
        if plugin is not None:
            return track, plugin
    raise RuntimeError("no MidiTrack with a PluginDevice found in template")


def _introspect_plugin(plugin: ET.Element) -> PluginInfo:
    params = plugin.findall(".//PluginFloatParameter")
    configured = [p for p in params if p.find("ParameterId").get("Value") != "-1"]
    if not configured:
        raise RuntimeError("DMXIS plugin has no configured parameters")
    at_ids = [
        int(p.find("ParameterValue/AutomationTarget").get("Id")) for p in configured
    ]
    base = at_ids[0]
    if len(at_ids) >= 2:
        stride = at_ids[1] - at_ids[0]
    else:
        stride = 2
    expected = [base + stride * i for i in range(len(at_ids))]
    if at_ids != expected:
        raise RuntimeError(
            f"plugin parameters are not sequentially numbered: "
            f"got {at_ids[:5]}..., expected base={base} stride={stride}"
        )
    return PluginInfo(
        base_at_id=base,
        stride=stride,
        channel_count=len(configured),
        configured_at_ids=frozenset(at_ids),
    )


def _find_clone_source(track: ET.Element) -> ET.Element:
    clips = track.findall(".//ClipSlotList/ClipSlot/ClipSlot/Value/MidiClip")
    if not clips:
        raise RuntimeError(
            "template contains no MidiClip to clone — author at least one "
            "clip in the DMXIS track before rendering"
        )
    return clips[0]


def load_template(path: str | Path, *, track_index: int | None = None) -> TemplateInfo:
    p = Path(path)
    with gzip.open(p, "rb") as f:
        xml_bytes = f.read()
    root = ET.fromstring(xml_bytes)
    track, plugin = _find_dmxis_track(root, track_index=track_index)
    info = _introspect_plugin(plugin)
    slots = track.findall(".//ClipSlotList/ClipSlot")
    scenes = root.findall("LiveSet/Scenes/Scene")
    next_id = int(root.find("LiveSet/NextPointeeId").get("Value"))
    clone = _find_clone_source(track)
    return TemplateInfo(
        root=root,
        plugin=info,
        midi_track=track,
        clip_slots=slots,
        clone_source=copy.deepcopy(clone),
        scene_count=len(scenes),
        next_pointee_id=next_id,
    )


def save(
    template: TemplateInfo,
    path: str | Path,
    *,
    tolerate: tuple[str, ...] = (),
    validate_track_indices: set[int] | None = None,
) -> None:
    """Validate and write the template to `path`.

    `tolerate` is a list of substrings; any issue whose message contains one of
    them is logged but doesn't block the save. Use for issues that pre-exist in
    a third-party `.als` and that Live demonstrably tolerates (e.g. slot/scene
    Id mismatches when rendering into someone else's working set).

    `validate_track_indices` limits validation to specific track indices. Use
    when rendering into a multi-track destination so we don't flag pre-existing
    state on tracks we didn't touch.
    """
    template.commit_next_pointee_id()
    issues = validate(template.root, track_indices=validate_track_indices)
    fatal = [i for i in issues if not any(t in i for t in tolerate)]
    tolerated = [i for i in issues if i not in fatal]
    if fatal:
        raise ValueError(
            "template failed validation, refusing to save:\n  - "
            + "\n  - ".join(fatal)
        )
    if tolerated:
        print(f"warning: tolerating {len(tolerated)} pre-existing issue(s) "
              f"matching {list(tolerate)}")
    xml_bytes = ET.tostring(
        template.root, encoding="utf-8", xml_declaration=True, short_empty_elements=True
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wb") as f:
        f.write(xml_bytes)


def validate(
    root: ET.Element, *, track_indices: set[int] | None = None
) -> list[str]:
    """Run hard invariants from build-lighting-clips.md. Returns list of issues.

    If `track_indices` is given, only check those tracks (by index into
    LiveSet/Tracks). Use this when rendering into a multi-track destination
    where other tracks have their own pre-existing envelope conventions
    (e.g. rack-macro routing or 0..127 value ranges) that this validator
    doesn't model.
    """
    ls = root.find("LiveSet")
    issues: list[str] = []
    scenes = list(ls.find("Scenes"))
    for ti, track in enumerate(ls.find("Tracks")):
        if track_indices is not None and ti not in track_indices:
            continue
        if track.tag not in ("AudioTrack", "MidiTrack"):
            continue
        slots = track.findall(".//ClipSlotList/ClipSlot")
        for i in range(min(len(scenes), len(slots))):
            if slots[i].get("Id") != scenes[i].get("Id"):
                issues.append(
                    f"{track.tag}: slot/scene Id mismatch at position {i} "
                    f"(slot={slots[i].get('Id')} scene={scenes[i].get('Id')})"
                )
        if track.tag != "MidiTrack":
            continue
        plugin = track.find(".//PluginDevice")
        if plugin is None:
            continue
        configured = {
            p.find("ParameterValue/AutomationTarget").get("Id")
            for p in plugin.findall(".//PluginFloatParameter")
            if p.find("ParameterId").get("Value") != "-1"
        }
        for i, s in enumerate(slots):
            clip = s.find("ClipSlot/Value/MidiClip")
            if clip is None:
                continue
            clip_end = float(clip.find("Loop/LoopEnd").get("Value"))
            for env in clip.findall(".//Envelopes/Envelopes/ClipEnvelope"):
                pid = env.find("EnvelopeTarget/PointeeId").get("Value")
                if pid not in configured:
                    issues.append(
                        f"slot {i}: envelope targets unconfigured parameter {pid}"
                    )
                evs = env.findall("Automation/Events/FloatEvent")
                ts = [float(e.get("Time")) for e in evs]
                for j in range(1, len(ts)):
                    if ts[j] < ts[j - 1]:
                        issues.append(
                            f"slot {i}: non-monotone time {ts[j-1]} -> {ts[j]} "
                            f"(target={pid})"
                        )
                        break
                for t in ts:
                    if t > clip_end + 1e-9 and t > 0:
                        issues.append(
                            f"slot {i}: event past clip end "
                            f"(t={t}, end={clip_end}, target={pid})"
                        )
                        break
                for e in evs:
                    v = float(e.get("Value"))
                    if not 0.0 <= v <= 1.0:
                        issues.append(
                            f"slot {i}: value {v} out of range (target={pid})"
                        )
                        break
    return issues
