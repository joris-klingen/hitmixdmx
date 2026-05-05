# Lighting Design App — Build Plan

A standalone application that lets a user describe lighting clips in plain language (or via a UI) and exports them as Ableton Live `.als` files. Optionally drives DMX directly for live preview. Uses the Claude API to translate intent into clip patterns.

This plan is informed by ground-truth experience generating `.als` files: see `build-lighting-clips.md` for the file-format specifics that the renderer must implement.

## Goal

Replace the slow workflow of "draw envelopes by hand in Live" with a faster one: type or click what you want, see it instantly, save as a clip you can launch from Ableton's Session view. Keep DMXIS as the rendering backend so the user's existing fixture setup, dimmer curves, and patch all keep working.

## Phasing — build in order, ship MVP early

### Phase 0 — JSON-to-`.als` converter (1 weekend)

The smallest useful thing. No UI, no LLM. A CLI tool that takes a JSON spec describing clips and writes an `.als`.

**Spec format (rough sketch):**

```json
{
  "template_als": "path/to/template.als",
  "fixtures": [
    { "name": "right bar", "type": "rgb_strip", "pixels": 18, "dmx_start": 1, "orientation": "bottom_up" },
    { "name": "left bar",  "type": "rgb_strip", "pixels": 18, "dmx_start": 55, "orientation": "bottom_up" }
  ],
  "clips": [
    {
      "name": "four-on-floor red",
      "slot": 0,
      "length_beats": 4,
      "color_index": 1,
      "events": [
        { "type": "stab", "fixture": "*", "pixel": "*", "time": 0, "color": [1,0,0], "duration": 0.25 },
        { "type": "stab", "fixture": "*", "pixel": "*", "time": 1, "color": [1,0,0], "duration": 0.25 },
        ...
      ]
    }
  ]
}
```

Higher-level event types layered on top of `stab` and `hold`:
- `sweep`: linear pixel-by-pixel travel with a color and direction
- `gradient_hold`: static color gradient across a fixture
- `breathe`: sinusoidal intensity modulation
- `chase`, `comet`, `strobe`: parameterized pattern primitives

The converter expands these into the `(channel, time, value)` events that the renderer writes to envelopes. Validator runs before save.

**Why start here:** every later phase needs this same renderer. Building it without UI distractions ensures the foundation is solid. And it's already useful on its own — even hand-edited JSON is faster than drawing automation in Live.

**Deliverable:** `lightgen render spec.json out.als`

### Phase 1 — Local web UI (1–2 weeks)

A single-page app that visualizes the JSON spec and lets the user edit it. Runs locally; reads/writes `.als` files via a small Python or Node backend.

**Layout:**
- Left pane: fixture configurator (add fixture, set DMX start, pixel count, orientation)
- Center pane: timeline grid, one row per fixture group, columns are beats. Click to add events; drag to move; right-click for context menu (change to fade, change duration, etc.)
- Right pane: properties of selected event (color picker, duration slider, easing curve)
- Top toolbar: BPM, clip length, loop toggle, save-as-`.als` button

**Implementation notes:**
- Build with whatever stack you're fastest in. React + Tailwind + a Canvas or SVG timeline works. Tauri or Electron if you want desktop packaging; pure web app with `File System Access API` is simpler for a v1.
- The renderer logic from Phase 0 stays unchanged. The UI just produces the JSON spec.
- Color picker should support HSV directly — it's how lighting designers think.
- Keep undo/redo from day one (immer or zundo). It's painful to add later.
- Snap to grid (1/4, 1/8, 1/16 beat) is essential — drag-to-place without snap is unusable.

**What this gets you:** a useful tool. From here you have a working lighting designer that beats the Live UI for envelope editing.

### Phase 2 — Live DMX preview (weekend)

Render the timeline directly to DMX while the user edits, so they can see the lights without round-tripping through Ableton. This is the "wow" feature.

Three options for DMX output:
- **Art-Net / sACN** — UDP packets to a network DMX node. Most flexible. Use `python-sacn` or `node-artnet`.
- **Enttec USB DMX (Open DMX)** — direct serial, tons of cheap interfaces support it. Use `pyserial` with the Open DMX protocol (8N2, 250000 baud, BREAK, MAB, 513 bytes).
- **Loopback to DMXIS** — virtual MIDI port, plugin sends to lights. Possible but Hacky.

