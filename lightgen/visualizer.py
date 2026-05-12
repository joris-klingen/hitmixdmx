"""Snapshot visualizer: render the rig state at a given time as a small image.

The sampling logic matches Live's envelope semantics: linear interpolation
between events on the same channel, with the two-events-at-the-same-time
idiom collapsing to an instant jump (post-jump value wins).
"""

from __future__ import annotations

from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from .fixtures import RGBStrip, RGBWSpot
from .spec import Spec


def collect_channel_events(
    spec: Spec, clip_index: int = 0
) -> dict[int, list[tuple[float, float]]]:
    """Expand the clip's events into per-channel timelines, each sorted by time."""
    rig = spec.resolve_rig()
    clip = spec.clips[clip_index]
    by_channel: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for event in clip.events:
        for ch, t, v in event.expand(rig):
            by_channel[ch].append((t, v))
    for ch in by_channel:
        by_channel[ch].sort(key=lambda x: x[0])
    return dict(by_channel)


def sample_channel(events: list[tuple[float, float]], t: float) -> float:
    """Channel value at time t. 0 before first event; linear interp between events;
    constant after last event; instant-jump idiom (two events at same t) honors the
    post-jump value."""
    if not events:
        return 0.0
    if t < events[0][0]:
        return 0.0
    prev_t, prev_v = events[0]
    for curr_t, curr_v in events[1:]:
        if curr_t > t:
            if prev_t == curr_t:
                return prev_v
            frac = (t - prev_t) / (curr_t - prev_t)
            return prev_v + frac * (curr_v - prev_v)
        prev_t, prev_v = curr_t, curr_v
    return prev_v


def sample_spec_at(
    spec: Spec, t: float, clip_index: int = 0
) -> dict[int, float]:
    """Channel state at time t for the given clip: {dmx_channel: value in [0,1]}."""
    by_channel = collect_channel_events(spec, clip_index)
    return {ch: sample_channel(evs, t) for ch, evs in by_channel.items()}


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def render_snapshot(
    spec: Spec,
    t: float,
    *,
    size: tuple[int, int] = (560, 220),
    clip_index: int = 0,
) -> Image.Image:
    """Render the rig's visible state at time t.

    Layout: 4 equal columns left→right, ordered to match the physical stage
    (left bar | singer left | singer right | right bar). Bars are vertical
    stacks of pixel cells (pixel 1 = bottom). Spots are circles tinted with
    `(R, G, B) * dimmer + W`."""
    w, h = size
    img = Image.new("RGB", (w, h), (18, 18, 18))
    if clip_index >= len(spec.clips):
        return img

    state = sample_spec_at(spec, t, clip_index)
    rig = spec.resolve_rig()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    col_w = w // 4
    pad = 8
    label_h = 16

    for col_idx, name in enumerate(
        ["left_bar", "singer_left", "singer_right", "right_bar"]
    ):
        x0 = col_idx * col_w + pad
        x1 = (col_idx + 1) * col_w - pad
        y0 = pad
        y1 = h - pad - label_h

        f = rig.fixtures.get(name)
        if f is None:
            continue

        if isinstance(f, RGBStrip):
            n = f.pixels
            pixel_h = (y1 - y0) / n
            for p in range(1, n + 1):
                r_ch, g_ch, b_ch = f.channels_for(p)
                r = _clip01(state.get(r_ch, 0.0))
                g = _clip01(state.get(g_ch, 0.0))
                b = _clip01(state.get(b_ch, 0.0))
                color = (int(r * 255), int(g * 255), int(b * 255))
                # pixel 1 = bottom, so invert y
                py_bot = y1 - (p - 1) * pixel_h
                py_top = y1 - p * pixel_h
                draw.rectangle([x0, py_top, x1, py_bot - 1], fill=color)
        elif isinstance(f, RGBWSpot):
            dim = _clip01(state.get(f.dimmer, 0.0))
            r = _clip01(state.get(f.red, 0.0)) * dim
            g = _clip01(state.get(f.green, 0.0)) * dim
            b = _clip01(state.get(f.blue, 0.0)) * dim
            white = _clip01(state.get(f.white, 0.0))
            color = (
                int(min(1.0, r + white) * 255),
                int(min(1.0, g + white) * 255),
                int(min(1.0, b + white) * 255),
            )
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            radius = min(x1 - x0, y1 - y0) / 2 - 4
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=color,
                outline=(70, 70, 70),
            )

        if font is not None:
            draw.text((x0, h - label_h + 2), name, fill=(170, 170, 170), font=font)

    return img


def render_strip(
    spec: Spec,
    times: list[float],
    *,
    snapshot_size: tuple[int, int] = (560, 220),
    clip_index: int = 0,
) -> list[tuple[Image.Image, str]]:
    """Render one (image, label) tuple per requested time."""
    out: list[tuple[Image.Image, str]] = []
    for t in times:
        img = render_snapshot(spec, t, size=snapshot_size, clip_index=clip_index)
        out.append((img, f"t = {t:.2f}"))
    return out
