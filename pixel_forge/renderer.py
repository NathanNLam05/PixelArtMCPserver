"""Declarative JSON sprite specification parser, validator, and CLI runner."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .canvas import FrameExport, PixelCanvas, _require_int, export_sprite_sheet, validate_sprite_name

SPRITE_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PixelForgeSpriteSpec",
    "type": "object",
    "required": ["name", "frames"],
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string",
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]*$",
            "description": "Output sprite sheet base name (produces <name>.png, <name>_preview.png, <name>.json).",
        },
        "width": {
            "type": "integer",
            "minimum": 1,
            "default": 128,
            "description": "Frame width in pixels.",
        },
        "height": {
            "type": "integer",
            "minimum": 1,
            "default": 128,
            "description": "Frame height in pixels.",
        },
        "palette": {
            "type": "object",
            "additionalProperties": {
                "type": "string",
                "pattern": "^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$",
            },
            "description": "Mapping of named colors to #RRGGBB or #RRGGBBAA hex strings. 'transparent' is always included.",
        },
        "frames": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["commands"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional animation frame label (e.g., 'idle', 'punch').",
                    },
                    "commands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["op"],
                            "description": "Drawing operation command object.",
                        },
                    },
                    "outline_color": {
                        "type": ["string", "null"],
                        "description": "Optional palette color or hex string applied via add_outline() after all commands.",
                    },
                    "hurtboxes": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "prefixItems": [
                                {"type": "integer"},
                                {"type": "integer"},
                                {"type": "integer", "minimum": 1},
                                {"type": "integer", "minimum": 1},
                            ],
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "description": "Optional [x, y, w, h] vulnerable body boxes for this frame.",
                    },
                    "hitboxes": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "prefixItems": [
                                {"type": "integer"},
                                {"type": "integer"},
                                {"type": "integer", "minimum": 1},
                                {"type": "integer", "minimum": 1},
                            ],
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "description": "Optional [x, y, w, h] active attack boxes for this frame.",
                    },
                },
            },
        },
    },
}

_ALLOWED_SPEC_KEYS = frozenset({"name", "width", "height", "palette", "frames"})
_ALLOWED_FRAME_KEYS = frozenset({"name", "commands", "outline_color", "hurtboxes", "hitboxes"})

_OP_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "rect": (frozenset({"op", "x", "y", "w", "h", "color"}), frozenset()),
    "ellipse": (frozenset({"op", "x", "y", "w", "h", "color"}), frozenset()),
    "polygon": (frozenset({"op", "points", "color"}), frozenset()),
    "line": (frozenset({"op", "x1", "y1", "x2", "y2", "color"}), frozenset({"thickness"})),
    "pixels": (frozenset({"op", "coords", "color"}), frozenset()),
    "mirror_horizontal": (frozenset({"op"}), frozenset({"axis_x"})),
    "add_outline": (frozenset({"op", "color"}), frozenset({"diagonal"})),
}


def _parse_boxes(
    raw_boxes: Any,
    kind: str,
    frame_idx: int,
    width: int,
    height: int,
    warnings: list[str],
) -> list[list[int]]:
    """Validate [x, y, w, h] collision boxes and emit out-of-bounds warnings."""
    if raw_boxes is None:
        return []
    if not isinstance(raw_boxes, list):
        raise ValueError(f"Frame {frame_idx} '{kind}' must be a list of [x, y, w, h] boxes")

    parsed: list[list[int]] = []
    for box_idx, box in enumerate(raw_boxes):
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(
                f"Frame {frame_idx} {kind}[{box_idx}] must be [x, y, w, h], got {box!r}"
            )
        x = _require_int(box[0], f"frames[{frame_idx}].{kind}[{box_idx}][0]")
        y = _require_int(box[1], f"frames[{frame_idx}].{kind}[{box_idx}][1]")
        w = _require_int(box[2], f"frames[{frame_idx}].{kind}[{box_idx}][2]")
        h = _require_int(box[3], f"frames[{frame_idx}].{kind}[{box_idx}][3]")
        if w <= 0 or h <= 0:
            raise ValueError(
                f"Frame {frame_idx} {kind}[{box_idx}] width and height must be > 0, got [{x}, {y}, {w}, {h}]"
            )
        if x < 0 or y < 0 or x + w > width or y + h > height:
            warnings.append(
                f"Frame {frame_idx} {kind}[{box_idx}]: box [{x}, {y}, {w}, {h}] "
                f"exceeds canvas bounds 0..{width - 1} x 0..{height - 1}"
            )
        parsed.append([x, y, w, h])
    return parsed


def _execute_command(canvas: PixelCanvas, cmd: Any, frame_idx: int, cmd_idx: int) -> None:
    """Validate and execute one command dict on the target PixelCanvas."""
    if not isinstance(cmd, Mapping):
        raise ValueError(f"Frame {frame_idx}, command {cmd_idx} must be an object, got {type(cmd).__name__}")

    op = cmd.get("op")
    if not isinstance(op, str) or op not in _OP_KEYS:
        raise ValueError(
            f"Frame {frame_idx}, command {cmd_idx}: unknown or missing 'op' {op!r}. "
            f"Supported ops: {sorted(_OP_KEYS.keys())}"
        )

    required_keys, optional_keys = _OP_KEYS[op]
    provided_keys = frozenset(cmd.keys())
    missing = required_keys - provided_keys
    if missing:
        raise ValueError(
            f"Frame {frame_idx}, command {cmd_idx} ({op}): missing required field(s): {sorted(missing)}"
        )
    unknown = provided_keys - (required_keys | optional_keys)
    if unknown:
        raise ValueError(
            f"Frame {frame_idx}, command {cmd_idx} ({op}): unexpected field(s): {sorted(unknown)}"
        )

    warn_start = len(canvas.warnings)
    try:
        if op == "rect":
            canvas.rect(cmd["x"], cmd["y"], cmd["w"], cmd["h"], cmd["color"])
        elif op == "ellipse":
            canvas.ellipse(cmd["x"], cmd["y"], cmd["w"], cmd["h"], cmd["color"])
        elif op == "polygon":
            canvas.polygon(cmd["points"], cmd["color"])
        elif op == "line":
            thickness = cmd.get("thickness", 1)
            canvas.line(cmd["x1"], cmd["y1"], cmd["x2"], cmd["y2"], cmd["color"], thickness=thickness)
        elif op == "pixels":
            canvas.pixels(cmd["coords"], cmd["color"])
        elif op == "mirror_horizontal":
            canvas.mirror_horizontal(cmd.get("axis_x"))
        elif op == "add_outline":
            diagonal = cmd.get("diagonal", False)
            if not isinstance(diagonal, bool):
                raise ValueError("'diagonal' must be a boolean")
            canvas.add_outline(cmd["color"], diagonal=diagonal)
    except ValueError as exc:
        raise ValueError(f"Frame {frame_idx}, command {cmd_idx} ({op}): {exc}") from exc

    for idx in range(warn_start, len(canvas.warnings)):
        canvas.warnings[idx] = f"Frame {frame_idx}, command {cmd_idx}: {canvas.warnings[idx]}"


def render_spec(
    spec: Mapping[str, Any],
    output_dir: str | Path = "assets/sprites",
    *,
    preview_scale: int = 4,
) -> dict[str, Any]:
    """Validate and render a parsed sprite specification dict into sprite sheet artifacts."""
    if not isinstance(spec, Mapping):
        raise ValueError("Sprite spec must be a JSON object")

    unknown_top = set(spec.keys()) - _ALLOWED_SPEC_KEYS
    if unknown_top:
        raise ValueError(f"Unexpected top-level spec field(s): {sorted(unknown_top)}")

    if "name" not in spec:
        raise ValueError("Sprite spec missing required field 'name'")
    name = validate_sprite_name(spec["name"])

    width = _require_int(spec.get("width", 128), "width")
    height = _require_int(spec.get("height", 128), "height")
    if width <= 0 or height <= 0:
        raise ValueError(f"Canvas dimensions must be positive, got {width}x{height}")

    palette = spec.get("palette")
    if palette is not None and not isinstance(palette, Mapping):
        raise ValueError("'palette' must be an object mapping color names to hex strings")

    raw_frames = spec.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("'frames' must be a non-empty array of frame definitions")

    rendered_frames: list[FrameExport] = []
    all_warnings: list[str] = []

    for frame_idx, raw_frame in enumerate(raw_frames):
        if not isinstance(raw_frame, Mapping):
            raise ValueError(f"Frame {frame_idx} must be a JSON object")

        unknown_frame = set(raw_frame.keys()) - _ALLOWED_FRAME_KEYS
        if unknown_frame:
            raise ValueError(f"Frame {frame_idx} has unexpected field(s): {sorted(unknown_frame)}")

        frame_name = raw_frame.get("name")
        if frame_name is not None and (not isinstance(frame_name, str) or not frame_name.strip()):
            raise ValueError(f"Frame {frame_idx} 'name' must be a non-empty string if provided")

        commands = raw_frame.get("commands")
        if not isinstance(commands, list):
            raise ValueError(f"Frame {frame_idx} requires a 'commands' list")

        canvas = PixelCanvas(width=width, height=height, palette=palette)
        for cmd_idx, cmd in enumerate(commands):
            _execute_command(canvas, cmd, frame_idx, cmd_idx)

        outline_color = raw_frame.get("outline_color")
        if outline_color is not None:
            if not isinstance(outline_color, str):
                raise ValueError(f"Frame {frame_idx} 'outline_color' must be a color string")
            try:
                canvas.add_outline(outline_color)
            except ValueError as exc:
                raise ValueError(f"Frame {frame_idx} outline_color: {exc}") from exc

        all_warnings.extend(canvas.warnings)

        hurtboxes = _parse_boxes(
            raw_frame.get("hurtboxes"),
            "hurtboxes",
            frame_idx,
            width,
            height,
            all_warnings,
        )
        hitboxes = _parse_boxes(
            raw_frame.get("hitboxes"),
            "hitboxes",
            frame_idx,
            width,
            height,
            all_warnings,
        )

        rendered_frames.append(
            FrameExport(
                canvas=canvas,
                name=frame_name.strip() if isinstance(frame_name, str) else None,
                hurtboxes=hurtboxes,
                hitboxes=hitboxes,
            )
        )

    export_result = export_sprite_sheet(
        name=name,
        frames=rendered_frames,
        output_dir=output_dir,
        preview_scale=preview_scale,
    )
    export_result["warnings"] = all_warnings
    return export_result


def render_spec_json(
    spec_json: str,
    output_dir: str | Path = "assets/sprites",
    *,
    preview_scale: int = 4,
) -> dict[str, Any]:
    """Parse a JSON string sprite spec and render it to disk."""
    if not isinstance(spec_json, str):
        raise ValueError("spec_json must be a JSON string")
    try:
        parsed = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in sprite spec: {exc}") from exc
    return render_spec(parsed, output_dir=output_dir, preview_scale=preview_scale)


def render_spec_file(
    spec_path: str | Path,
    output_dir: str | Path = "assets/sprites",
    *,
    preview_scale: int = 4,
) -> dict[str, Any]:
    """Load a sprite spec JSON file and render it to disk."""
    path = Path(spec_path)
    if not path.is_file():
        raise FileNotFoundError(f"Spec file not found: {path}")
    return render_spec_json(
        path.read_text(encoding="utf-8"),
        output_dir=output_dir,
        preview_scale=preview_scale,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pixel_forge.renderer",
        description="Render a declarative JSON pixel-art sprite specification into a sprite sheet, preview, and hitbox metadata.",
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Path to the input JSON sprite specification file.",
    )
    parser.add_argument(
        "--out-dir",
        default="assets/sprites",
        help="Directory where <name>.png, <name>_preview.png, and <name>.json will be written (default: assets/sprites).",
    )
    parser.add_argument(
        "--preview-scale",
        type=int,
        default=4,
        help="Nearest-neighbor integer upscale factor for the preview PNG (default: 4).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for rendering a sprite specification file."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = render_spec_file(
            spec_path=args.spec,
            output_dir=args.out_dir,
            preview_scale=args.preview_scale,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