A simple play head moves through the spec at the current BPM, computes the value of every channel at that instant by walking the events, and sends the resulting 512-byte universe at 30+ Hz.

For most users, Art-Net is the right default — it works with anything and doesn't require a USB device.

### Phase 3 — Claude API integration (1 week)

The pitch: "describe the lights you want; the app generates a clip."

**Architecture:**
- User types a prompt: *"4 bars of slow blue breathing on the side bars, then a white strobe on the last beat"*
- App sends the prompt + a system prompt explaining the JSON spec format + the fixture configuration to the Claude API
- Claude returns a JSON spec
- App validates the spec, expands to events, runs the renderer, shows the result in the timeline + DMX preview
- User can edit the result manually or refine via follow-up prompt

**System prompt structure:**
- Schema description (the JSON spec from Phase 0, with strict types)
- Fixture configuration (the user's actual fixtures, in the prompt every time)
- Pattern vocabulary (definitions of stab, hold, sweep, breathe, chase, etc.)
- Examples (several pairs of natural language → JSON spec)
- Constraint reminders (loop length must be a positive number of beats; values 0..1; etc.)

**Use structured output / JSON mode** — saves a class of parsing bugs.

**Key pattern**: keep the LLM out of the rendering pipeline. Claude generates *spec*, not events. The deterministic renderer turns spec into events. This means:
- LLM hallucinations affect the spec but can't produce invalid `.als` files (the renderer + validator catch them)
- Tweaking the renderer doesn't require re-prompting
- Users can hand-edit the spec to correct the LLM
- You can swap LLMs later without touching the rendering code

**Iteration loop:** after generation, the user can say *"make it twice as slow"* or *"add a red flash on beat 3"* and the app sends the current spec + the diff request to Claude. Claude returns a modified spec. Show a visual diff before applying.

### Phase 4 — Pattern library and presets (ongoing)

Curated patterns that can be parameterized:
- "Drop" template (8-bar build → impact)
- "Breakdown" template (sparse → dense → sparse)
- Genre-specific palettes (techno, drum and bass, house, ambient)

Users can save their own patterns, parameterize them, and reuse. This is also the natural place to add LLM-generated suggestions ("Claude, give me 5 variations of this clip").

### Phase 5 — Multi-clip arrangements

Sequence clips into a full set. Tempo-locked transitions between clips. Crossfade options. Export as a single long `.als` arrangement instead of separate Session view clips.

This unlocks the "design a whole show in 30 minutes" use case but requires careful UI work to avoid feeling like a worse version of Ableton.

## Architecture decisions, with rationale

### Spec → events → `.als` is a 3-stage pipeline

Don't collapse stages. Each stage has a different failure mode:

1. **Spec** is human-readable, LLM-writable, declarative
2. **Events** are flat, channel-time-value triples — easy to validate, simulate, render to DMX
3. **`.als`** is the gnarly XML output

Bug in the spec? Inspect JSON. Bug in event expansion? Run the simulator. Bug in `.als` output? Re-run with the validator. Each stage is independently testable.

### Use the file-format invariants as a hard validator

From `build-lighting-clips.md`:
- Times monotone, no event past clip end
- Values in [0, 1]
- All envelope targets reference configured plugin parameters
- Scene/slot Id alignment

The renderer enforces these unconditionally before writing the file. If the validator fails, do not write the file — fix the events. Live will crash on violations, and a crashed `.als` is much harder to debug than a refused render.

### Treat the user's `.als` template as authoritative

The user's existing project has a DMXIS plugin instance with their channel configuration, a tempo, an audio track for the music, and (sometimes) other tracks they care about. The app should **not** generate `.als` files from scratch. Always start from a user-provided template, modify in place (specifically: replace clips in slots), and save. This means:
- Plugin state is preserved (DMXIS channel mapping, custom dimmer curves)
- Audio routing is preserved
- The user's existing clips can be kept or overwritten as the user chooses

The app's "create new project" feature should ship a known-good template, not generate XML.

### Fixture model — keep it simple, stay extensible

Start with these fixture types:
- `rgb_strip` (N pixels × 3 channels, with orientation flag)
- `rgbw_strip` (N pixels × 4 channels)
- `single_channel` (a dimmer, a strobe, a pan or tilt)
- `moving_head` (named channels: pan, tilt, dimmer, color, gobo, etc.)

Each fixture has a name, a DMX start address, and a type-specific schema. The pattern primitives operate on fixtures and pixels by name; the renderer handles the address arithmetic.

Don't try to build a full DMX fixture profile editor in v1. Hard-code the four types above and let users specify in JSON. A full profile editor is a project unto itself (see QLC+, Avolites Titan).

### Don't reinvent DMXIS

The app generates clips that drive DMXIS, not raw DMX (unless in preview mode). DMXIS handles:
- Channel patching to physical fixtures
- Per-fixture dimmer curves
- Master fader / blackout
- Custom presets

The app's job is to **make clips that DMXIS can play** — not to replace DMXIS. This is the right scope for v1: it leverages a tool the user already has and trusts, instead of competing with the entire lighting-software ecosystem.

## Tech stack recommendation

- **Backend:** Python. The `.als` manipulation is already in Python. `python-sacn` or `pyserial` for DMX. FastAPI to expose to the frontend.
- **Frontend:** React + TypeScript + Tailwind. Canvas-based timeline for performance.
- **Packaging:** Tauri (Rust shell, web frontend). Smaller and faster than Electron.
- **LLM:** Anthropic Claude API. Use Sonnet for speed/cost; bump to Opus for tricky generations if needed. Pass the JSON schema, fixture config, and pattern vocabulary in the system prompt.

If you'd rather stay JS-only, port the `.als` generator to TypeScript. The XML manipulation is straightforward. `pako` for gzip in-browser. Then the whole thing can run as a static SPA + a tiny WebSocket server for DMX output.

## What to build first when you sit down

1. Take the existing Python `.als` generator (the one we built across this conversation) and refactor it into three modules:
   - `als_io.py` — gzip + XML in/out, template loading, validator
   - `events.py` — pattern primitives (stab, hold, sweep, breathe, etc.) that emit `(channel, time, value)` tuples
   - `renderer.py` — takes a JSON spec, calls `events.py` primitives, writes `.als` via `als_io.py`
2. Define the JSON schema as a Pydantic model. Validate on input.
3. Write 10 hand-authored JSON spec files reproducing the clips already built in this conversation. They become regression tests.
4. CLI: `lightgen render spec.json template.als out.als`. Done — Phase 0 shipped.

After that, layer on the UI, then DMX preview, then LLM integration. Each stage is independently shippable.

## Open questions / decisions to make

- **Project state.** Single-file (everything in JSON) or library + project (fixtures saved separately, projects reference them)? Single-file is simpler; library is what users will eventually want.
- **Tempo/time signature.** Hard-code 4/4, or support arbitrary signatures? 4/4 covers 95% of dance music; broader support is a tar pit.
- **Versioning.** The JSON spec format will evolve. Add a `version` field from day one and write migrators when it changes.
- **Sharing clips.** Will users want to share patterns? If yes, the JSON spec should be self-contained (fixture config bundled with clips so a recipient can render).
- **MIDI clock sync.** Does the live preview sync to incoming MIDI clock from Ableton? This is the difference between "useful for design" and "useful at gigs."

## Estimated timeline (weekday-equivalent)

- Phase 0 (JSON → `.als`): 2–3 days
- Phase 1 (web UI): 7–10 days
- Phase 2 (DMX preview): 3 days
- Phase 3 (Claude integration): 5 days
- Phase 4 (pattern library): ongoing
- Phase 5 (arrangements): 5–10 days

Realistic milestone for a working v1 (Phases 0–3): 3–4 weeks for someone working on this full-time, more like 2–3 months part-time.

## What this app would *not* be

Not a replacement for full lighting consoles (Avolites, MA, ChamSys). Those are designed for live performance with dozens of fixtures and complex timing. This app is for **DJs and producers who want their light show authored alongside their music in Ableton** — a smaller, more specific niche, but a real one.
