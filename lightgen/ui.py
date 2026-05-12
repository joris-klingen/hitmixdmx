"""Gradio web UI for LLM-driven and deterministic lightshow generation.

Launch with:
    uv run lightgen ui

A localhost server starts at http://127.0.0.1:7860 with two tabs:
- Prompt: LLM-driven prompt → spec → tweak loop.
- Patterns: parametric recipes (four_on_floor, breathing, chase, wash, sparkle)
  with active-beat selection.

Both tabs write to the same shared spec view; Render and Open in Live operate
on whatever spec is currently shown.

Requires the optional `ui` dependency group: `uv sync --extra ui`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .als_io import load_template, save
from .fixtures import HITMIX_RIG
from .llm import generate_spec
from .patterns import (
    breathing,
    chase,
    four_on_floor,
    parse_beats,
    sparkle,
    wash,
)
from .renderer import render
from .spec import Clip, Event, Spec
from .visualizer import render_strip

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = (
    REPO_ROOT / "template/claude_lights_start Project/claude_lights_start.als"
)
OUT_DIR = REPO_ROOT / "out"
SPEC_PATH = OUT_DIR / "ui_current.json"
ALS_PATH = OUT_DIR / "ui_current.als"


WHERE_CHOICES = [
    "*",
    "bars",
    "spots",
    "left_bar",
    "right_bar",
    "singer_left",
    "singer_right",
]

LIVE_COLOR_CHOICES = [
    ("red (1)", 1),
    ("orange (2)", 2),
    ("yellow (4)", 4),
    ("green (5)", 5),
    ("cyan (8)", 8),
    ("blue (9)", 9),
    ("purple (14)", 14),
    ("magenta (16)", 16),
    ("white (0)", 0),
]


def _resolve_model(choice: str) -> str | None:
    return None if choice == "(default)" else choice


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert '#rrggbb' (or 'rgba(...)') to (r, g, b) floats in [0, 1]."""
    s = hex_color.strip()
    if s.startswith("rgba(") or s.startswith("rgb("):
        inner = s[s.find("(") + 1 : s.rfind(")")]
        parts = [p.strip() for p in inner.split(",")]
        r = float(parts[0]) / 255
        g = float(parts[1]) / 255
        b = float(parts[2]) / 255
        return (r, g, b)
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    return (r, g, b)


def _save_spec(spec: Spec) -> str:
    OUT_DIR.mkdir(exist_ok=True)
    spec_json = spec.model_dump_json(indent=2)
    SPEC_PATH.write_text(spec_json + "\n")
    return spec_json


def _generate(prompt: str, model_choice: str):
    """Generate a fresh spec from English. Returns (spec_json, status)."""
    if not prompt.strip():
        return "", "Type a prompt first."
    try:
        spec = generate_spec(prompt, model=_resolve_model(model_choice))
    except RuntimeError as e:
        return "", f"❌ {e}"
    spec_json = _save_spec(spec)
    return (
        spec_json,
        f"✅ Generated {len(spec.clips)} clip(s) · saved to {SPEC_PATH.relative_to(REPO_ROOT)}",
    )


def _tweak(tweak_prompt: str, model_choice: str, current_spec_json: str):
    """Modify the current spec via a follow-up prompt."""
    if not current_spec_json.strip():
        return current_spec_json, "Generate a spec first, then tweak."
    if not tweak_prompt.strip():
        return current_spec_json, "Type a tweak first."
    try:
        base = Spec.model_validate_json(current_spec_json)
    except Exception as e:
        return current_spec_json, f"❌ Current spec failed to parse: {e}"
    try:
        spec = generate_spec(
            tweak_prompt, base_spec=base, model=_resolve_model(model_choice)
        )
    except RuntimeError as e:
        return current_spec_json, f"❌ {e}"
    spec_json = _save_spec(spec)
    return spec_json, f"✅ Tweaked · {len(spec.clips)} clip(s)"


