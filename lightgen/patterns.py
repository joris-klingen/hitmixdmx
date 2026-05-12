"""Parametric pattern recipes — deterministic alternative to the LLM.

Each recipe returns a `list[Event]` that can be dropped into a `Clip`. Recipes
compose by stacking event lists on the same clip; the renderer overlays
per-channel correctly.

All recipes accept an `active_beats` list to gate the effect to specific beats
of the clip. Beat numbers are 1-based and beat N spans time `[N-1, N)`. An
empty/None list means the full `length_beats` range. Non-contiguous beats split
sustained patterns into multiple events, one per contiguous run.
"""

from __future__ import annotations

from typing import Sequence

from .fixtures import HITMIX_RIG, RGBStrip, Rig
from .spec import (
    Breathe,
    Chase,
    ColorHold,
    ColorStab,
    Event,
    Sparkle,
    ValueHold,
)


Color = tuple[float, float, float]


def parse_beats(text: str) -> list[int] | None:
    """Parse "1,3-5, 7" into [1, 3, 4, 5, 7]. Empty/whitespace → None (all beats)."""
    s = text.strip()
    if not s:
        return None
    out: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            if lo > hi:
                lo, hi = hi, lo
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return sorted(out)


def active_windows(
    active_beats: Sequence[int] | None, length_beats: float
) -> list[tuple[float, float]]:
    """Convert a 1-based beat list into contiguous (t_start, t_end) windows.

    Beat N occupies time [N-1, N). Adjacent beats merge into one window.
    Windows are clipped to [0, length_beats]. Empty/None → one full window.
    """
    if not active_beats:
        return [(0.0, float(length_beats))]
    sorted_beats = sorted({b for b in active_beats if b >= 1 and b <= length_beats})
    if not sorted_beats:
        return []
    windows: list[tuple[float, float]] = []
    run_start = sorted_beats[0] - 1
    run_end = sorted_beats[0]
    for b in sorted_beats[1:]:
        if b == run_end + 1:
            run_end = b
        else:
            windows.append((float(run_start), float(run_end)))
            run_start = b - 1
            run_end = b
    windows.append((float(run_start), float(run_end)))
    return windows


def four_on_floor(
    fixtures: str,
    color: Color,
    length_beats: float,
    *,
    active_beats: Sequence[int] | None = None,
    stab_duration: float = 0.25,
) -> list[Event]:
    """A stab on every active beat. Includes a `dimmer` hold so RGBW spots light up."""
    if active_beats is None:
        active_beats = list(range(1, int(length_beats) + 1))
    events: list[Event] = []
    for b in active_beats:
        if b < 1 or b > length_beats:
            continue
        events.append(
            ColorStab(
                type="color_stab",
                fixture=fixtures,
                time=float(b - 1),
                duration=stab_duration,
                color=tuple(color),
            )
        )
    for t_start, t_end in active_windows(active_beats, length_beats):
        events.append(
            ValueHold(
                type="value_hold",
                fixture=fixtures,
                component="dimmer",
                t_start=t_start,
                t_end=t_end,
                value=1.0,
            )
        )
    return events


def breathing(
    fixtures: str,
    color: Color,
    length_beats: float,
    *,
    active_beats: Sequence[int] | None = None,
    cycles: int = 1,
    v_min: float = 0.05,
    v_max: float = 1.0,
) -> list[Event]:
    """Sinusoidal modulation of an RGB color. One breathe event per active run."""
    events: list[Event] = []
    for t_start, t_end in active_windows(active_beats, length_beats):
        events.append(
            Breathe(
                type="breathe",
                fixture=fixtures,
                component="rgb",
                t_start=t_start,
                t_end=t_end,
                v_min=v_min,
                v_max=v_max,
                cycles=cycles,
                color=tuple(color),
            )
        )
        events.append(
            ValueHold(
                type="value_hold",
                fixture=fixtures,
                component="dimmer",
                t_start=t_start,
                t_end=t_end,
                value=1.0,
            )
        )
    return events


def wash(
    fixtures: str,
    color: Color,
    length_beats: float,
    *,
    active_beats: Sequence[int] | None = None,
) -> list[Event]:
    """Sustain a static color. One color_hold event per active run."""
    events: list[Event] = []
    for t_start, t_end in active_windows(active_beats, length_beats):
        events.append(
            ColorHold(
                type="color_hold",
                fixture=fixtures,
                t_start=t_start,
                t_end=t_end,
                color=tuple(color),
            )
        )
        events.append(
            ValueHold(
                type="value_hold",
                fixture=fixtures,
                component="dimmer",
                t_start=t_start,
                t_end=t_end,
                value=1.0,
            )
        )
    return events


def chase(
    fixtures: str,
    color: Color,
    length_beats: float,
    *,
    active_beats: Sequence[int] | None = None,
    step: float = 0.25,
    duration: float = 0.25,
    period: float = 1.0,
    reverse: bool = False,
    rig: Rig = HITMIX_RIG,
) -> list[Event]:
    """Sweep of stabs across strip pixels. Expands a group selector to one chase per strip."""
    targets = rig.resolve(fixtures)
    strip_names = [f.name for f in targets if isinstance(f, RGBStrip)]
    events: list[Event] = []
    for name in strip_names:
        for t_start, t_end in active_windows(active_beats, length_beats):
            events.append(
                Chase(
                    type="chase",
                    fixture=name,
                    t_start=t_start,
                    step=step,
                    duration=duration,
                    color=tuple(color),
                    reverse=reverse,
                    period=period,
                    t_end=t_end,
                )
            )
    return events


def sparkle(
    fixtures: str,
    color: Color,
    length_beats: float,
    *,
    active_beats: Sequence[int] | None = None,
    density: float = 4.0,
    duration: float = 0.1,
    seed: int = 0,
    rig: Rig = HITMIX_RIG,
) -> list[Event]:
    """Seeded random stabs across strip pixels. Expands a group to one sparkle per strip."""
    targets = rig.resolve(fixtures)
    strip_names = [f.name for f in targets if isinstance(f, RGBStrip)]
    events: list[Event] = []
    for i, name in enumerate(strip_names):
        for t_start, t_end in active_windows(active_beats, length_beats):
            events.append(
                Sparkle(
                    type="sparkle",
                    fixture=name,
                    t_start=t_start,
                    t_end=t_end,
                    density=density,
                    duration=duration,
                    color=tuple(color),
                    seed=seed + i,
                )
            )
    return events


PATTERNS = {
    "four_on_floor": four_on_floor,
    "breathing": breathing,
    "wash": wash,
    "chase": chase,
    "sparkle": sparkle,
}
"""Registry of pattern recipes, keyed by name (matches the UI dropdown)."""
