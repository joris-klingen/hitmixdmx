"""Tests for the Claude-backed spec generator. No network — uses a fake client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lightgen.llm import build_system_prompt, generate_spec
from lightgen.spec import Spec


@dataclass
class _FakeBlock:
    type: str
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


def _make_response(spec_dict: dict[str, Any]) -> _FakeResponse:
    return _FakeResponse(
        content=[_FakeBlock(type="tool_use", name="submit_spec", input=spec_dict)]
    )


SIMPLE_SPEC: dict[str, Any] = {
    "version": 1,
    "rig": "hitmix",
    "clips": [
        {
            "name": "test red kicks",
            "slot": 0,
            "length_beats": 4,
            "color_index": 1,
            "events": [
                {
                    "type": "color_stab",
                    "fixture": "*",
                    "pixel": "*",
                    "time": 0,
                    "duration": 0.25,
                    "color": [1, 0, 0],
                },
                {
                    "type": "value_hold",
                    "fixture": "*",
                    "component": "dimmer",
                    "t_start": 0,
                    "t_end": 4,
                    "value": 1.0,
                },
            ],
        }
    ],
}


def test_build_system_prompt_mentions_rig_fixtures() -> None:
    prompt = build_system_prompt()
    for fixture in ("left_bar", "right_bar", "singer_left", "singer_right"):
        assert fixture in prompt


def test_generate_spec_from_scratch() -> None:
    client = _FakeClient(_make_response(SIMPLE_SPEC))
    spec = generate_spec("4 bars of red kicks", client=client)

    assert isinstance(spec, Spec)
    assert spec.clips[0].name == "test red kicks"
    assert len(spec.clips[0].events) == 2

    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_spec"}
    assert kwargs["tools"][0]["name"] == "submit_spec"
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    user_blocks = kwargs["messages"][0]["content"]
    assert len(user_blocks) == 1
    assert user_blocks[0]["text"] == "4 bars of red kicks"


def test_generate_spec_with_base_includes_existing_spec() -> None:
    base = Spec.model_validate(SIMPLE_SPEC)
    client = _FakeClient(_make_response(SIMPLE_SPEC))
    generate_spec("make beat 3 white", base_spec=base, client=client)

    user_blocks = client.messages.last_kwargs["messages"][0]["content"]
    assert len(user_blocks) == 2
    assert "existing spec" in user_blocks[0]["text"]
    assert "test red kicks" in user_blocks[0]["text"]
    assert user_blocks[1]["text"] == "make beat 3 white"


def test_generate_spec_raises_when_tool_not_called() -> None:
    response = _FakeResponse(
        content=[_FakeBlock(type="text")], stop_reason="end_turn"
    )
    client = _FakeClient(response)
    with pytest.raises(RuntimeError, match="did not call submit_spec"):
        generate_spec("hi", client=client)
