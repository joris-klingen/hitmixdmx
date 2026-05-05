"""Fixture model: maps semantic ("pixel 5 of left bar") to DMX channel numbers.

A `Fixture` is a typed thing patched at a DMX start address. The renderer asks
fixtures for channels via `Fixture.channels_for(...)`; it never does address
arithmetic itself.

Default rig (Hitmix):
  - left_bar:    18-pixel RGB strip, DMX 1-54,    bottom-up orientation
  - right_bar:   18-pixel RGB strip, DMX 55-108,  bottom-up orientation
  - singer_left: 6-channel RGBW spot (Dimmer, R, G, B, W, Strobe), DMX 109-114
  - singer_right: same fixture type, DMX 115-120
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Orientation = Literal["bottom_up", "top_down"]


@dataclass(frozen=True, kw_only=True)
class RGBStrip:
    """Pixel strip with R, G, B channels per pixel."""

    name: str
    dmx_start: int
    pixels: int
    orientation: Orientation = "bottom_up"

    @property
    def channel_count(self) -> int:
        return self.pixels * 3

    def pixel_index(self, p: int) -> int:
        """Translate semantic pixel index (1-based, 1=bottom) to physical pixel index."""
        if not 1 <= p <= self.pixels:
            raise ValueError(f"pixel {p} out of range [1, {self.pixels}] for {self.name!r}")
        return p if self.orientation == "bottom_up" else (self.pixels - p + 1)

    def channels_for(self, p: int) -> tuple[int, int, int]:
        """Return (R, G, B) DMX channels for semantic pixel p."""
        phys = self.pixel_index(p)
        base = self.dmx_start + (phys - 1) * 3
        return base, base + 1, base + 2


@dataclass(frozen=True, kw_only=True)
class RGBWSpot:
    """Single-fixture RGBW spot with named channels.

    Channel layout matches the DMXIS configuration of the singer spots:
      offset 0: Dimmer
      offset 1: Red
      offset 2: Green
      offset 3: Blue
      offset 4: White
      offset 5: Strobe
    """

    name: str
    dmx_start: int

    @property
    def channel_count(self) -> int:
        return 6

    @property
    def dimmer(self) -> int:
        return self.dmx_start + 0

    @property
    def red(self) -> int:
        return self.dmx_start + 1

    @property
    def green(self) -> int:
        return self.dmx_start + 2

    @property
    def blue(self) -> int:
        return self.dmx_start + 3

    @property
    def white(self) -> int:
        return self.dmx_start + 4

    @property
    def strobe(self) -> int:
        return self.dmx_start + 5

    def rgb(self) -> tuple[int, int, int]:
        return self.red, self.green, self.blue


Fixture = RGBStrip | RGBWSpot


@dataclass(frozen=True)
class Rig:
    fixtures: dict[str, Fixture] = field(default_factory=dict)

    @classmethod
    def from_list(cls, fixtures: list[Fixture]) -> "Rig":
        names = [f.name for f in fixtures]
        if len(set(names)) != len(names):
            raise ValueError(f"fixture names must be unique: {names}")
        return cls(fixtures={f.name: f for f in fixtures})

    def __getitem__(self, name: str) -> Fixture:
        if name not in self.fixtures:
            raise KeyError(
                f"fixture {name!r} not in rig (have: {list(self.fixtures)})"
            )
        return self.fixtures[name]

    def __iter__(self):
        return iter(self.fixtures.values())

    @property
    def total_channels(self) -> int:
        if not self.fixtures:
            return 0
        return max(f.dmx_start + f.channel_count - 1 for f in self.fixtures.values())


HITMIX_RIG = Rig.from_list(
    [
        RGBStrip(name="left_bar", dmx_start=1, pixels=18, orientation="bottom_up"),
        RGBStrip(name="right_bar", dmx_start=55, pixels=18, orientation="bottom_up"),
        RGBWSpot(name="singer_left", dmx_start=109),
        RGBWSpot(name="singer_right", dmx_start=115),
    ]
)
"""The default rig for the Hitmix setup. 120 DMX channels total."""
