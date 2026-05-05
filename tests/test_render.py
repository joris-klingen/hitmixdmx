"""End-to-end smoke tests: render examples against the bundled template."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightgen.als_io import load_template, save, validate
from lightgen.fixtures import HITMIX_RIG, RGBStrip, RGBWSpot
from lightgen.renderer import render
from lightgen.spec import Spec

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (
    REPO_ROOT
    / "documentation/example_sets/claude_lights_start Project/claude_lights_start.als"
)
EXAMPLES = REPO_ROOT / "examples"


def test_template_round_trips(tmp_path):
    template = load_template(TEMPLATE)
    assert template.plugin.channel_count == 120
    assert template.plugin.base_at_id == 23980
    assert template.plugin.stride == 2
    assert template.scene_count == 50
    out = tmp_path / "round.als"
    save(template, out)
    reloaded = load_template(out)
    assert reloaded.plugin.channel_count == 120
    assert reloaded.next_pointee_id == template.next_pointee_id


def test_hitmix_rig_layout():
    assert HITMIX_RIG.total_channels == 120
    lb = HITMIX_RIG["left_bar"]
    assert isinstance(lb, RGBStrip)
    assert lb.channels_for(1) == (1, 2, 3)
    assert lb.channels_for(18) == (52, 53, 54)
    sl = HITMIX_RIG["singer_left"]
    assert isinstance(sl, RGBWSpot)
    assert sl.dimmer == 109
    assert sl.rgb() == (110, 111, 112)
    assert sl.strobe == 114


@pytest.mark.parametrize(
    "spec_name",
    ["four_on_floor_red.json", "rainbow_gradient.json", "breathing_blue.json", "multi_clip_demo.json"],
)
def test_example_renders_cleanly(tmp_path, spec_name):
    spec_path = EXAMPLES / spec_name
    raw = json.loads(spec_path.read_text())
    spec = Spec.model_validate(raw)
    template = load_template(TEMPLATE)
    render(spec, template)
    out = tmp_path / f"{spec_name}.als"
    save(template, out)
    reloaded = load_template(out)
    issues = validate(reloaded.root)
    assert not issues, "validator issues: " + "\n".join(issues)
    for clip_spec in spec.clips:
        slot = reloaded.clip_slots[clip_spec.slot]
        clip = slot.find("ClipSlot/Value/MidiClip")
        assert clip is not None, f"slot {clip_spec.slot} has no clip"
        assert clip.find("Name").get("Value") == clip_spec.name
        loop_end = float(clip.find("Loop/LoopEnd").get("Value"))
        assert loop_end == clip_spec.length_beats


def test_invalid_slot_raises(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate(
        {
            "version": 1,
            "clips": [
                {
                    "name": "out of range",
                    "slot": 9999,
                    "length_beats": 1,
                    "events": [],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="out of range"):
        render(spec, template)
