"""Tests for the snapshot visualizer's sampling logic."""

from __future__ import annotations

import pytest

from lightgen.patterns import four_on_floor, wash
from lightgen.spec import Clip, Spec
from lightgen.visualizer import (
    collect_channel_events,
    render_snapshot,
    sample_channel,
    sample_spec_at,
)


def test_sample_channel_empty():
    assert sample_channel([], 0) == 0.0
    assert sample_channel([], 1.5) == 0.0


def test_sample_channel_before_first_event_is_zero():
    assert sample_channel([(1.0, 1.0), (2.0, 1.0)], 0.5) == 0.0


def test_sample_channel_linear_interp():
    events = [(0.0, 0.0), (1.0, 1.0)]
    assert sample_channel(events, 0.0) == 0.0
    assert sample_channel(events, 0.5) == 0.5
    assert sample_channel(events, 1.0) == 1.0


def test_sample_channel_instant_jump():
    # Two events at t=1: 0.0 then 1.0 → at t=1 the post-jump value wins.
    events = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (2.0, 1.0)]
    assert sample_channel(events, 0.99) == pytest.approx(0.0, abs=0.02)
    assert sample_channel(events, 1.0) == 1.0
    assert sample_channel(events, 1.5) == 1.0


def test_sample_channel_after_last_holds():
    events = [(0.0, 0.0), (1.0, 0.7)]
    assert sample_channel(events, 5.0) == 0.7


def test_sample_spec_at_during_wash():
    # A wash of pure red on all fixtures: every R-channel should read 1.0 mid-clip.
    events = wash("*", (1.0, 0.0, 0.0), 4)
    spec = Spec(clips=[Clip(name="t", slot=0, length_beats=4, events=events)])
    state = sample_spec_at(spec, 2.0)
    # left_bar pixel 1: R-channel = 1
    assert state[1] == 1.0
    assert state.get(2, 0.0) == 0.0  # G
    assert state.get(3, 0.0) == 0.0  # B
    # singer_left dimmer (channel 109) is held to 1
    assert state[109] == 1.0


def test_sample_spec_at_between_stabs():
    # four_on_floor at beats 1 and 3 → at t=1.5 the stab should be off.
    events = four_on_floor("*", (1.0, 0.0, 0.0), 4, active_beats=[1, 3])
    spec = Spec(clips=[Clip(name="t", slot=0, length_beats=4, events=events)])
    # During the stab at t=0 (duration 0.25), R should be 1
    state_on = sample_spec_at(spec, 0.1)
    assert state_on[1] == 1.0
    # Between stabs at t=1.5, the stab is over but dimmer hold for window [2,3) hasn't started
    state_off = sample_spec_at(spec, 1.5)
    assert state_off.get(1, 0.0) == 0.0


def test_render_snapshot_produces_image():
    events = wash("*", (0.0, 0.5, 1.0), 4)
    spec = Spec(clips=[Clip(name="t", slot=0, length_beats=4, events=events)])
    img = render_snapshot(spec, 1.0)
    assert img.size == (560, 220)
    # Sample a pixel in the left-bar area — should be tinted blueish
    r, g, b = img.getpixel((20, 100))
    assert b > 200
    assert r < 50


def test_render_snapshot_empty_spec():
    spec = Spec(clips=[])
    img = render_snapshot(spec, 0.0)
    assert img.size == (560, 220)
