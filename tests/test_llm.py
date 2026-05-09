"""Tests for the Claude-Code-backed spec generator. No subprocess — uses a fake runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from lightgen.llm import build_system_prompt, generate_spec
from lightgen.spec import Spec


@dataclass
class _FakeCompleted:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


@dataclass
class _FakeRunner:
    completed: _FakeCompleted
    last_cmd: list[str] | None = field(default=None)
    last_kwargs: dict[str, Any] | None = field(default=None)

    def __call__(self, cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        self.last_cmd = cmd
        self.last_kwargs = kwargs
        return self.completed


def _envelope(result_text: str, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success" if not is_error else "error",
            "is_error": is_error,
            "result": result_text,
        }
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
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope(json.dumps(SIMPLE_SPEC))))
    spec = generate_spec("4 bars of red kicks", runner=runner)

    assert isinstance(spec, Spec)
    assert spec.clips[0].name == "test red kicks"

    cmd = runner.last_cmd
    assert cmd is not None
    assert cmd[0] == "claude"
    assert "-p" in cmd and "--system-prompt" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "4 bars of red kicks"


def test_generate_spec_with_base_includes_existing_spec() -> None:
    base = Spec.model_validate(SIMPLE_SPEC)
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope(json.dumps(SIMPLE_SPEC))))
    generate_spec("make beat 3 white", base_spec=base, runner=runner)

    cmd = runner.last_cmd
    user_text = cmd[cmd.index("-p") + 1]
    assert "Existing spec to modify" in user_text
    assert "test red kicks" in user_text
    assert "make beat 3 white" in user_text


def test_generate_spec_handles_fenced_json() -> None:
    fenced = "```json\n" + json.dumps(SIMPLE_SPEC) + "\n```"
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope(fenced)))
    spec = generate_spec("anything", runner=runner)
    assert spec.clips[0].name == "test red kicks"


def test_generate_spec_tolerates_preamble_around_json() -> None:
    text = "Sure, here's the spec:\n" + json.dumps(SIMPLE_SPEC) + "\n\nLet me know!"
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope(text)))
    spec = generate_spec("anything", runner=runner)
    assert spec.clips[0].name == "test red kicks"


def test_generate_spec_passes_model_when_provided() -> None:
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope(json.dumps(SIMPLE_SPEC))))
    generate_spec("hi", model="opus", runner=runner)
    cmd = runner.last_cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_generate_spec_raises_on_nonzero_exit() -> None:
    runner = _FakeRunner(
        _FakeCompleted(stdout="", stderr="auth failed", returncode=1)
    )
    with pytest.raises(RuntimeError, match="claude exited 1.*auth failed"):
        generate_spec("hi", runner=runner)


def test_generate_spec_raises_on_error_envelope() -> None:
    runner = _FakeRunner(
        _FakeCompleted(stdout=_envelope("rate limit", is_error=True))
    )
    with pytest.raises(RuntimeError, match="claude returned error"):
        generate_spec("hi", runner=runner)


def test_generate_spec_raises_on_unparseable_text() -> None:
    runner = _FakeRunner(_FakeCompleted(stdout=_envelope("just words, no JSON here")))
    with pytest.raises(RuntimeError, match="could not parse JSON"):
        generate_spec("hi", runner=runner)
