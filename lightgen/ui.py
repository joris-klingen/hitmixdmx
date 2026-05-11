"""Gradio web UI for LLM-driven lightshow generation.

Launch with:
    uv run lightgen ui

A localhost server starts at http://127.0.0.1:7860 with a chat-style interface:
prompt → spec → render → open in Live → tweak → render again.

Requires the optional `ui` dependency group: `uv sync --extra ui`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .als_io import load_template, save
from .llm import generate_spec
from .renderer import render
from .spec import Spec

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = (
    REPO_ROOT / "template/claude_lights_start Project/claude_lights_start.als"
)
OUT_DIR = REPO_ROOT / "out"
SPEC_PATH = OUT_DIR / "ui_current.json"
ALS_PATH = OUT_DIR / "ui_current.als"


def _resolve_model(choice: str) -> str | None:
    return None if choice == "(default)" else choice


def _generate(prompt: str, model_choice: str):
    """Generate a fresh spec from English. Returns (spec_json, status)."""
    if not prompt.strip():
        return "", "Type a prompt first."
    try:
        spec = generate_spec(prompt, model=_resolve_model(model_choice))
    except RuntimeError as e:
        return "", f"❌ {e}"
    OUT_DIR.mkdir(exist_ok=True)
    spec_json = spec.model_dump_json(indent=2)
    SPEC_PATH.write_text(spec_json + "\n")
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
    OUT_DIR.mkdir(exist_ok=True)
    spec_json = spec.model_dump_json(indent=2)
    SPEC_PATH.write_text(spec_json + "\n")
    return (
        spec_json,
        f"✅ Tweaked · {len(spec.clips)} clip(s)",
    )


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
            "_LLM-driven lightshow generator for DMXIS-in-Live._  "
            "Type a prompt, render, watch the rig. Tweak in English."
        )

        with gr.Row():
            with gr.Column(scale=2):
                prompt_in = gr.Textbox(
                    label="Describe the show",
                    placeholder=(
                        "e.g. 10 clips for a rock band: slow color hold, "
                        "four-on-floor stab, building ramp into a strobe drop, "
                        "comet sweep, sparkle texture, breathing pad, "
                        "rainbow gradient, fast chase, heartbeat pulse_pattern, "
                        "fade between two colors. 4-16 beats each. Pair spot "
                        "color events with dimmer."
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

                gr.Markdown("---")

                template_in = gr.Textbox(
                    label="Template .als",
                    value=str(DEFAULT_TEMPLATE.relative_to(REPO_ROOT)),
                    lines=1,
                )
                with gr.Row():
                    render_btn = gr.Button("Render", variant="primary")
                    open_btn = gr.Button("Open in Live")

                status_out = gr.Textbox(
                    label="Status",
                    interactive=False,
                    lines=2,
                )

            with gr.Column(scale=3):
                spec_view = gr.Code(
                    label="Spec JSON",
                    language="json",
                    lines=30,
                )

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