def _build_pattern_events(
    pattern: str,
    where: str,
    color_hex: str,
    length_beats: float,
    beats_text: str,
    stab_duration: float,
    cycles: int,
    v_min: float,
    v_max: float,
    chase_step: float,
    chase_duration: float,
    chase_period: float,
    chase_reverse: bool,
    sparkle_density: float,
    sparkle_duration: float,
    sparkle_seed: int,
) -> list[Event]:
    color = _hex_to_rgb(color_hex)
    beats = parse_beats(beats_text)
    if pattern == "four_on_floor":
        return four_on_floor(
            where, color, length_beats,
            active_beats=beats, stab_duration=stab_duration,
        )
    if pattern == "breathing":
        return breathing(
            where, color, length_beats,
            active_beats=beats, cycles=int(cycles), v_min=v_min, v_max=v_max,
        )
    if pattern == "wash":
        return wash(where, color, length_beats, active_beats=beats)
    if pattern == "chase":
        return chase(
            where, color, length_beats,
            active_beats=beats, step=chase_step, duration=chase_duration,
            period=chase_period, reverse=chase_reverse,
        )
    if pattern == "sparkle":
        return sparkle(
            where, color, length_beats,
            active_beats=beats, density=sparkle_density,
            duration=sparkle_duration, seed=int(sparkle_seed),
        )
    raise ValueError(f"unknown pattern: {pattern}")


def _pattern_build(
    pattern, where, color_hex, length_beats, beats_text,
    slot, color_index, clip_name,
    stab_duration, cycles, v_min, v_max,
    chase_step, chase_duration, chase_period, chase_reverse,
    sparkle_density, sparkle_duration, sparkle_seed,
):
    """Build a fresh spec containing one clip with the pattern's events."""
    try:
        events = _build_pattern_events(
            pattern, where, color_hex, length_beats, beats_text,
            stab_duration, cycles, v_min, v_max,
            chase_step, chase_duration, chase_period, chase_reverse,
            sparkle_density, sparkle_duration, sparkle_seed,
        )
        spec = Spec(
            clips=[
                Clip(
                    name=clip_name or pattern,
                    slot=int(slot),
                    length_beats=float(length_beats),
                    color_index=int(color_index),
                    events=events,
                )
            ]
        )
    except Exception as e:
        return "", f"❌ {e}"
    spec_json = _save_spec(spec)
    return spec_json, f"✅ Built {pattern} · 1 clip · {len(events)} event(s)"


def _pattern_add_layer(
    current_spec_json,
    pattern, where, color_hex, length_beats, beats_text,
    slot, color_index, clip_name,
    stab_duration, cycles, v_min, v_max,
    chase_step, chase_duration, chase_period, chase_reverse,
    sparkle_density, sparkle_duration, sparkle_seed,
):
    """Append the pattern's events to the clip at `slot` (creates one if missing)."""
    try:
        if current_spec_json.strip():
            spec = Spec.model_validate_json(current_spec_json)
        else:
            spec = Spec(clips=[])
        events = _build_pattern_events(
            pattern, where, color_hex, length_beats, beats_text,
            stab_duration, cycles, v_min, v_max,
            chase_step, chase_duration, chase_period, chase_reverse,
            sparkle_density, sparkle_duration, sparkle_seed,
        )
        target = next((c for c in spec.clips if c.slot == int(slot)), None)
        if target is None:
            target = Clip(
                name=clip_name or pattern,
                slot=int(slot),
                length_beats=float(length_beats),
                color_index=int(color_index),
                events=[],
            )
            spec.clips.append(target)
        target.events.extend(events)
    except Exception as e:
        return current_spec_json, f"❌ {e}"
    spec_json = _save_spec(spec)
    return (
        spec_json,
        f"✅ Added {pattern} to slot {slot} · {len(events)} event(s) · total {len(target.events)} in clip",
    )


def _clear_spec():
    return "", "Cleared spec."


def _render(current_spec_json: str, template_path: str):
    """Render the on-screen spec to .als."""
    if not current_spec_json.strip():
        return "No spec to render."
    try:
        spec = Spec.model_validate_json(current_spec_json)
    except Exception as e:
        return f"❌ Spec parse error: {e}"
    template_file = Path(template_path)
    if not template_file.exists():
        return f"❌ Template not found: {template_file}"
    try:
        template = load_template(template_file)
        render(spec, template)
        OUT_DIR.mkdir(exist_ok=True)
        save(template, ALS_PATH)
    except (ValueError, RuntimeError) as e:
        return f"❌ Render error: {e}"
    return f"✅ Rendered to {ALS_PATH.relative_to(REPO_ROOT)}"


