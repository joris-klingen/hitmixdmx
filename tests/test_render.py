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
    / "template/claude_lights_start Project/claude_lights_start.als"
)
EXAMPLES = REPO_ROOT / "examples"


def test_template_round_trips(tmp_path):
    template = load_template(TEMPLATE)
    assert template.plugin.channel_count == 120
    assert template.plugin.base_at_id == 23980
    assert template.plugin.stride == 2
    assert template.scene_count == 2000
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


def test_unknown_rig_rejected_at_parse():
    with pytest.raises(ValueError, match="unknown rig"):
        Spec.model_validate({"version": 1, "rig": "not_a_rig", "clips": []})


def test_fixture_groups_resolve():
    bars = HITMIX_RIG.resolve("bars")
    assert [b.name for b in bars] == ["left_bar", "right_bar"]
    spots = HITMIX_RIG.resolve("spots")
    assert [s.name for s in spots] == ["singer_left", "singer_right"]
    assert {f.name for f in HITMIX_RIG.resolve("*")} == {
        "left_bar", "right_bar", "singer_left", "singer_right"
    }
    with pytest.raises(KeyError):
        HITMIX_RIG.resolve("nope")


def test_pulse_pattern_rgb_repeats_on_bars(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "p",
            "slot": 0,
            "length_beats": 8,
            "events": [
                {"type": "pulse_pattern", "fixture": "bars", "t_start": 0, "t_end": 8,
                 "period": 1, "pulses": [{"offset": 0, "duration": 0.25}],
                 "color": [1, 0, 0]},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "p.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    # left_bar pixel-1 R = channel 1; expect 8 stab pulses → on/off pairs at 0..0.25, 1..1.25, ...
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    envs = clip.findall("Envelopes/Envelopes/ClipEnvelope")
    # spots should be untouched (group=bars excludes them); only bar channels appear.
    pointee_ids = {int(e.find("EnvelopeTarget/PointeeId").get("Value")) for e in envs}
    bar_at_ids = {reloaded.plugin.at_id(c) for c in range(1, 109)}  # bars: ch 1..108
    spot_at_ids = {reloaded.plugin.at_id(c) for c in range(109, 121)}
    assert pointee_ids.issubset(bar_at_ids)
    assert pointee_ids.isdisjoint(spot_at_ids)


def test_pulse_pattern_value_on_spot_dimmer(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "d",
            "slot": 0,
            "length_beats": 8,
            "events": [
                {"type": "pulse_pattern", "fixture": "spots", "component": "dimmer",
                 "t_start": 0, "t_end": 8, "period": 2,
                 "pulses": [{"offset": 0, "duration": 1.0}], "value": 1.0},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "d.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    envs = clip.findall("Envelopes/Envelopes/ClipEnvelope")
    pointee_ids = {int(e.find("EnvelopeTarget/PointeeId").get("Value")) for e in envs}
    # only spot dimmer channels (109, 115)
    assert pointee_ids == {reloaded.plugin.at_id(109), reloaded.plugin.at_id(115)}


def test_chase_stabs_per_pixel(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "c",
            "slot": 0,
            "length_beats": 16,
            "events": [
                {"type": "chase", "fixture": "left_bar", "t_start": 0, "step": 0.5,
                 "duration": 0.4, "color": [0, 0, 1]},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "c.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    envs = clip.findall("Envelopes/Envelopes/ClipEnvelope")
    # 18 pixels × 3 channels each, but only B>0; expect 18 envelopes (B channels only,
    # since R and G stay at 0 across the whole clip and get deduped into a flat envelope).
    # Actually flat envelopes do still get emitted. Just sanity check we touched all 18 pixels' B.
    pointee_ids = {int(e.find("EnvelopeTarget/PointeeId").get("Value")) for e in envs}
    blue_at_ids = {reloaded.plugin.at_id(p * 3) for p in range(1, 19)}  # B channels: 3, 6, 9, ..., 54
    assert blue_at_ids.issubset(pointee_ids)


def test_chase_with_period_repeats(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "r",
            "slot": 0,
            "length_beats": 16,
            "events": [
                {"type": "chase", "fixture": "left_bar", "t_start": 0, "step": 0.05,
                 "duration": 0.25, "color": [1, 0, 0], "period": 2, "t_end": 16},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "r.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    # 8 sweeps × 18 pixels each = 144 stab pulses → many ON transitions on each pixel's R channel.
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    r_at_id = reloaded.plugin.at_id(1)  # R of left_bar pixel 1
    env = next(
        e for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")
        if int(e.find("EnvelopeTarget/PointeeId").get("Value")) == r_at_id
    )
    floats = env.findall("Automation/Events/FloatEvent")
    rising_edges = 0
    prev_v = 0.0
    for f in floats:
        t = float(f.get("Time"))
        v = float(f.get("Value"))
        if t < 0:
            continue
        if v > 0.5 and prev_v <= 0.5:
            rising_edges += 1
        prev_v = v
    assert rising_edges == 8, f"expected 8 rising edges (one per sweep), got {rising_edges}"


def test_chase_period_without_t_end_raises():
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "bad",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "chase", "fixture": "left_bar", "t_start": 0, "step": 0.1,
                 "duration": 0.25, "color": [1, 0, 0], "period": 1},
            ],
        }],
    })
    with pytest.raises(ValueError, match="period and t_end"):
        render(spec, template)


def test_fade_color_emits_linear_interp(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "f",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "fade", "fixture": "left_bar", "pixel": 1,
                 "t_start": 0, "t_end": 4,
                 "color_start": [1, 0, 0], "color_end": [0, 0, 1]},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "fade.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    r_id = reloaded.plugin.at_id(1)  # pixel 1 R = ch 1
    b_id = reloaded.plugin.at_id(3)  # pixel 1 B = ch 3
    envs = {int(e.find("EnvelopeTarget/PointeeId").get("Value")): e
            for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")}
    r_floats = envs[r_id].findall("Automation/Events/FloatEvent")
    b_floats = envs[b_id].findall("Automation/Events/FloatEvent")
    # R should ramp from 1 down to 0; B should ramp from 0 up to 1.
    r_in_range = [(float(f.get("Time")), float(f.get("Value"))) for f in r_floats if float(f.get("Time")) >= 0]
    b_in_range = [(float(f.get("Time")), float(f.get("Value"))) for f in b_floats if float(f.get("Time")) >= 0]
    assert r_in_range[0] == (0.0, 1.0) and r_in_range[-1] == (4.0, 0.0)
    assert b_in_range[0] == (0.0, 0.0) and b_in_range[-1] == (4.0, 1.0)


def test_fade_value_requires_value_endpoints():
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "bad",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "fade", "fixture": "singer_left", "component": "dimmer",
                 "t_start": 0, "t_end": 4},
            ],
        }],
    })
    template = load_template(TEMPLATE)
    with pytest.raises(ValueError, match="value_start"):
        render(spec, template)


