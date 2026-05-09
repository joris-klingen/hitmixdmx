"""Claude-powered spec generation and editing.

Two entry points:
  - generate_spec(prompt)               for from-scratch
  - generate_spec(prompt, base=spec)    for tweaks of an existing spec

Both use Anthropic tool-use with the Spec JSON schema as the tool's input
schema, forcing Claude to return a structurally-valid spec that we then
validate through pydantic. The system prompt is large and identical between
calls, so we mark it `cache_control=ephemeral` to hit the prompt cache.
"""

from __future__ import annotations

import os
from typing import Any

from .fixtures import HITMIX_RIG, RGBStrip, RGBWSpot, Rig
from .spec import Spec


DEFAULT_MODEL = "claude-sonnet-4-6"


SYSTEM_TEMPLATE = """You are a lighting designer writing DMX clips for an Ableton Live + DMXIS rig. You produce JSON specs that the lightgen renderer turns into clips.

# The rig

{rig_summary}

# Spec format

```json
{{
  "version": 1,
  "rig": "hitmix",
  "clips": [
    {{
      "name": "<short clip name>",
      "slot": 0,                  // 0-based slot index in the DMXIS clip track
      "length_beats": 4,          // loop length in beats; > 0
      "color_index": 1,           // optional Live clip-color palette: 0..69
      "events": [ ... ]
    }}
  ]
}}
```

Live clip colors that are useful: 0=white-ish, 1=red, 2=orange, 4=yellow, 5=green, 8=cyan, 9=blue, 14=purple, 16=magenta. Pick something that matches the clip's mood.

# Event types

All times are in beats from clip start. All values and color components are floats in [0, 1]. **No event time or t_end may exceed the clip's length_beats.**

- `color_stab`  flash an RGB color, then off.
  Fields: `fixture`, `pixel?` (strips only, default "*"), `time`, `duration`, `color: [r,g,b]`
- `color_hold`  sustain an RGB color over a range.
  Fields: `fixture`, `pixel?`, `t_start`, `t_end`, `color: [r,g,b]`
- `gradient_hold`  HSV gradient across a strip's pixels (strips only).
  Fields: `fixture`, `t_start`, `t_end`, `hue_start`, `hue_end`, `saturation?` (1), `value?` (1)
- `value_stab`  flash a value on a single spot channel.
  Fields: `fixture`, `component` ("dimmer"|"red"|"green"|"blue"|"white"|"strobe"), `time`, `duration`, `value?` (1)
- `value_hold`  sustain a value on a single spot channel.
  Fields: `fixture`, `component`, `t_start`, `t_end`, `value`
- `breathe`  sinusoidal modulation of an RGB color or single channel.
  Fields: `fixture`, `component?` ("rgb" or component name, default "rgb"), `pixel?`, `t_start`, `t_end`, `v_min?` (0), `v_max?` (1), `cycles?` (1), `color?` ([r,g,b], used when component="rgb")

# Selectors

- `fixture` accepts a fixture name from the rig, or `"*"` for all (auto-filtered to fixtures the event type supports).
- `pixel` accepts an integer 1..N (1-based), or `"*"` for all pixels of the strip.

# Crucial rules

1. RGBW spots have a separate **dimmer** channel. A color event alone gives you a black spot — always pair color events with a `value_hold` on the dimmer (or modulate dimmer via `breathe`/`value_stab`) for the duration the spot should be visible.
2. Strips don't have a separate dimmer; their pixel RGB channels are the brightness. Just emit the colors you want.
3. To make ONE beat different from a repeating pattern: emit the repeating events for the other beats, then a separate event at the unique beat with the different color/value. The renderer will overlay correctly per-channel.
4. Keep specs minimal — fewer events = easier to read and tweak. Use `"*"` selectors and `color_hold` over long ranges instead of many tiny stabs when the lights are static.

# Examples

Four-on-floor red kick stabs, dimmer locked on, 4-beat loop:
```json
{{
  "version": 1, "rig": "hitmix",
  "clips": [{{
    "name": "four-on-floor red", "slot": 0, "length_beats": 4, "color_index": 1,
    "events": [
      {{"type": "color_stab", "fixture": "*", "time": 0, "duration": 0.25, "color": [1, 0, 0]}},
      {{"type": "color_stab", "fixture": "*", "time": 1, "duration": 0.25, "color": [1, 0, 0]}},
      {{"type": "color_stab", "fixture": "*", "time": 2, "duration": 0.25, "color": [1, 0, 0]}},
      {{"type": "color_stab", "fixture": "*", "time": 3, "duration": 0.25, "color": [1, 0, 0]}},
      {{"type": "value_hold", "fixture": "*", "component": "dimmer", "t_start": 0, "t_end": 4, "value": 1.0}}
    ]
  }}]
}}
```

Slow breathing blue, 16 beats, with deeper-blue spots:
```json
{{
  "version": 1, "rig": "hitmix",
  "clips": [{{
    "name": "breathing blue", "slot": 0, "length_beats": 16, "color_index": 9,
    "events": [
      {{"type": "breathe", "fixture": "left_bar",  "t_start": 0, "t_end": 16, "color": [0, 0.4, 1.0], "v_min": 0.05, "v_max": 1.0, "cycles": 4}},
      {{"type": "breathe", "fixture": "right_bar", "t_start": 0, "t_end": 16, "color": [0, 0.4, 1.0], "v_min": 0.05, "v_max": 1.0, "cycles": 4}},
      {{"type": "color_hold", "fixture": "singer_left",  "t_start": 0, "t_end": 16, "color": [0, 0, 0.6]}},
      {{"type": "color_hold", "fixture": "singer_right", "t_start": 0, "t_end": 16, "color": [0, 0, 0.6]}},
      {{"type": "breathe", "fixture": "*", "component": "dimmer", "t_start": 0, "t_end": 16, "v_min": 0.2, "v_max": 1.0, "cycles": 4}}
    ]
  }}]
}}
```

# Your task

Call the `submit_spec` tool with the complete spec. Return ONLY through the tool — do not write the JSON in your text reply.

When given an existing spec to modify: keep everything the user did NOT ask to change, modify only what they asked for, preserve the slot/length/color_index unless explicitly asked otherwise.
"""


