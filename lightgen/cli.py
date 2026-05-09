"""Command-line interface for lightgen.

Commands:
    lightgen render <spec.json> <template.als> <out.als>
    lightgen inspect <template.als>
    lightgen prompt "<text>" <out_spec.json>
    lightgen tweak <spec.json> "<text>" [--out <new.json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .als_io import load_template, save
from .renderer import render
from .spec import Spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lightgen")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser(
        "render", help="render a spec into an .als using a template"
    )
    p_render.add_argument("spec", type=Path, help="path to the JSON spec")
    p_render.add_argument("template", type=Path, help="path to the template .als")
    p_render.add_argument("out", type=Path, help="path to write the output .als")
    p_render.add_argument(
        "--clean",
        action="store_true",
        help="clear any clip slots not referenced by the spec",
    )

    p_inspect = sub.add_parser("inspect", help="introspect an .als template")
    p_inspect.add_argument("template", type=Path)

    p_prompt = sub.add_parser(
        "prompt", help="generate a spec from a natural-language prompt via Claude"
    )
    p_prompt.add_argument("text", help="what you want, in plain English")
    p_prompt.add_argument("out", type=Path, help="path to write the spec JSON")
    p_prompt.add_argument("--model", default=None, help="override the Claude model id")

    p_tweak = sub.add_parser(
        "tweak", help="modify an existing spec via Claude (in-place by default)"
    )
    p_tweak.add_argument("spec", type=Path, help="path to the existing spec JSON")
    p_tweak.add_argument("text", help="the change you want, in plain English")
    p_tweak.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write to this path instead of overwriting the input",
    )
    p_tweak.add_argument("--model", default=None, help="override the Claude model id")

    args = parser.parse_args(argv)

    if args.cmd == "render":
        return _cmd_render(args.spec, args.template, args.out, args.clean)
    if args.cmd == "inspect":
        return _cmd_inspect(args.template)
    if args.cmd == "prompt":
        return _cmd_prompt(args.text, args.out, args.model)
    if args.cmd == "tweak":
        return _cmd_tweak(args.spec, args.text, args.out, args.model)
    parser.error(f"unknown command {args.cmd}")
    return 2


def _cmd_render(spec_path: Path, template_path: Path, out_path: Path, clean: bool) -> int:
    raw = json.loads(spec_path.read_text())
    spec = Spec.model_validate(raw)
    template = load_template(template_path)

    n_clips = len(spec.clips)
    print(
        f"Loaded template: {template.plugin.channel_count} channels, "
        f"{template.scene_count} scenes, {len(template.clip_slots)} slots"
    )
    suffix = " (clearing unused slots)" if clean else ""
    print(f"Rendering {n_clips} clip(s) from {spec_path.name}{suffix}")
    render(spec, template, clean=clean)
    save(template, out_path)
    print(f"Wrote {out_path}")
    return 0


def _cmd_inspect(template_path: Path) -> int:
    template = load_template(template_path)
    p = template.plugin
    print(f"Template: {template_path}")
    print(f"  channels configured: {p.channel_count}")
    print(f"  at_id base:          {p.base_at_id}")
    print(f"  at_id stride:        {p.stride}")
    print(f"  scenes:              {template.scene_count}")
    print(f"  clip slots:          {len(template.clip_slots)}")
    print(f"  next_pointee_id:     {template.next_pointee_id}")
    return 0


def _cmd_prompt(text: str, out_path: Path, model: str | None) -> int:
    from .llm import generate_spec

    print(f"Asking Claude{f' ({model})' if model else ''} for a spec…", file=sys.stderr)
    try:
        spec = generate_spec(text, model=model)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out_path.write_text(spec.model_dump_json(indent=2) + "\n")
    print(f"Wrote {out_path} ({len(spec.clips)} clip(s))")
    return 0


def _cmd_tweak(spec_path: Path, text: str, out_path: Path | None, model: str | None) -> int:
    from .llm import generate_spec

    raw = json.loads(spec_path.read_text())
    base = Spec.model_validate(raw)
    target = out_path or spec_path
    print(
        f"Asking Claude{f' ({model})' if model else ''} to tweak {spec_path.name} → {target.name}…",
        file=sys.stderr,
    )
    try:
        spec = generate_spec(text, base_spec=base, model=model)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    target.write_text(spec.model_dump_json(indent=2) + "\n")
    print(f"Wrote {target} ({len(spec.clips)} clip(s))")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