def test_strobe_emits_one_pulse_per_period(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "s",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "strobe", "fixture": "left_bar", "pixel": 1,
                 "t_start": 0, "t_end": 4,
                 "rate_per_beat": 4, "duty": 0.5, "color": [1, 1, 1]},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "strobe.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    r_id = reloaded.plugin.at_id(1)
    env = next(e for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")
               if int(e.find("EnvelopeTarget/PointeeId").get("Value")) == r_id)
    # 4 beats × 4 strobes/beat = 16 rising edges on the R channel.
    rising = 0
    prev = 0.0
    for f in env.findall("Automation/Events/FloatEvent"):
        t = float(f.get("Time"))
        v = float(f.get("Value"))
        if t < 0:
            continue
        if v > 0.5 and prev <= 0.5:
            rising += 1
        prev = v
    assert rising == 16, f"expected 16 strobe pulses, got {rising}"


def test_comet_pixel_fades_to_black(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "co",
            "slot": 0,
            "length_beats": 8,
            "events": [
                {"type": "comet", "fixture": "left_bar", "t_start": 0,
                 "step": 0.1, "tail_beats": 1.0, "color": [1, 0, 0]},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "comet.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    r_id = reloaded.plugin.at_id(1)  # pixel 1 R = ch 1; lit at t=0
    env = next(e for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")
               if int(e.find("EnvelopeTarget/PointeeId").get("Value")) == r_id)
    pts = [(float(f.get("Time")), float(f.get("Value")))
           for f in env.findall("Automation/Events/FloatEvent")
           if float(f.get("Time")) >= 0]
    # Pixel 1 lights at t=0 with full red, ramps to 0 at t=1.0
    assert pts[0] == (0.0, 1.0)
    assert (1.0, 0.0) in pts


def test_sparkle_is_seed_reproducible(tmp_path):
    def render_and_collect(seed: int):
        template = load_template(TEMPLATE)
        spec = Spec.model_validate({
            "version": 1,
            "clips": [{
                "name": "sp",
                "slot": 0,
                "length_beats": 4,
                "events": [
                    {"type": "sparkle", "fixture": "left_bar",
                     "t_start": 0, "t_end": 4, "density": 8, "duration": 0.1,
                     "color": [1, 1, 1], "seed": seed},
                ],
            }],
        })
        render(spec, template)
        clip = template.clip_slots[0].find("ClipSlot/Value/MidiClip")
        return {int(e.find("EnvelopeTarget/PointeeId").get("Value"))
                for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")}

    a = render_and_collect(seed=42)
    b = render_and_collect(seed=42)
    c = render_and_collect(seed=99)
    assert a == b, "same seed should produce identical channel coverage"
    assert a != c, "different seeds should (with high probability) differ"


def test_ramp_ease_in_is_monotone_accelerating(tmp_path):
    template = load_template(TEMPLATE)
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "ra",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "ramp", "fixture": "singer_left", "component": "dimmer",
                 "t_start": 0, "t_end": 4, "v_start": 0.0, "v_end": 1.0,
                 "curve": "ease_in", "samples": 8},
            ],
        }],
    })
    render(spec, template)
    out = tmp_path / "ramp.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    clip = reloaded.clip_slots[0].find("ClipSlot/Value/MidiClip")
    dim_id = reloaded.plugin.at_id(109)  # singer_left dimmer
    env = next(e for e in clip.findall("Envelopes/Envelopes/ClipEnvelope")
               if int(e.find("EnvelopeTarget/PointeeId").get("Value")) == dim_id)
    pts = sorted(
        (float(f.get("Time")), float(f.get("Value")))
        for f in env.findall("Automation/Events/FloatEvent")
        if float(f.get("Time")) >= 0
    )
    vals = [v for _, v in pts]
    assert vals[0] == 0.0 and vals[-1] == 1.0
    # ease_in: derivative grows over time → later deltas exceed earlier deltas
    deltas = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    assert deltas[-1] > deltas[0], f"ease_in should accelerate; deltas={deltas}"