def _rig_summary(rig: Rig) -> str:
    lines = []
    for f in rig:
        if isinstance(f, RGBStrip):
            end = f.dmx_start + f.channel_count - 1
            lines.append(
                f"- `{f.name}`: RGB strip, {f.pixels} pixels ({f.orientation}), "
                f"DMX {f.dmx_start}..{end}"
            )
        elif isinstance(f, RGBWSpot):
            end = f.dmx_start + f.channel_count - 1
            lines.append(
                f"- `{f.name}`: RGBW spot (channels: dimmer, red, green, blue, white, strobe), "
                f"DMX {f.dmx_start}..{end}"
            )
    return "\n".join(lines)


def build_system_prompt(rig: Rig = HITMIX_RIG) -> str:
    return SYSTEM_TEMPLATE.format(rig_summary=_rig_summary(rig))


def _spec_input_schema() -> dict[str, Any]:
    """JSON Schema for the Spec tool input. Pydantic produces JSON Schema with
    $defs/$ref and discriminated unions; Anthropic accepts these."""
    return Spec.model_json_schema()


def _build_messages(user_prompt: str, base_spec: Spec | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if base_spec is not None:
        content.append(
            {
                "type": "text",
                "text": (
                    "Here is the existing spec. Apply the requested change and return the "
                    "complete updated spec:\n\n```json\n"
                    + base_spec.model_dump_json(indent=2)
                    + "\n```"
                ),
            }
        )
    content.append({"type": "text", "text": user_prompt})
    return [{"role": "user", "content": content}]


def generate_spec(
    user_prompt: str,
    *,
    base_spec: Spec | None = None,
    model: str = DEFAULT_MODEL,
    client: Any = None,
    max_tokens: int = 8192,
) -> Spec:
    """Ask Claude to produce or modify a Spec. Returns a validated Spec.

    Raises RuntimeError on API or schema failures, ValidationError if Claude
    returns a spec that doesn't match the pydantic model.
    """
    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package not installed; run `uv sync`") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Get a key at https://console.anthropic.com/ "
                "and export it: `export ANTHROPIC_API_KEY=sk-ant-...`"
            )
        client = anthropic.Anthropic()

    rig = HITMIX_RIG if base_spec is None else base_spec.resolve_rig()
    system_text = build_system_prompt(rig)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[
            {
                "name": "submit_spec",
                "description": "Submit the complete lighting spec.",
                "input_schema": _spec_input_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "submit_spec"},
        messages=_build_messages(user_prompt, base_spec),
    )

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_spec":
            return Spec.model_validate(block.input)
    raise RuntimeError(
        "Claude did not call submit_spec. stop_reason="
        f"{getattr(response, 'stop_reason', '?')!r}"
    )
