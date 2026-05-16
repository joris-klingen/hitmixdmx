"""Command-line interface for lightgen.

Commands:
    lightgen render <spec.json> <template.als> <out.als>
    lightgen inspect <template.als>
    lightgen prompt "<text>" <out_spec.json>
    lightgen tweak <spec.json> "<text>" [--out <new.json>]
    lightgen convert-legacy <source.als> <template.als> <out.als> [--limit N]
    lightgen ui
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

    sub.add_parser(
        "ui",
        help="launch the Gradio web UI (requires `uv sync --extra ui`)",
    )

    p_conv = sub.add_parser(
        "convert-legacy",
        help="read clips from a macro-driven legacy .als and render them as pixel DMX",
    )
    p_conv.add_argument("source", type=Path, help="legacy .als with the macro track")
    p_conv.add_argument("template", type=Path, help="DMXIS template .als to render into")
    p_conv.add_argument("out", type=Path, help="output .als path")
    p_conv.add_argument(
        "--limit", type=int, default=10, help="only convert the first N populated clips (default 10)"
    )
    p_conv.add_argument(
        "--track-index",
        type=int,
        default=0,
        help="which track in the source to read clips from (default 0 = first)",
    )
    p_conv.add_argument(
        "--target-track",
        type=int,
        default=None,
        help="which track in the template to render into (default: first "
        "MidiTrack with a plugin). Use this when rendering into a multi-track "
        "destination — e.g. the source itself, targeting its DMX_pixel track.",
    )
    p_conv.add_argument(
        "--strip-source-track",
        action="store_true",
        help="after rendering, remove the source-read track from the output. "
        "Useful when using the source `.als` as the template — gives a clean "
        "single-track file at the same slot grid for paste-back.",
    )
    p_conv.add_argument(
        "--save-spec",
        type=Path,
        default=None,
        help="also write the intermediate JSON spec to this path",
    )

    args = parser.parse_args(argv)

    if args.cmd == "render":
        return _cmd_render(args.spec, args.template, args.out, args.clean)
    if args.cmd == "inspect":
        return _cmd_inspect(args.template)
    if args.cmd == "prompt":
        return _cmd_prompt(args.text, args.out, args.model)
    if args.cmd == "tweak":
        return _cmd_tweak(args.spec, args.text, args.out, args.model)
    if args.cmd == "ui":
        return _cmd_ui()
    if args.cmd == "convert-legacy":
        return _cmd_convert_legacy(
            args.source,
            args.template,
            args.out,
            args.limit,
            args.track_index,
            args.save_spec,
            args.target_track,
            args.strip_source_track,
        )
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


def _cmd_ui() -> int:
    from .ui import main as ui_main

    return ui_main()


def _cmd_convert_legacy(
    source: Path,
    template_path: Path,
    out_path: Path,
    limit: int,
    track_index: int,
    save_spec: Path | None,
    target_track: int | None,
    strip_source_track: bool,
) -> int:
    from .legacy_convert import convert_to_spec, patch_clip_properties, read_legacy_clips

    print(f"Reading legacy clips from {source} (track {track_index})…")
    legacy = read_legacy_clips(source, track_index=track_index)
    print(f"  found {len(legacy)} populated clips; converting first {min(limit, len(legacy))}")
    selected = legacy[:limit]
    spec = convert_to_spec(selected)

    if save_spec is not None:
        save_spec.write_text(spec.model_dump_json(indent=2) + "\n")
        print(f"  wrote intermediate spec → {save_spec}")

    template = load_template(template_path, track_index=target_track)
    target_label = (
        f"track {target_track}" if target_track is not None else "first plugin-bearing track"
    )
    print(
        f"Loaded template ({target_label}): {template.plugin.channel_count} channels, "
        f"{template.scene_count} scenes, {len(template.clip_slots)} slots"
    )
    render(spec, template)
    patch_clip_properties(template, selected)

    validate_idx = target_track
    if strip_source_track:
        tracks_el = template.root.find("LiveSet/Tracks")
        tracks = list(tracks_el)
        source_track_el = tracks[track_index]
        target_track_el = tracks[target_track] if target_track is not None else None
        tracks_el.remove(source_track_el)
        print(f"Stripped source track at index {track_index} from output")
        if target_track_el is not None:
            # Track indices shift down by 1 if the source track came before the target.
            validate_idx = list(tracks_el).index(target_track_el)

    # When rendering into a multi-track destination, only validate the target
    # track and tolerate the pre-existing slot/scene Id mismatches that Live
    # itself accepts.
    save_kwargs: dict = {"tolerate": ("slot/scene Id mismatch",)}
    if validate_idx is not None:
        save_kwargs["validate_track_indices"] = {validate_idx}
    save(template, out_path, **save_kwargs)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