def _preview(current_spec_json: str):
    """Render 4 evenly-spaced snapshots of the first clip. Returns (gallery, status)."""
    if not current_spec_json.strip():
        return [], "No spec to preview."
    try:
        spec = Spec.model_validate_json(current_spec_json)
    except Exception as e:
        return [], f"❌ Spec parse error: {e}"
    if not spec.clips:
        return [], "Spec has no clips."
    clip = spec.clips[0]
    length = float(clip.length_beats)
    times = [i * length / 4 for i in range(4)]
    frames = render_strip(spec, times)
    return (
        frames,
        f"✅ Previewed clip 0 ({clip.name!r}, {length}b) at t = {', '.join(f'{t:.2f}' for t in times)}",
    )


def _open_in_live():
    """Open the last rendered .als in Live."""
    if not ALS_PATH.exists():
        return f"❌ Nothing to open — {ALS_PATH.relative_to(REPO_ROOT)} doesn't exist yet. Render first."
    try:
        subprocess.run(["open", str(ALS_PATH)], check=False)
    except FileNotFoundError:
        return (
            f"❌ `open` not on PATH — open {ALS_PATH.relative_to(REPO_ROOT)} "
            "in Live manually."
        )
    return f"✅ Opened {ALS_PATH.relative_to(REPO_ROOT)} in Live."


