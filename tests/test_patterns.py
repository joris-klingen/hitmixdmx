"""Tests for the deterministic pattern recipes."""

from __future__ import annotations

import pytest

from lightgen.patterns import (
    active_windows,
    breathing,
    chase,
    four_on_floor,
    parse_beats,
    sparkle,
    wash,
)
from lightgen.spec import Clip, Spec


def test_parse_beats_empty():
    assert parse_beats("") is None
    assert parse_beats("   ") is None


def test_parse_beats_list():
    assert parse_beats("1,3,5") == [1, 3, 5]
    assert parse_beats(" 2 , 4 ") == [2, 4]


def test_parse_beats_range():
    assert parse_beats("1-3") == [1, 2, 3]
    assert parse_beats("3-1") == [1, 2, 3]  # backward range still works
    assert parse_beats("1,3-5,8") == [1, 3, 4, 5, 8]


def test_parse_beats_dedup():
    assert parse_beats("1,1,2,2-3") == [1, 2, 3]


def test_active_windows_none():
    assert active_windows(None, 4) == [(0.0, 4.0)]
    assert active_windows([], 4) == [(0.0, 4.0)]


def test_active_windows_contiguous():
    assert active_windows([1, 2, 3, 4], 4) == [(0.0, 4.0)]


def test_active_windows_gaps():
    assert active_windows([1, 3], 4) == [(0.0, 1.0), (2.0, 3.0)]
    assert active_windows([1, 2, 4], 4) == [(0.0, 2.0), (3.0, 4.0)]


def test_active_windows_clips_out_of_range():
    assert active_windows([0, 1, 5], 4) == [(0.0, 1.0)]


def test_four_on_floor_default_beats():
    events = four_on_floor("*", (1, 0, 0), 4)
    stabs = [e for e in events if e.type == "color_stab"]
    holds = [e for e in events if e.type == "value_hold"]
    assert len(stabs) == 4
    assert [s.time for s in stabs] == [0.0, 1.0, 2.0, 3.0]
    assert len(holds) == 1
    assert (holds[0].t_start, holds[0].t_end) == (0.0, 4.0)


def test_four_on_floor_selected_beats():
    events = four_on_floor("*", (1, 0, 0), 4, active_beats=[1, 3])
    stabs = [e for e in events if e.type == "color_stab"]
    holds = [e for e in events if e.type == "value_hold"]
    assert [s.time for s in stabs] == [0.0, 2.0]
    assert [(h.t_start, h.t_end) for h in holds] == [(0.0, 1.0), (2.0, 3.0)]


def test_breathing_splits_on_gaps():
    events = breathing("bars", (0, 0.5, 1), 8, active_beats=[1, 2, 5, 6, 7, 8])
    breathes = [e for e in events if e.type == "breathe"]
    assert [(b.t_start, b.t_end) for b in breathes] == [(0.0, 2.0), (4.0, 8.0)]


def test_wash_single_window():
    events = wash("spots", (0, 0, 1), 4)
    color_holds = [e for e in events if e.type == "color_hold"]
    value_holds = [e for e in events if e.type == "value_hold"]
    assert len(color_holds) == 1
    assert color_holds[0].color == (0, 0, 1)
    assert (color_holds[0].t_start, color_holds[0].t_end) == (0.0, 4.0)
    assert len(value_holds) == 1


def test_chase_expands_group_to_strips():
    events = chase("bars", (1, 1, 0), 4, step=0.1, duration=0.2)
    chases = [e for e in events if e.type == "chase"]
    fixture_names = {c.fixture for c in chases}
    assert fixture_names == {"left_bar", "right_bar"}


def test_chase_skips_spots():
    events = chase("*", (1, 1, 0), 4)
    chases = [e for e in events if e.type == "chase"]
    fixture_names = {c.fixture for c in chases}
    assert fixture_names == {"left_bar", "right_bar"}


def test_sparkle_skips_spots():
    events = sparkle("*", (1, 1, 1), 4)
    sparkles = [e for e in events if e.type == "sparkle"]
    fixture_names = {s.fixture for s in sparkles}
    assert fixture_names == {"left_bar", "right_bar"}


def test_patterns_compose_into_clip():
    """Stacking patterns on one clip should pass spec validation."""
    events = four_on_floor("*", (1, 0, 0), 4) + sparkle(
        "bars", (1, 1, 1), 4, active_beats=[3, 4]
    )
    spec = Spec(
        clips=[Clip(name="test", slot=0, length_beats=4, color_index=1, events=events)]
    )
    assert len(spec.clips[0].events) == len(events)
