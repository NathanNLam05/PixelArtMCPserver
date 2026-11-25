"""Pixel Forge: Grid-aligned pixel-art sprite generator and MCP server."""

from typing import Any

from .canvas import FrameExport, PixelCanvas, export_sprite_sheet, parse_hex_color

__all__ = [
    "FrameExport",
    "PixelCanvas",
    "export_sprite_sheet",
    "parse_hex_color",
    "render_spec",
    "render_spec_file",
]


def __getattr__(name: str) -> Any:
    """Lazily expose renderer helpers without triggering runpy warnings on `-m pixel_forge.renderer`."""
    if name in ("render_spec", "render_spec_file"):
        from . import renderer

        return getattr(renderer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
