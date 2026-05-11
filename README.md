# lightgen

Generate Ableton Live (`.als`) clips that drive DMXIS lighting fixtures from a JSON spec.

Phase 0 (this CLI) is the foundation for the full app described in [documentation/lighting-app-plan.md](documentation/lighting-app-plan.md). The `.als` format details come from [documentation/build-lighting-clips.md](documentation/build-lighting-clips.md).

## Usage

```bash
# Inspect a template
uv run lightgen inspect "template/claude_lights_start Project/claude_lights_start.als"

# Render a spec into a new .als
uv run lightgen render examples/four_on_floor_red.json \
  "template/claude_lights_start Project/claude_lights_start.als" \
  out/four_on_floor_red.als
```

Open the resulting `.als` in Ableton Live; the generated clip will be in the slot you specified, named as in the spec, ready to launch.

### Generate or tweak a spec with Claude

For fast iteration without hand-editing JSON:

```bash
# Generate a fresh spec from a description
uv run lightgen prompt "4-bar red four-on-floor with a white flash on beat 4" my_spec.json

# Render it
uv run lightgen render my_spec.json \
  "template/claude_lights_start Project/claude_lights_start.als" \
  out/my.als

# Listen, then tweak (overwrites my_spec.json in place)
uv run lightgen tweak my_spec.json "make beat 3 blue instead of red"
uv run lightgen render my_spec.json \
  "template/claude_lights_start Project/claude_lights_start.als" \
  out/my.als
```

Authentication uses the local `claude` CLI (Claude Code), so this taps your Claude Max subscription — no separate API key needed. Make sure you've run `claude` at least once and signed in.

Use `--out new.json` on `tweak` to keep the original spec when you want to compare. Use `--model opus` (or `--model sonnet`) if a request needs a different model.

### Web UI

For a browser-based prompt / tweak / render loop instead of typing commands:

```bash
uv sync --extra ui     # one-time, pulls in gradio
uv run lightgen ui     # launches at http://127.0.0.1:7860
```

Or just **double-click `start-ui.command`** in Finder (drag it to the dock for a persistent shortcut).

You'll get a single page with a textarea (prompt), a tweak box, a JSON preview of the generated spec, a **Render** button, and a separate **Open in Live** button (re-render as often as you like; only open Live when you want to actually watch). State is in-memory per session; the current spec and render are also written to `out/ui_current.json` and `out/ui_current.als` so you can grab them from disk.

## Setup

```bash
uv sync       # installs Python 3.13, pydantic, pytest
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

| Type            | Targets                       | Required fields                                                                              |
| --------------- | ----------------------------- | -------------------------------------------------------------------------------------------- |
| `color_stab`    | RGB triples on strips & spots | `fixture`, `pixel?`, `time`, `duration`, `color: [r,g,b]`                                    |
| `color_hold`    | RGB triples on strips & spots | `fixture`, `pixel?`, `t_start`, `t_end`, `color`                                             |
| `gradient_hold` | Strip pixels (HSV interp)     | `fixture`, `t_start`, `t_end`, `hue_start`, `hue_end`                                        |
| `value_stab`    | Spot single channel           | `fixture`, `component`, `time`, `duration`, `value`                                          |
| `value_hold`    | Spot single channel           | `fixture`, `component`, `t_start`, `t_end`, `value`                                          |
| `breathe`       | RGB triple or spot channel    | `fixture`, `t_start`, `t_end`, `v_min`, `v_max`, `cycles`                                    |
| `fade`          | RGB triple or spot channel    | `fixture`, `component?`, `pixel?`, `t_start`, `t_end`, `color_start`/`color_end` or `value_start`/`value_end` |
| `ramp`          | Spot single channel           | `fixture`, `component`, `t_start`, `t_end`, `v_start`, `v_end`, `curve?` (`linear`/`ease_in`/`ease_out`) |
| `pulse_pattern` | RGB triple or spot channel    | `fixture`, `pixel?`, `component?`, `t_start`, `t_end`, `period`, `pulses`, `color?`/`value?` |
| `strobe`        | RGB triple or spot channel    | `fixture`, `pixel?`, `component?`, `t_start`, `t_end`, `rate_per_beat`, `duty?`, `color?`/`value?` |
| `chase`         | Strip pixels (pixel-by-pixel) | `fixture`, `t_start`, `step`, `duration`, `color`, `reverse?`, `period?`, `t_end?`           |
| `comet`         | Strip pixels with fading tail | `fixture`, `t_start`, `step`, `tail_beats`, `color`, `reverse?`, `period?`, `t_end?`         |
| `sparkle`       | Random pixels on a strip      | `fixture`, `t_start`, `t_end`, `density`, `duration`, `color`, `seed?`                       |

- `fixture` accepts `"*"` for "all fixtures" (filtered by event type's compatibility), a group name (`bars`, `spots`), or a single fixture name.
- `pixel` (strips) accepts an integer 1..N or `"*"` for all pixels. Default: `"*"`.
- `component` (spots) is one of `dimmer`, `red`, `green`, `blue`, `white`, `strobe`. For `breathe`/`fade`/`pulse_pattern`/`strobe`, also accepts `"rgb"` to address the color triple.
- `color_index` is Live's clip-color palette: 0..69. 1=red, 4=blue, 9=purple, etc.
- `pulse_pattern.pulses` is a list of `{offset, duration}` objects describing one cycle; the cycle repeats every `period` beats.
- `chase.period`/`t_end` (and `comet.period`/`t_end`) are paired: set both to repeat the sweep, omit both for a single pass.
- `strobe.rate_per_beat` sets the strobe rate (e.g. `16` = sixteen flashes per beat); `duty` is the on-fraction (default `0.5`).
- `sparkle.density` is approximate stabs per beat; `seed` makes output reproducible.

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

- Only the `hitmix` rig ships built-in. Adding a rig is a one-line entry in `lightgen.fixtures.RIGS`; UI for custom rigs comes in a later phase.
- High-level patterns shipped: `chase`, `comet`, `pulse_pattern`, `strobe`, `sparkle`, `fade`, `ramp`. More (e.g. sweep, wash crossfade) can be expressed by stacking primitives or described to `lightgen prompt`.
- No DMX preview, no UI yet. See the [build plan](documentation/lighting-app-plan.md) for the roadmap.
- The renderer uses the *first* MidiClip it finds in the template's DMXIS track as the clone source. If your template has no clips, add one in Live first.
