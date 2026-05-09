"""JSON spec format for lightgen, validated via pydantic.

A spec describes one or more clips to drop into ClipSlots of a template.als.

Top-level shape:

    {
      "version": 1,
      "rig": "hitmix",                 // optional, defaults to "hitmix"
      "clips": [ ... ]
    }

A clip:

    {
      "name": "four-on-floor red",
      "slot": 0,                       // 0-based index into the DMXIS track's clip slots
      "length_beats": 4,
      "color_index": 1,                // optional, Live's clip-color palette 0..69
      "events": [ ... ]
    }

Event types (each gets its own model, dispatched by `type`):

  - color_stab:    flash an RGB color
  - color_hold:    sustain an RGB color
  - gradient_hold: gradient across a strip's pixels
  - value_stab:    flash a numeric value on a non-color channel (dimmer, white, strobe)
  - value_hold:    sustain a numeric value
  - breathe:       sinusoidal modulation of an RGB color or numeric value

See `examples/` for hand-authored specs.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .events import (
    Color,
    ChannelEvent,
    breathe,
    color_hold,
    color_stab,
    hold,
    hsv_to_rgb,
    stab,
)
from .fixtures import HITMIX_RIG, Fixture, RGBStrip, RGBWSpot, Rig


PixelSelector = Union[int, Literal["*"]]
"""For RGB strips: a 1-based pixel index, or '*' for all pixels."""

SpotComponent = Literal["dimmer", "red", "green", "blue", "white", "strobe"]
"""Single-channel selector for an RGBW spot."""

FixtureSelector = Union[str, Literal["*"]]
"""A fixture name, or '*' for all fixtures."""


def _resolve_fixtures(rig: Rig, sel: FixtureSelector) -> list[Fixture]:
    return rig.resolve(sel)


def _strip_pixel_channels(strip: RGBStrip, pixel: PixelSelector) -> list[tuple[int, int, int]]:
    if pixel == "*":
        return [strip.channels_for(p) for p in range(1, strip.pixels + 1)]
    return [strip.channels_for(pixel)]


def _spot_channel(spot: RGBWSpot, component: SpotComponent) -> int:
    return getattr(spot, component)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def expand(self, rig: Rig) -> list[ChannelEvent]:  # pragma: no cover - abstract
        raise NotImplementedError


class ColorStab(_Base):
    type: Literal["color_stab"]
    fixture: FixtureSelector
    pixel: PixelSelector = "*"
    time: float
    duration: float
    color: Color

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if isinstance(f, RGBStrip):
                for rgb in _strip_pixel_channels(f, self.pixel):
                    out += color_stab(rgb, self.time, self.duration, self.color)
            elif isinstance(f, RGBWSpot):
                out += color_stab(f.rgb(), self.time, self.duration, self.color)
        return out


class ColorHold(_Base):
    type: Literal["color_hold"]
    fixture: FixtureSelector
    pixel: PixelSelector = "*"
    t_start: float
    t_end: float
    color: Color

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if isinstance(f, RGBStrip):
                for rgb in _strip_pixel_channels(f, self.pixel):
                    out += color_hold(rgb, self.t_start, self.t_end, self.color)
            elif isinstance(f, RGBWSpot):
                out += color_hold(f.rgb(), self.t_start, self.t_end, self.color)
        return out


class GradientHold(_Base):
    """Static color gradient across a strip's pixels.

    Computes per-pixel color via HSV interpolation between `hue_start` and
    `hue_end`. Uses HSV because lighting designers think in hue, and a
    pure-RGB lerp produces muddy mid-tones.
    """

    type: Literal["gradient_hold"]
    fixture: str
    t_start: float
    t_end: float
    hue_start: float
    hue_end: float
    saturation: float = 1.0
    value: float = 1.0

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        f = rig[self.fixture]
        if not isinstance(f, RGBStrip):
            raise ValueError(
                f"gradient_hold target {self.fixture!r} must be an RGB strip "
                f"(got {type(f).__name__})"
            )
        out: list[ChannelEvent] = []
        n = f.pixels
        for p in range(1, n + 1):
            t = (p - 1) / max(n - 1, 1)
            hue = self.hue_start + (self.hue_end - self.hue_start) * t
            color = hsv_to_rgb(hue, self.saturation, self.value)
            out += color_hold(f.channels_for(p), self.t_start, self.t_end, color)
        return out


class ValueStab(_Base):
    """Stab a single numeric value on a spot's named channel."""

    type: Literal["value_stab"]
    fixture: FixtureSelector
    component: SpotComponent
    time: float
    duration: float
    value: float = 1.0

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if not isinstance(f, RGBWSpot):
                continue  # silently skip non-spot fixtures for "*" selector
            ch = _spot_channel(f, self.component)
            out += stab([ch], self.time, self.duration, self.value)
        return out


class ValueHold(_Base):
    type: Literal["value_hold"]
    fixture: FixtureSelector
    component: SpotComponent
    t_start: float
    t_end: float
    value: float

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if not isinstance(f, RGBWSpot):
                continue
            ch = _spot_channel(f, self.component)
            out += hold([ch], self.t_start, self.t_end, self.value)
        return out


