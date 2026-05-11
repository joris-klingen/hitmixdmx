"""Channel-level event primitives.

A `ChannelEvent` is `(channel, time, value)` where channel is a 1-based DMX
channel number, time is in beats, value is in [0, 1].

These primitives know nothing about fixtures or pixels — they operate on raw
channel numbers. Higher-level pattern types in `spec.py` translate fixture/pixel
addresses into channel lists and then call these primitives.

Time encoding for instant value jumps follows Live's convention: two events at
the same time, the first carrying the pre-jump value, the second the post-jump
value. See build-lighting-clips.md "Instant value jumps".
"""

from __future__ import annotations

import math
from typing import Iterable

ChannelEvent = tuple[int, float, float]
Color = tuple[float, float, float]


def step(channels: Iterable[int], time: float, value: float) -> list[ChannelEvent]:
    """Instantaneous jump to `value` on each channel at `time`.

    Encodes the Live "two events at the same t" idiom: a 0.0 establishes the
    pre-jump state, then `value` is the post-jump. The renderer dedupes
    when this is composed with adjacent events.
    """
    return [(c, t, v) for c in channels for (t, v) in ((time, 0.0), (time, value))]


def stab(
    channels: Iterable[int], time: float, duration: float, value: float = 1.0
) -> list[ChannelEvent]:
    """On at `time`, off at `time + duration`. The classic kick/strobe primitive."""
    if duration <= 0:
        raise ValueError(f"stab duration must be > 0, got {duration}")
    out: list[ChannelEvent] = []
    end = time + duration
    for c in channels:
        out.extend(
            [(c, time, 0.0), (c, time, value), (c, end, value), (c, end, 0.0)]
        )
    return out


def hold(
    channels: Iterable[int], t_start: float, t_end: float, value: float
) -> list[ChannelEvent]:
    """Sustain `value` from t_start to t_end. Use for static color blocks."""
    if t_end < t_start:
        raise ValueError(f"hold: t_end ({t_end}) < t_start ({t_start})")
    out: list[ChannelEvent] = []
    for c in channels:
        out.extend(
            [(c, t_start, 0.0), (c, t_start, value), (c, t_end, value), (c, t_end, 0.0)]
        )
    return out


def fade(
    channels: Iterable[int],
    t_start: float,
    t_end: float,
    v_start: float,
    v_end: float,
) -> list[ChannelEvent]:
    """Linear interpolation from v_start to v_end. Live interpolates between unequal values."""
    if t_end <= t_start:
        raise ValueError(f"fade: t_end ({t_end}) must be > t_start ({t_start})")
    return [(c, t, v) for c in channels for (t, v) in ((t_start, v_start), (t_end, v_end))]


def breathe(
    channels: Iterable[int],
    t_start: float,
    t_end: float,
    v_min: float,
    v_max: float,
    cycles: int = 1,
    samples_per_cycle: int = 8,
) -> list[ChannelEvent]:
    """Sinusoidal modulation. Sample-and-line-segment approximation.

    Starts at v_min, peaks at v_max in the middle of each cycle, returns to v_min.
    """
    if t_end <= t_start:
        raise ValueError(f"breathe: t_end ({t_end}) must be > t_start ({t_start})")
    if cycles < 1 or samples_per_cycle < 4:
        raise ValueError("breathe: need cycles >= 1 and samples_per_cycle >= 4")
    total_samples = cycles * samples_per_cycle
    duration = t_end - t_start
    out: list[ChannelEvent] = []
    for c in channels:
        for i in range(total_samples + 1):
            phase = (i / samples_per_cycle) * 2 * math.pi
            # 0 at phase=0, peak at phase=pi, back to 0 at phase=2pi
            unit = (1 - math.cos(phase)) / 2
            value = v_min + (v_max - v_min) * unit
            t = t_start + duration * (i / total_samples)
            out.append((c, t, value))
    return out


def color_step(
    rgb: tuple[int, int, int], time: float, color: Color
) -> list[ChannelEvent]:
    """Instantaneous color change on an (R, G, B) channel triple."""
    r, g, b = rgb
    cr, cg, cb = color
    return step([r], time, cr) + step([g], time, cg) + step([b], time, cb)


def color_stab(
    rgb: tuple[int, int, int], time: float, duration: float, color: Color
) -> list[ChannelEvent]:
    """Stab a color on an (R, G, B) channel triple."""
    r, g, b = rgb
    cr, cg, cb = color
    return (
        stab([r], time, duration, cr)
        + stab([g], time, duration, cg)
        + stab([b], time, duration, cb)
    )


def color_hold(
    rgb: tuple[int, int, int], t_start: float, t_end: float, color: Color
) -> list[ChannelEvent]:
    """Hold a color on an (R, G, B) channel triple from t_start to t_end."""
    r, g, b = rgb
    cr, cg, cb = color
    return (
        hold([r], t_start, t_end, cr)
        + hold([g], t_start, t_end, cg)
        + hold([b], t_start, t_end, cb)
    )


def color_fade(
    rgb: tuple[int, int, int],
    t_start: float,
    t_end: float,
    color_start: Color,
    color_end: Color,
) -> list[ChannelEvent]:
    """Linear color crossfade on an (R, G, B) channel triple."""
    r, g, b = rgb
    sr, sg, sb = color_start
    er, eg, eb = color_end
    return (
        fade([r], t_start, t_end, sr, er)
        + fade([g], t_start, t_end, sg, eg)
        + fade([b], t_start, t_end, sb, eb)
    )


def hsv_to_rgb(h: float, s: float, v: float) -> Color:
    """Convert HSV to RGB. h, s, v all in [0, 1]."""
    h = h % 1.0
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i = i % 6
    if i == 0:
        return v, t, p
    if i == 1:
        return q, v, p
    if i == 2:
        return p, v, t
    if i == 3:
        return p, q, v
    if i == 4:
        return t, p, v
    return v, p, q
