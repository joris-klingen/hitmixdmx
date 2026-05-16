"""Render a Spec into an .als file by mutating a TemplateInfo in place.

Pipeline:
  1. For each clip in the spec, expand events to (channel, time, value) tuples.
  2. Group by channel; sort, clamp, dedupe adjacent duplicates, terminate at clip_len.
  3. Deepcopy the template's clone-source MidiClip; rewire its name, length,
     loop bounds, color, Id; wipe its envelopes; install fresh envelopes built
     from the per-channel events.
  4. Insert each built clip into its target ClipSlot, replacing any prior clip.
  5. The caller saves via als_io.save (which runs the validator).
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from .als_io import TemplateInfo
from .events import ChannelEvent
from .spec import Clip, Spec


def render(spec: Spec, template: TemplateInfo, clean: bool = False) -> None:
    rig = spec.resolve_rig()
    if rig.total_channels > template.plugin.channel_count:
        raise ValueError(
            f"rig requires {rig.total_channels} channels but template only "
            f"exposes {template.plugin.channel_count}. Configure more channels in DMXIS."
        )
    used_slots: set[int] = set()
    for clip_spec in spec.clips:
        if clip_spec.slot >= len(template.clip_slots):
            raise ValueError(
                f"clip {clip_spec.name!r}: slot {clip_spec.slot} out of range "
                f"[0, {len(template.clip_slots) - 1}]"
            )
        events = _expand_clip(clip_spec, rig)
        by_channel = _group_normalize(events, clip_spec.length_beats)
        clip_xml = _build_clip(template, clip_spec, by_channel)
        _replace_slot_clip(template.clip_slots[clip_spec.slot], clip_xml)
        used_slots.add(clip_spec.slot)
    if clean:
        for i, slot in enumerate(template.clip_slots):
            if i in used_slots:
                continue
            value = slot.find("ClipSlot/Value")
            if value is None:
                continue
            for child in list(value):
                value.remove(child)


def _expand_clip(clip: Clip, rig) -> list[ChannelEvent]:
    out: list[ChannelEvent] = []
    for ev in clip.events:
        out.extend(ev.expand(rig))
    return out


SIMPLIFY_TOLERANCE = 0.01
"""Per-event simplification tolerance in [0,1] space (~2.5 DMX values out of 255).
Drop a middle point if it deviates from the linear interpolation of its
neighbours by less than this. Invisible for lighting, slashes file size."""


def _simplify_collinear(
    events: list[tuple[float, float]], tolerance: float = SIMPLIFY_TOLERANCE
) -> list[tuple[float, float]]:
    """Drop middle events that lie on (or very near) the line between their
    surviving neighbours. Single forward pass. Preserves instant-jump idiom
    (events sharing a time stamp with a neighbour are never dropped)."""
    if len(events) < 3:
        return events
    kept: list[tuple[float, float]] = [events[0]]
    for i in range(1, len(events) - 1):
        t_prev, v_prev = kept[-1]
        t_curr, v_curr = events[i]
        t_next, v_next = events[i + 1]
        if t_curr == t_prev or t_curr == t_next:
            kept.append(events[i])
            continue
        frac = (t_curr - t_prev) / (t_next - t_prev)
        v_interp = v_prev + frac * (v_next - v_prev)
        if abs(v_curr - v_interp) > tolerance:
            kept.append(events[i])
    kept.append(events[-1])
    return kept


def _group_normalize(
    events: list[ChannelEvent], clip_len: float
) -> dict[int, list[tuple[float, float]]]:
    """Group events by channel, then per-channel: sort, clamp, dedupe, terminate,
    simplify collinear runs."""
    by_ch: dict[int, list[tuple[float, float]]] = {}
    for ch, t, v in events:
        by_ch.setdefault(ch, []).append((t, v))

    for ch, evs in by_ch.items():
        evs_sorted = sorted(evs, key=lambda x: x[0])
        evs_clamped = [
            (max(0.0, min(t, clip_len)), max(0.0, min(1.0, v)))
            for t, v in evs_sorted
        ]
        evs_dedup: list[tuple[float, float]] = []
        for ev in evs_clamped:
            if not evs_dedup or evs_dedup[-1] != ev:
                evs_dedup.append(ev)
        if evs_dedup:
            last_t, last_v = evs_dedup[-1]
            if last_t < clip_len:
                evs_dedup.append((clip_len, last_v))
        by_ch[ch] = _simplify_collinear(evs_dedup)
    return by_ch


def _build_clip(
    template: TemplateInfo,
    clip_spec: Clip,
    by_channel: dict[int, list[tuple[float, float]]],
) -> ET.Element:
    new_clip = copy.deepcopy(template.clone_source)
    new_clip.set("Id", str(template.new_id()))
    _set_value(new_clip, "Name", clip_spec.name)
    _set_value(new_clip, "Color", clip_spec.color_index)
    _set_value(new_clip, "CurrentStart", 0)
    _set_value(new_clip, "CurrentEnd", clip_spec.length_beats)

    loop = new_clip.find("Loop")
    _set_value(loop, "LoopStart", 0)
    _set_value(loop, "LoopEnd", clip_spec.length_beats)
    _set_value(loop, "OutMarker", clip_spec.length_beats)
    _set_value(loop, "HiddenLoopStart", 0)
    _set_value(loop, "HiddenLoopEnd", clip_spec.length_beats)

    envs_inner = new_clip.find("Envelopes/Envelopes")
    for ce in list(envs_inner):
        envs_inner.remove(ce)

    for channel in sorted(by_channel):
        events = by_channel[channel]
        if not events:
            continue
        at_id = template.plugin.at_id(channel)
        ce = _build_envelope(template, at_id, events, clip_spec.length_beats)
        envs_inner.append(ce)

    return new_clip


def _build_envelope(
    template: TemplateInfo,
    at_id: int,
    events: list[tuple[float, float]],
    clip_len: float,
) -> ET.Element:
    """Produce a <ClipEnvelope> targeting the given AutomationTarget Id.

    The two prelude events (t=-63072000 with Id=0, and t=-1) are required by
    Live; without them the clip won't load. See build-lighting-clips.md.
    """
    ce = ET.Element("ClipEnvelope", Id=str(template.new_id()))
    target = ET.SubElement(ce, "EnvelopeTarget")
    ET.SubElement(target, "PointeeId", Value=str(at_id))
    automation = ET.SubElement(ce, "Automation")
    ev_root = ET.SubElement(automation, "Events")

    initial_v = events[0][1]
    ET.SubElement(ev_root, "FloatEvent", Id="0", Time="-63072000", Value=_fmt(initial_v))
    ET.SubElement(
        ev_root,
        "FloatEvent",
        Id=str(template.new_id()),
        Time="-1",
        Value=_fmt(initial_v),
    )
    for t, v in events:
        ET.SubElement(
            ev_root,
            "FloatEvent",
            Id=str(template.new_id()),
            Time=_fmt(t),
            Value=_fmt(v),
        )

    transform = ET.SubElement(automation, "AutomationTransformViewState")
    ET.SubElement(transform, "IsTransformPending", Value="false")
    ET.SubElement(transform, "TimeAndValueTransforms")
    return ce


def _replace_slot_clip(slot: ET.Element, new_clip: ET.Element) -> None:
    """Remove any existing MidiClip in slot, install new_clip in its place."""
    value = slot.find("ClipSlot/Value")
    if value is None:
        raise RuntimeError(f"ClipSlot id={slot.get('Id')} missing inner ClipSlot/Value")
    for child in list(value):
        value.remove(child)
    value.append(new_clip)


def _set_value(parent: ET.Element, child_tag: str, value) -> None:
    el = parent.find(child_tag)
    if el is None:
        raise RuntimeError(f"expected child <{child_tag}> under <{parent.tag}>")
    el.set("Value", _fmt(value) if isinstance(value, float) else str(value))


def _fmt(v: float | int) -> str:
    """Format a number the way Live's XML expects (no trailing E-notation, no junk)."""
    if isinstance(v, int):
        return str(v)
    if v == int(v):
        return str(int(v))
    return f"{v:g}"