class Breathe(_Base):
    """Sinusoidal modulation of a numeric value on a fixture component or RGB triple."""

    type: Literal["breathe"]
    fixture: FixtureSelector
    component: SpotComponent | Literal["rgb"] = "rgb"
    pixel: PixelSelector = "*"
    t_start: float
    t_end: float
    v_min: float = 0.0
    v_max: float = 1.0
    cycles: int = 1
    samples_per_cycle: int = 8
    color: Color | None = None
    """If set and component=='rgb', modulate this color's RGB; else modulate the channel directly."""

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if self.component == "rgb":
                rgb_triples: list[tuple[int, int, int]]
                if isinstance(f, RGBStrip):
                    rgb_triples = _strip_pixel_channels(f, self.pixel)
                elif isinstance(f, RGBWSpot):
                    rgb_triples = [f.rgb()]
                else:
                    continue
                color = self.color if self.color is not None else (1.0, 1.0, 1.0)
                for rgb in rgb_triples:
                    for ch, base in zip(rgb, color):
                        out += breathe(
                            [ch],
                            self.t_start,
                            self.t_end,
                            self.v_min * base,
                            self.v_max * base,
                            self.cycles,
                            self.samples_per_cycle,
                        )
            else:
                if not isinstance(f, RGBWSpot):
                    continue
                ch = _spot_channel(f, self.component)
                out += breathe(
                    [ch],
                    self.t_start,
                    self.t_end,
                    self.v_min,
                    self.v_max,
                    self.cycles,
                    self.samples_per_cycle,
                )
        return out


class Pulse(BaseModel):
    """One pulse within a PulsePattern: a stab at `offset` lasting `duration`."""

    model_config = ConfigDict(extra="forbid")

    offset: float = 0.0
    duration: float = Field(gt=0)


class PulsePattern(_Base):
    """Repeating stab pattern over time.

    Emits a stab at every `t_start + k * period + pulse.offset` for k = 0, 1, ...
    while still within `[t_start, t_end)`. Collapses what would otherwise be a
    long list of color_stab/value_stab events into one declarative event.

    When `component == "rgb"` (the default), each pulse is a `color_stab` using
    `color`. Otherwise it is a `value_stab` on the named spot channel using
    `value`.
    """

    type: Literal["pulse_pattern"]
    fixture: FixtureSelector
    pixel: PixelSelector = "*"
    component: SpotComponent | Literal["rgb"] = "rgb"
    t_start: float
    t_end: float
    period: float = Field(gt=0)
    pulses: list[Pulse] = Field(min_length=1)
    color: Color | None = None
    value: float = 1.0

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        if self.component == "rgb" and self.color is None:
            raise ValueError("pulse_pattern with component='rgb' requires `color`")
        if self.t_end <= self.t_start:
            raise ValueError(
                f"pulse_pattern: t_end ({self.t_end}) must be > t_start ({self.t_start})"
            )
        out: list[ChannelEvent] = []
        for f in _resolve_fixtures(rig, self.fixture):
            if self.component == "rgb":
                if isinstance(f, RGBStrip):
                    rgb_triples = _strip_pixel_channels(f, self.pixel)
                elif isinstance(f, RGBWSpot):
                    rgb_triples = [f.rgb()]
                else:
                    continue
                for rgb in rgb_triples:
                    out += self._emit_rgb(rgb)
            else:
                if not isinstance(f, RGBWSpot):
                    continue
                ch = _spot_channel(f, self.component)
                out += self._emit_value([ch])
        return out

    def _pulse_times(self) -> list[tuple[float, float]]:
        """List of (t_pulse_start, duration) tuples within [t_start, t_end)."""
        out: list[tuple[float, float]] = []
        k = 0
        while True:
            base = self.t_start + k * self.period
            if base >= self.t_end:
                break
            for p in self.pulses:
                t_pulse = base + p.offset
                if t_pulse < self.t_start or t_pulse >= self.t_end:
                    continue
                end = min(t_pulse + p.duration, self.t_end)
                if end <= t_pulse:
                    continue
                out.append((t_pulse, end - t_pulse))
            k += 1
        return out

    def _emit_rgb(self, rgb: tuple[int, int, int]) -> list[ChannelEvent]:
        assert self.color is not None
        out: list[ChannelEvent] = []
        for t, dur in self._pulse_times():
            out += color_stab(rgb, t, dur, self.color)
        return out

    def _emit_value(self, channels: list[int]) -> list[ChannelEvent]:
        out: list[ChannelEvent] = []
        for t, dur in self._pulse_times():
            out += stab(channels, t, dur, self.value)
        return out


class Chase(_Base):
    """Sweep of color_stabs across a strip's pixels, staggered in time.

    Pixel `p` (1-based) gets a stab at `t_start + (p - 1) * step` with the given
    `duration`. With `reverse=True`, the sweep goes from highest pixel to
    lowest.
    """

    type: Literal["chase"]
    fixture: str
    t_start: float
    step: float = Field(gt=0)
    duration: float = Field(gt=0)
    color: Color
    reverse: bool = False

    def expand(self, rig: Rig) -> list[ChannelEvent]:
        f = rig[self.fixture]
        if not isinstance(f, RGBStrip):
            raise ValueError(
                f"chase target {self.fixture!r} must be an RGB strip "
                f"(got {type(f).__name__})"
            )
        order = range(f.pixels, 0, -1) if self.reverse else range(1, f.pixels + 1)
        out: list[ChannelEvent] = []
        for i, p in enumerate(order):
            t = self.t_start + i * self.step
            out += color_stab(f.channels_for(p), t, self.duration, self.color)
        return out


Event = Annotated[
    Union[ColorStab, ColorHold, GradientHold, ValueStab, ValueHold, Breathe, PulsePattern, Chase],
    Field(discriminator="type"),
]


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slot: int = Field(ge=0)
    length_beats: float = Field(gt=0)
    color_index: int = Field(default=1, ge=0, le=69)
    events: list[Event] = Field(default_factory=list)


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    rig: Literal["hitmix"] = "hitmix"
    clips: list[Clip]

    def resolve_rig(self) -> Rig:
        if self.rig == "hitmix":
            return HITMIX_RIG
        raise ValueError(f"unknown rig {self.rig!r}")