def test_clean_clears_unspecified_slots(tmp_path):
    template = load_template(TEMPLATE)
    # Pre-populate two slots so we can prove clean wipes the one not in the next spec.
    setup_spec = Spec.model_validate({
        "version": 1,
        "clips": [
            {"name": "keeper", "slot": 0, "length_beats": 4, "events": [
                {"type": "color_hold", "fixture": "*", "t_start": 0, "t_end": 4, "color": [0, 1, 0]},
            ]},
            {"name": "to be wiped", "slot": 5, "length_beats": 4, "events": [
                {"type": "color_hold", "fixture": "*", "t_start": 0, "t_end": 4, "color": [0, 0, 1]},
            ]},
        ],
    })
    render(setup_spec, template)
    pre_populated = sum(
        1 for s in template.clip_slots if s.find("ClipSlot/Value/MidiClip") is not None
    )
    assert pre_populated == 2, f"expected 2 pre-populated slots, got {pre_populated}"
    spec = Spec.model_validate({
        "version": 1,
        "clips": [{
            "name": "only one",
            "slot": 0,
            "length_beats": 4,
            "events": [
                {"type": "color_hold", "fixture": "*", "t_start": 0, "t_end": 4, "color": [1, 0, 0]},
            ],
        }],
    })
    render(spec, template, clean=True)
    out = tmp_path / "clean.als"
    save(template, out)
    reloaded = load_template(out)
    assert not validate(reloaded.root)
    populated = [
        i for i, s in enumerate(reloaded.clip_slots)
        if s.find("ClipSlot/Value/MidiClip") is not None
    ]
    assert populated == [0]


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
