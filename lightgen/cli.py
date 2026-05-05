"""Command-line interface for lightgen.

Usage:
    lightgen render <spec.json> <template.als> <out.als>
    lightgen inspect <template.als>
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

    p_inspect = sub.add_parser("inspect", help="introspect an .als template")
    p_inspect.add_argument("template", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "render":
        return _cmd_render(args.spec, args.template, args.out)
    if args.cmd == "inspect":
        return _cmd_inspect(args.template)
    parser.error(f"unknown command {args.cmd}")
    return 2


def _cmd_render(spec_path: Path, template_path: Path, out_path: Path) -> int:
    raw = json.loads(spec_path.read_text())
    spec = Spec.model_validate(raw)
    template = load_template(template_path)

    n_clips = len(spec.clips)
    print(
        f"Loaded template: {template.plugin.channel_count} channels, "
        f"{template.scene_count} scenes, {len(template.clip_slots)} slots"
    )
    print(f"Rendering {n_clips} clip(s) from {spec_path.name}")
    render(spec, template)
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