def build_app():
    """Build and return the Gradio Blocks app."""
    try:
        import gradio as gr
    except ImportError as e:
        raise RuntimeError(
            "gradio is required to launch the UI. Install with: "
            "`uv sync --extra ui` (or `pip install gradio`)."
        ) from e

    with gr.Blocks(title="lightgen") as app:
        gr.Markdown(
            "# lightgen\n"
            "_Lightshow generator for DMXIS-in-Live._  "
            "Use **Prompt** to describe in English, or **Patterns** to build deterministically."
        )

        with gr.Row():
            with gr.Column(scale=2):
                with gr.Tabs():
                    # --- PROMPT TAB ---
                    with gr.TabItem("Prompt"):
                        prompt_in = gr.Textbox(
                            label="Describe the show",
                            placeholder=(
                                "e.g. 10 clips for a rock band: slow color hold, "
                                "four-on-floor stab, building ramp into a strobe drop, "
                                "comet sweep, sparkle texture, breathing pad..."
                            ),
                            lines=5,
                        )
                        model_in = gr.Dropdown(
                            choices=["(default)", "sonnet", "opus"],
                            value="(default)",
                            label="Claude model",
                        )
                        gen_btn = gr.Button("Generate", variant="primary")

                        gr.Markdown("---")

                        tweak_in = gr.Textbox(
                            label="Tweak the current spec",
                            placeholder="e.g. make the chase reverse and twice as fast",
                            lines=3,
                        )
                        tweak_btn = gr.Button("Tweak")

                    # --- PATTERNS TAB ---
                    with gr.TabItem("Patterns"):
                        with gr.Row():
                            pattern_in = gr.Dropdown(
                                choices=[
                                    "four_on_floor", "breathing", "wash",
                                    "chase", "sparkle",
                                ],
                                value="four_on_floor",
                                label="Pattern",
                            )
                            where_in = gr.Dropdown(
                                choices=WHERE_CHOICES, value="*", label="Where"
                            )
                        with gr.Row():
                            color_in = gr.ColorPicker(value="#ff0000", label="Color")
                            length_in = gr.Number(value=4, label="Length (beats)", precision=2)
                        beats_in = gr.Textbox(
                            label="Active beats (1-based, e.g. '1,3' or '1-2,4'; blank = all)",
                            value="",
                            lines=1,
                        )
                        with gr.Row():
                            slot_in = gr.Number(value=0, label="Slot", precision=0)
                            color_index_in = gr.Dropdown(
                                choices=LIVE_COLOR_CHOICES, value=1, label="Live clip color"
                            )
                            name_in = gr.Textbox(label="Clip name", value="")

                        with gr.Accordion("Pattern parameters", open=False):
                            gr.Markdown("_Only the params for the selected pattern are used._")
                            stab_duration_in = gr.Number(
                                value=0.25, label="four_on_floor: stab duration (beats)", precision=3
                            )
                            with gr.Row():
                                cycles_in = gr.Number(value=1, label="breathing: cycles", precision=0)
                                vmin_in = gr.Number(value=0.05, label="breathing: v_min", precision=3)
                                vmax_in = gr.Number(value=1.0, label="breathing: v_max", precision=3)
                            with gr.Row():
                                chase_step_in = gr.Number(value=0.25, label="chase: step (beats)", precision=3)
                                chase_dur_in = gr.Number(value=0.25, label="chase: stab duration", precision=3)
                                chase_period_in = gr.Number(value=1.0, label="chase: period", precision=3)
                                chase_rev_in = gr.Checkbox(value=False, label="chase: reverse")
                            with gr.Row():
                                sparkle_density_in = gr.Number(value=4.0, label="sparkle: density (per beat)", precision=2)
                                sparkle_dur_in = gr.Number(value=0.1, label="sparkle: duration", precision=3)
                                sparkle_seed_in = gr.Number(value=0, label="sparkle: seed", precision=0)

                        with gr.Row():
                            build_btn = gr.Button("Build (replace spec)", variant="primary")
                            add_btn = gr.Button("+ Add layer to slot")
                            clear_btn = gr.Button("Clear spec")

                gr.Markdown("---")

                template_in = gr.Textbox(
                    label="Template .als",
                    value=str(DEFAULT_TEMPLATE.relative_to(REPO_ROOT)),
                    lines=1,
                )
                with gr.Row():
                    render_btn = gr.Button("Render", variant="primary")
                    open_btn = gr.Button("Open in Live")
                    preview_btn = gr.Button("Preview")

                status_out = gr.Textbox(
                    label="Status",
                    interactive=False,
                    lines=2,
                )

            with gr.Column(scale=3):
                preview_gallery = gr.Gallery(
                    label="Preview (clip 0, 4 frames)",
                    columns=4,
                    rows=1,
                    height=260,
                    show_label=True,
                    object_fit="contain",
                )
                spec_view = gr.Code(
                    label="Spec JSON",
                    language="json",
                    lines=24,
                )

        # --- Wire prompt tab ---
        gen_btn.click(
            _generate,
            inputs=[prompt_in, model_in],
            outputs=[spec_view, status_out],
        )
        tweak_btn.click(
            _tweak,
            inputs=[tweak_in, model_in, spec_view],
            outputs=[spec_view, status_out],
        )

        # --- Wire patterns tab ---
        pattern_inputs = [
            pattern_in, where_in, color_in, length_in, beats_in,
            slot_in, color_index_in, name_in,
            stab_duration_in,
            cycles_in, vmin_in, vmax_in,
            chase_step_in, chase_dur_in, chase_period_in, chase_rev_in,
            sparkle_density_in, sparkle_dur_in, sparkle_seed_in,
        ]
        build_btn.click(
            _pattern_build,
            inputs=pattern_inputs,
            outputs=[spec_view, status_out],
        )
        add_btn.click(
            _pattern_add_layer,
            inputs=[spec_view] + pattern_inputs,
            outputs=[spec_view, status_out],
        )
        clear_btn.click(
            _clear_spec,
            inputs=[],
            outputs=[spec_view, status_out],
        )

        # --- Shared render/open ---
        render_btn.click(
            _render,
            inputs=[spec_view, template_in],
            outputs=[status_out],
        )
        open_btn.click(
            _open_in_live,
            inputs=[],
            outputs=[status_out],
        )
        preview_btn.click(
            _preview,
            inputs=[spec_view],
            outputs=[preview_gallery, status_out],
        )

    return app


def main() -> int:
    try:
        import gradio as gr

        app = build_app()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    app.launch(inbrowser=True, theme=gr.themes.Soft())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
