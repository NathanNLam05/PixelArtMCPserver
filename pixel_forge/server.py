"""FastMCP / MCPServer stdio server exposing Pixel Forge sprite generation tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - compatibility with mcp < 2.0
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from .canvas import _require_int
from .renderer import SPRITE_SPEC_SCHEMA, render_spec_json

mcp = FastMCP(
    "pixel-forge",
    instructions=(
        "Programmatic pixel-art sprite sheet generator with zero anti-aliasing. "
        "Call get_canvas_spec(width, height) first to get the JSON schema, drawing ops, "
        "and pixel budget, then call render_sprite_sheet(spec_json, output_dir). "
        "Pass an absolute output_dir pointing to the target project's sprite folder; "
        "relative paths resolve from the MCP server's working directory."
    ),
)


def _compute_proportion_guidelines(width: int, height: int) -> dict[str, Any]:
    """Compute fighting-game sprite pixel budget and landmarks for a canvas size."""
    ground_margin = max(4, height // 16)
    ground_y = height - ground_margin
    fighter_height = max(12, round(height * 0.72))
    head_top_y = max(2, ground_y - fighter_height)
    head_h = max(4, round(fighter_height * 0.18))
    torso_h = max(6, round(fighter_height * 0.38))
    legs_h = max(4, fighter_height - head_h - torso_h)

    center_x = width // 2
    # Shift anchor slightly left on wide frames (e.g. 256x128) so forward attacks have generous room.
    attack_anchor_x = round(width * 0.40) if width > height else center_x
    body_width = max(8, round(min(width, height) * 0.28))
    forward_reach_px = max(8, (width - 4) - (attack_anchor_x + body_width // 2))

    return {
        "ground_y": ground_y,
        "recommended_fighter_height_px": fighter_height,
        "head_top_y": head_top_y,
        "head_height_px": head_h,
        "torso_height_px": torso_h,
        "legs_height_px": legs_h,
        "recommended_body_width_px": body_width,
        "symmetric_center_x": center_x,
        "attack_stance_anchor_x": attack_anchor_x,
        "forward_attack_reach_budget_px": forward_reach_px,
        "recommended_outline_margin_px": 1,
        "tips": [
            "Reserve at least 1px transparent padding around the sprite silhouette so frame-level outline_color never clips at canvas edges.",
            f"Anchor feet consistently on ground_y={ground_y} across every frame so animations do not bob unintentionally.",
            f"Keep symmetric idle body around x={center_x} (mirror_horizontal defaults to axis_x={center_x}), or anchor around x={attack_anchor_x} for wide forward attacks.",
            "Layer commands back-to-front: back limb -> torso/legs -> head -> shading/highlights -> front limb/weapon -> frame outline_color.",
            "Use hurtboxes for vulnerable body regions on every frame and hitboxes only on active strike frames.",
        ],
    }


@mcp.tool()
def get_canvas_spec(width: int = 128, height: int = 128) -> dict[str, Any]:
    """Return the JSON schema, drawing operations, coordinate rules, and pixel budget for width x height."""
    try:
        canvas_width = _require_int(width, "width")
        canvas_height = _require_int(height, "height")
        if canvas_width <= 0 or canvas_height <= 0:
            raise ValueError(
                f"Canvas width and height must be positive integers, got {canvas_width}x{canvas_height}"
            )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "canvas": {
            "width": canvas_width,
            "height": canvas_height,
            "origin": "(0, 0) top-left",
            "x_bounds": [0, canvas_width - 1],
            "y_bounds": [0, canvas_height - 1],
            "default_mirror_axis_x": canvas_width // 2,
            "color_mode": "RGBA (strict nearest-neighbor / zero anti-aliasing)",
            "builtin_palette_entries": {"transparent": "#00000000"},
        },
        "coordinate_rules": [
            f"All coordinates must be integers within 0 <= x < {canvas_width} and 0 <= y < {canvas_height}.",
            "Origin (0, 0) is top-left; +x goes right and +y goes down.",
            "rect(x, y, w, h) and ellipse(x, y, w, h) fill pixels x..x+w-1 and y..y+h-1 inclusively.",
            "Palette maps color names to #RRGGBB or #RRGGBBAA hex strings; 'transparent' is always (0, 0, 0, 0).",
            "hurtboxes and hitboxes are lists of [x, y, w, h] integer rectangles with w >= 1, h >= 1.",
            "Multi-frame animations pack horizontally left-to-right into (<width * frame_count>) x <height> transparent PNG plus a 4x nearest-neighbor _preview.png and companion .json.",
        ],
        "operations": {
            "rect": {
                "fields": {"op": "rect", "x": "int", "y": "int", "w": "int", "h": "int", "color": "str"},
                "description": "Filled hard-edged rectangle.",
            },
            "ellipse": {
                "fields": {"op": "ellipse", "x": "int", "y": "int", "w": "int", "h": "int", "color": "str"},
                "description": "Filled hard-edged ellipse inside bounding box (x, y, w, h).",
            },
            "polygon": {
                "fields": {"op": "polygon", "points": "list[[int, int]] (>=3)", "color": "str"},
                "description": "Filled hard-edged polygon.",
            },
            "line": {
                "fields": {
                    "op": "line",
                    "x1": "int",
                    "y1": "int",
                    "x2": "int",
                    "y2": "int",
                    "color": "str",
                    "thickness": "int (optional, default 1)",
                },
                "description": "Crisp integer-aligned line segment.",
            },
            "pixels": {
                "fields": {"op": "pixels", "coords": "list[[int, int]]", "color": "str"},
                "description": "Exact 1x1 pixel plotting for eyes, highlights, rivets, and blade edges.",
            },
            "mirror_horizontal": {
                "fields": {"op": "mirror_horizontal", "axis_x": "int | null (optional, default width // 2)"},
                "description": "Mirrors left-side pixels (0 <= x < axis_x) onto the right side (2*axis_x - 1 - x).",
            },
            "add_outline": {
                "fields": {
                    "op": "add_outline",
                    "color": "str",
                    "diagonal": "bool (optional, default false)",
                },
                "description": "Adds a 1px solid border around all non-transparent pixels without overwriting interior pixels.",
            },
        },
        "proportion_guidelines": _compute_proportion_guidelines(canvas_width, canvas_height),
        "json_schema": SPRITE_SPEC_SCHEMA,
    }


@mcp.tool()
def render_sprite_sheet(spec_json: str, output_dir: str = "assets/sprites") -> dict[str, Any]:
    """Validate and render a JSON sprite spec into PNG sheet, 4x preview PNG, and hitbox JSON.

    Pass an absolute path for output_dir to save directly into a target project directory;
    relative paths resolve from the MCP server's cwd.
    """
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ToolError("output_dir must be a non-empty directory path string")

    try:
        result = render_spec_json(spec_json, output_dir=output_dir)
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc

    return {
        "name": result["name"],
        "frame_width": result["frame_width"],
        "frame_height": result["frame_height"],
        "frame_count": result["frame_count"],
        "sheet_width": result["sheet_width"],
        "sheet_height": result["sheet_height"],
        "sprite_sheet_path": str(Path(result["sprite_sheet_path"]).resolve()),
        "preview_path": str(Path(result["preview_path"]).resolve()),
        "metadata_path": str(Path(result["metadata_path"]).resolve()),
        "warnings": result["warnings"],
    }


def main() -> None:
    """Run the Pixel Forge MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
