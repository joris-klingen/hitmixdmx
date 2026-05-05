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


def _find_dmxis_track(root: ET.Element) -> tuple[ET.Element, ET.Element]:
    """Return (MidiTrack, PluginDevice) for the track containing DMXIS."""
    for track in root.find("LiveSet/Tracks"):
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


def load_template(path: str | Path) -> TemplateInfo:
    p = Path(path)
    with gzip.open(p, "rb") as f:
        xml_bytes = f.read()
    root = ET.fromstring(xml_bytes)
    track, plugin = _find_dmxis_track(root)
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


def save(template: TemplateInfo, path: str | Path) -> None:
    template.commit_next_pointee_id()
    issues = validate(template.root)
    if issues:
        raise ValueError(
            "template failed validation, refusing to save:\n  - "
            + "\n  - ".join(issues)
        )
    xml_bytes = ET.tostring(
        template.root, encoding="utf-8", xml_declaration=True, short_empty_elements=True
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wb") as f:
        f.write(xml_bytes)


def validate(root: ET.Element) -> list[str]:
    """Run hard invariants from build-lighting-clips.md. Returns list of issues."""
    ls = root.find("LiveSet")
    issues: list[str] = []
    scenes = list(ls.find("Scenes"))
    for track in ls.find("Tracks"):
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
