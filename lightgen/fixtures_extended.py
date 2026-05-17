"""Extended Hitmix rig: 4 short bars + 2 RGBW spots, 120 DMX channels.

Layout (matches the MIDI-clip lighting template):
  - bar_1:  9-pixel RGB strip,  DMX 1-27,    bottom-up
  - bar_2:  9-pixel RGB strip,  DMX 28-54,   bottom-up
  - bar_3:  9-pixel RGB strip,  DMX 55-81,   bottom-up
  - bar_4:  9-pixel RGB strip,  DMX 82-108,  bottom-up
  - spot_l: 6-channel RGBW spot, DMX 109-114 (Dim, R, G, B, W, Strobe)
  - spot_r: 6-channel RGBW spot, DMX 115-120
"""

from __future__ import annotations

from .fixtures import RGBStrip, RGBWSpot, Rig


HITMIX_EXTENDED_RIG = Rig.from_list(
    [
        RGBStrip(name="bar_1", dmx_start=1, pixels=9, orientation="bottom_up"),
        RGBStrip(name="bar_2", dmx_start=28, pixels=9, orientation="bottom_up"),
        RGBStrip(name="bar_3", dmx_start=55, pixels=9, orientation="bottom_up"),
        RGBStrip(name="bar_4", dmx_start=82, pixels=9, orientation="bottom_up"),
        RGBWSpot(name="spot_l", dmx_start=109),
        RGBWSpot(name="spot_r", dmx_start=115),
    ],
    groups={
        "bars": ["bar_1", "bar_2", "bar_3", "bar_4"],
        "spots": ["spot_l", "spot_r"],
        "left_pair": ["bar_1", "bar_2"],
        "right_pair": ["bar_3", "bar_4"],
        "outer_pair": ["bar_1", "bar_4"],
    },
)
