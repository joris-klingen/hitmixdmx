"""Claude-powered spec generation via the Claude Code CLI.

Shells out to `claude -p` so authentication uses the user's Claude Max
subscription (no separate Anthropic API key needed). The system prompt is
fully overridden via `--system-prompt`, which suppresses Claude Code's
default auto-memory and dynamic sections. Tools are disabled via
`--tools ""` so the model behaves as a pure chat completion.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

from .fixtures import HITMIX_RIG, RGBStrip, RGBWSpot, Rig
from .spec import Spec


SYSTEM_TEMPLATE = """You are a lighting designer writing DMX clips for an Ableton Live + DMXIS rig. You produce JSON specs that the lightgen renderer turns into clips.

# The rig

{rig_summary}

# Spec format

```
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
- `fade`  linear crossfade between two states (Live interpolates between endpoints).
  Fields: `fixture`, `component?` ("rgb" default or component name), `pixel?`, `t_start`, `t_end`.  When component="rgb": `color_start`, `color_end`.  Otherwise: `value_start`, `value_end`.
- `ramp`  non-linear value ramp on a single spot component.  Use for energy builds (`ease_in`) and tails (`ease_out`).
  Fields: `fixture`, `component`, `t_start`, `t_end`, `v_start?` (0), `v_end?` (1), `curve?` ("linear"|"ease_in"|"ease_out", default "linear")
- `pulse_pattern`  repeating stab pattern.  Emits a stab at every `t_start + k * period + pulse.offset`.
  Fields: `fixture`, `pixel?`, `component?` ("rgb" default), `t_start`, `t_end`, `period`, `pulses` (list of `{{offset, duration}}`), `color?`/`value?`
- `strobe`  beat-aligned fast on/off.  `rate_per_beat: 16` = sixteenth-note strobe.
  Fields: `fixture`, `pixel?`, `component?` ("rgb" default), `t_start`, `t_end`, `rate_per_beat`, `duty?` (0.5), `color?`/`value?`
- `chase`  sweep of stabs across a strip's pixels.  Pixel `p` lights at `t_start + (p-1) * step`.  Set `period` AND `t_end` to repeat.
  Fields: `fixture` (strip only), `t_start`, `step`, `duration`, `color`, `reverse?` (false), `period?`, `t_end?`
- `comet`  sweep with a fading tail per pixel (color → black over `tail_beats`).  Set `period` AND `t_end` to repeat.
  Fields: `fixture` (strip only), `t_start`, `step`, `tail_beats`, `color`, `reverse?` (false), `period?`, `t_end?`
- `sparkle`  seeded random stabs across a strip's pixels.  Low density = twinkle, high density = confetti.
  Fields: `fixture` (strip only), `t_start`, `t_end`, `density` (stabs per beat), `duration`, `color`, `seed?` (0)

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
```
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
```
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

# Modifying an existing spec

When the user message includes an existing spec to modify, return the COMPLETE updated spec. Keep everything they did NOT ask to change (slot, length_beats, color_index, untouched events) exactly as it was. Modify only what they asked for.

# Output

Respond with ONLY the JSON spec object. No markdown code fences, no commentary, no preamble — just the bare JSON starting with `{{` and ending with `}}`. Your entire response will be parsed as JSON.
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


def _build_user_prompt(user_prompt: str, base_spec: Spec | None) -> str:
    if base_spec is None:
        return user_prompt
    return (
        "Existing spec to modify (return the complete updated spec):\n\n"
        + base_spec.model_dump_json(indent=2)
        + "\n\nRequested change:\n"
        + user_prompt
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from text, tolerant of code-fence wrapping or stray prose."""
    s = text.strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object found in response: {text[:300]!r}")
    return json.loads(s[start : end + 1])


Runner = Callable[..., subprocess.CompletedProcess]


def generate_spec(
    user_prompt: str,
    *,
    base_spec: Spec | None = None,
    model: str | None = None,
    runner: Runner | None = None,
    timeout: float = 180.0,
) -> Spec:
    """Use Claude Code (`claude -p`) to generate or modify a Spec.

    Auth uses the user's Claude Max subscription via the local `claude` CLI.
    """
    rig = HITMIX_RIG if base_spec is None else base_spec.resolve_rig()
    system_text = build_system_prompt(rig)
    user_text = _build_user_prompt(user_prompt, base_spec)

    cmd = [
        "claude",
        "-p",
        user_text,
        "--system-prompt",
        system_text,
        "--output-format",
        "json",
        "--tools",
        "",
        "--no-session-persistence",
    ]
    if model:
        cmd += ["--model", model]

    run = runner if runner is not None else subprocess.run
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError(
            "`claude` CLI not found. Install Claude Code from https://claude.com/code "
            "and ensure it's on your PATH."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude timed out after {timeout}s") from e

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip() or "(no output)"
        raise RuntimeError(f"claude exited {result.returncode}: {msg}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"claude stdout was not JSON: {result.stdout[:500]!r}"
        ) from e

    if envelope.get("is_error"):
        raise RuntimeError(
            f"claude returned error: {envelope.get('result') or envelope}"
        )
    text = (envelope.get("result") or "").strip()
    if not text:
        raise RuntimeError(f"claude returned empty result. envelope={envelope}")

    try:
        spec_dict = _extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(
            f"could not parse JSON from claude response: {e}\n\nresponse text:\n{text[:1000]}"
        ) from e

    return Spec.model_validate(spec_dict)
