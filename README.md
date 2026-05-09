# lightgen

Generate Ableton Live (`.als`) clips that drive DMXIS lighting fixtures from a JSON spec.

Phase 0 (this CLI) is the foundation for the full app described in [documentation/lighting-app-plan.md](documentation/lighting-app-plan.md). The `.als` format details come from [documentation/build-lighting-clips.md](documentation/build-lighting-clips.md).

## Usage

```bash
# Inspect a template
uv run lightgen inspect "documentation/example_sets/claude_lights_start Project/claude_lights_start.als"

# Render a spec into a new .als
uv run lightgen render examples/four_on_floor_red.json \
  "documentation/example_sets/claude_lights_start Project/claude_lights_start.als" \
  out/four_on_floor_red.als
```

Open the resulting `.als` in Ableton Live; the generated clip will be in the slot you specified, named as in the spec, ready to launch.

### Generate or tweak a spec with Claude

For fast iteration without hand-editing JSON:

```bash
# Set your API key once per shell session (get one at https://console.anthropic.com/)
export ANTHROPIC_API_KEY=sk-ant-...

# Generate a fresh spec from a description
uv run lightgen prompt "4-bar red four-on-floor with a white flash on beat 4" my_spec.json

# Render it
uv run lightgen render my_spec.json \
  "documentation/example_sets/claude_lights_start Project/claude_lights_start.als" \
  out/my.als

# Listen, then tweak (overwrites my_spec.json in place)
uv run lightgen tweak my_spec.json "make beat 3 blue instead of red"
uv run lightgen render my_spec.json \
  "documentation/example_sets/claude_lights_start Project/claude_lights_start.als" \
  out/my.als
```

Use `--out new.json` on `tweak` to keep the original spec when you want to compare. Use `--model claude-opus-4-7` on either if a request needs more creative reasoning.

## Setup

```bash
uv sync       # installs Python 3.13, pydantic, anthropic, pytest
uv run pytest # runs the test suite
```

## The rig

`lightgen` ships with one rig built in, called `hitmix`, matching the hardware in the bundled template:

| Fixture        | Type           | DMX channels | Notes                                |
| -------------- | -------------- | ------------ | ------------------------------------ |
| `left_bar`     | RGB strip      | 1–54         | 18 pixels, bottom-up                 |
| `right_bar`    | RGB strip      | 55–108       | 18 pixels, bottom-up                 |
| `singer_left`  | RGBW spot      | 109–114      | Dimmer, R, G, B, W, Strobe           |
| `singer_right` | RGBW spot      | 115–120      | Dimmer, R, G, B, W, Strobe           |

Total: 120 DMX channels (must match the count configured in DMXIS itself).

## Spec format

A spec is JSON. One file describes one or more clips that get dropped into specific slots of a template `.als`.

```json
{
  "version": 1,
  "rig": "hitmix",
  "clips": [
    {
      "name": "four-on-floor red",
      "slot": 0,
      "length_beats": 4,
      "color_index": 1,
      "events": [
        {"type": "color_stab", "fixture": "*", "pixel": "*", "time": 0, "duration": 0.25, "color": [1, 0, 0]},
        {"type": "value_hold", "fixture": "*", "component": "dimmer", "t_start": 0, "t_end": 4, "value": 1.0}
      ]
    }
  ]
}
```

### Event types

| Type            | Targets                       | Required fields                                                  |
| --------------- | ----------------------------- | ---------------------------------------------------------------- |
| `color_stab`    | RGB triples on strips & spots | `fixture`, `pixel?`, `time`, `duration`, `color: [r,g,b]`        |
| `color_hold`    | RGB triples on strips & spots | `fixture`, `pixel?`, `t_start`, `t_end`, `color`                 |
| `gradient_hold` | Strip pixels (HSV interp)     | `fixture`, `t_start`, `t_end`, `hue_start`, `hue_end`            |
| `value_stab`    | Spot single channel           | `fixture`, `component`, `time`, `duration`, `value`              |
| `value_hold`    | Spot single channel           | `fixture`, `component`, `t_start`, `t_end`, `value`              |
| `breathe`       | RGB triple or spot channel    | `fixture`, `t_start`, `t_end`, `v_min`, `v_max`, `cycles`        |

- `fixture` accepts `"*"` for "all fixtures" (filtered by event type's compatibility).
- `pixel` (strips) accepts an integer 1..N or `"*"` for all pixels. Default: `"*"`.
- `component` (spots) is one of `dimmer`, `red`, `green`, `blue`, `white`, `strobe`.
- `color_index` is Live's clip-color palette: 0..69. 1=red, 4=blue, 9=purple, etc.

### Examples

- [examples/four_on_floor_red.json](examples/four_on_floor_red.json) — classic 1/4-note kick stabs in red, dimmer locked on.
- [examples/rainbow_gradient.json](examples/rainbow_gradient.json) — HSV gradient across each strip, contrasting spot colors on the singers.
- [examples/breathing_blue.json](examples/breathing_blue.json) — 4-cycle sinusoidal breathe over 16 beats.
- [examples/multi_clip_demo.json](examples/multi_clip_demo.json) — all three of the above in one render, into slots 0/1/2.

## Architecture

```
spec (JSON)
  ↓ Spec.model_validate (pydantic)
Spec
  ↓ event.expand(rig)  per event
ChannelEvent = (channel, time, value)
  ↓ group, sort, clamp [0,1], dedupe, terminate at clip_len
{channel: [(time, value), ...]}
  ↓ build ClipEnvelope per channel; clone template MidiClip
MidiClip XML
  ↓ insert into ClipSlot/Value, run validator
.als (gzipped XML)
```

The pipeline is intentionally three-stage: invalid input is caught at parse, invalid logic at expand, invalid output at validate. Live will crash on certain XML invariants ([documented here](documentation/build-lighting-clips.md#time-and-value-invariants--live-will-crash-if-violated)) — `als_io.validate` checks them all and refuses to save a non-conforming file.

## Limits in this phase

- Only the `hitmix` rig is supported. Custom rigs come in a later phase.
- No high-level patterns yet (chase, comet, sweep, strobe). Build them by stacking the primitives or describe them to `lightgen prompt`.
- No DMX preview, no UI yet. See the [build plan](documentation/lighting-app-plan.md) for the roadmap.
- The renderer uses the *first* MidiClip it finds in the template's DMXIS track as the clone source. If your template has no clips, add one in Live first.
