"""Core pixel-art drawing engine with strict zero-anti-aliasing rasterization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]
ColorInput = str | RGBA
Point = tuple[int, int]

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _require_int(value: Any, name: str) -> int:
    """Validate that a coordinate or dimension is a real integer (not bool/float)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {type(value).__name__} ({value!r})")
    return value


def parse_hex_color(hex_code: str) -> RGBA:
    """Parse a #RRGGBB or #RRGGBBAA hex color string into an RGBA tuple."""
    if not isinstance(hex_code, str):
        raise ValueError(f"Hex color must be a string, got {type(hex_code).__name__}")
    match = _HEX_COLOR_RE.fullmatch(hex_code.strip())
    if match is None:
        raise ValueError(
            f"Invalid hex color {hex_code!r}; expected '#RRGGBB' or '#RRGGBBAA'"
        )
    digits = match.group(1)
    r = int(digits[0:2], 16)
    g = int(digits[2:4], 16)
    b = int(digits[4:6], 16)
    a = int(digits[6:8], 16) if len(digits) == 8 else 255
    return (r, g, b, a)


def validate_sprite_name(name: str) -> str:
    """Ensure sprite name is non-empty and safe to use as a filename stem."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Sprite 'name' must be a non-empty string")
    cleaned = name.strip()
    if _SAFE_NAME_RE.fullmatch(cleaned) is None or ".." in cleaned:
        raise ValueError(
            f"Invalid sprite name {name!r}; use letters, digits, '.', '_', or '-' only"
        )
    return cleaned


class PixelCanvas:
    """Hard-pixel RGBA drawing surface with named palette management and export helpers.

    Origin (0, 0) is top-left with valid pixel bounds ``0 <= x < width`` and
    ``0 <= y < height``. Every primitive rasterizes strictly without anti-aliasing.
    """

    def __init__(
        self,
        width: int = 128,
        height: int = 128,
        palette: Mapping[str, str] | None = None,
    ) -> None:
        width = _require_int(width, "width")
        height = _require_int(height, "height")
        if width <= 0 or height <= 0:
            raise ValueError(f"Canvas dimensions must be positive, got {width}x{height}")

        self.width = width
        self.height = height
        self.image: Image.Image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        self._draw: ImageDraw.ImageDraw = ImageDraw.Draw(self.image)
        self.palette: dict[str, RGBA] = {"transparent": (0, 0, 0, 0)}
        self.warnings: list[str] = []

        if palette is not None:
            self.set_palette(palette)

    def set_palette(self, palette: Mapping[str, str]) -> None:
        """Replace custom palette entries while always keeping 'transparent': (0, 0, 0, 0)."""
        if not isinstance(palette, Mapping):
            raise ValueError("Palette must be a mapping of color names to hex strings")

        parsed: dict[str, RGBA] = {"transparent": (0, 0, 0, 0)}
        for name, hex_code in palette.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Palette color name must be a non-empty string, got {name!r}")
            rgba = parse_hex_color(hex_code)
            if name == "transparent":
                if rgba[3] != 0:
                    raise ValueError(
                        "Cannot override 'transparent' palette color with a non-transparent alpha"
                    )
                continue
            parsed[name] = rgba
        self.palette = parsed

    def add_color(self, name: str, hex_code: str) -> None:
        """Add or update a named palette entry."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Palette color name must be a non-empty string, got {name!r}")
        rgba = parse_hex_color(hex_code)
        if name == "transparent":
            if rgba[3] != 0:
                raise ValueError(
                    "Cannot override 'transparent' palette color with a non-transparent alpha"
                )
            return
        self.palette[name] = rgba

    def resolve_color(self, color: ColorInput) -> RGBA:
        """Resolve a palette color name, hex string, or RGBA tuple to a 4-tuple."""
        if isinstance(color, tuple):
            if len(color) != 4:
                raise ValueError(f"RGBA tuple must have 4 elements, got {len(color)}")
            channels = tuple(_require_int(c, "rgba channel") for c in color)
            if any(c < 0 or c > 255 for c in channels):
                raise ValueError(f"RGBA channels must be in 0..255, got {channels}")
            return channels  # type: ignore[return-value]

        if isinstance(color, str):
            if color in self.palette:
                return self.palette[color]
            if color.startswith("#"):
                return parse_hex_color(color)
            raise ValueError(
                f"Unknown palette color {color!r}. Available: {sorted(self.palette.keys())}"
            )

        raise ValueError(f"Unsupported color type {type(color).__name__}: {color!r}")

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def _check_box_bounds(self, x: int, y: int, w: int, h: int, op: str) -> bool:
        """Validate box size and record out-of-bounds warnings; returns False if nothing to draw."""
        if w < 0 or h < 0:
            raise ValueError(f"{op} dimensions must be non-negative, got w={w}, h={h}")
        if w == 0 or h == 0:
            self.warnings.append(f"{op}: zero-area dimensions (w={w}, h={h}); nothing drawn")
            return False

        x2 = x + w - 1
        y2 = y + h - 1
        if x < 0 or y < 0 or x2 >= self.width or y2 >= self.height:
            self.warnings.append(
                f"{op}: box [{x}, {y}, {w}, {h}] (pixel span x={x}..{x2}, y={y}..{y2}) "
                f"exceeds canvas bounds 0..{self.width - 1} x 0..{self.height - 1}"
            )
        if x2 < 0 or y2 < 0 or x >= self.width or y >= self.height:
            return False
        return True

    def rect(self, x: int, y: int, w: int, h: int, color: ColorInput) -> None:
        """Draw a hard-edged filled rectangle with top-left (x, y) and size (w, h)."""
        x = _require_int(x, "x")
        y = _require_int(y, "y")
        w = _require_int(w, "w")
        h = _require_int(h, "h")
        rgba = self.resolve_color(color)
        if not self._check_box_bounds(x, y, w, h, "rect"):
            return
        self._draw.rectangle([x, y, x + w - 1, y + h - 1], fill=rgba)

    def ellipse(self, x: int, y: int, w: int, h: int, color: ColorInput) -> None:
        """Draw a hard-edged filled ellipse inside bounding box (x, y, w, h)."""
        x = _require_int(x, "x")
        y = _require_int(y, "y")
        w = _require_int(w, "w")
        h = _require_int(h, "h")
        rgba = self.resolve_color(color)
        if not self._check_box_bounds(x, y, w, h, "ellipse"):
            return
        self._draw.ellipse([x, y, x + w - 1, y + h - 1], fill=rgba)

    def polygon(self, points: Sequence[Sequence[int]], color: ColorInput) -> None:
        """Draw a hard-edged filled polygon through integer vertices."""
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise ValueError("polygon 'points' must be a list of [x, y] integer pairs")
        if len(points) < 3:
            raise ValueError(f"polygon requires at least 3 points, got {len(points)}")

        parsed_points: list[Point] = []
        oob_points: list[Point] = []
        for idx, pt in enumerate(points):
            if not isinstance(pt, Sequence) or isinstance(pt, (str, bytes)) or len(pt) != 2:
                raise ValueError(f"polygon point[{idx}] must be a [x, y] pair, got {pt!r}")
            px = _require_int(pt[0], f"points[{idx}][0]")
            py = _require_int(pt[1], f"points[{idx}][1]")
            parsed_points.append((px, py))
            if not self._in_bounds(px, py):
                oob_points.append((px, py))

        rgba = self.resolve_color(color)
        if oob_points:
            sample = oob_points[:4]
            suffix = f" (+{len(oob_points) - 4} more)" if len(oob_points) > 4 else ""
            self.warnings.append(
                f"polygon: vertices {sample}{suffix} exceed canvas bounds "
                f"0..{self.width - 1} x 0..{self.height - 1}"
            )
        self._draw.polygon(parsed_points, fill=rgba)

    def line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: ColorInput,
        thickness: int = 1,
    ) -> None:
        """Draw a crisp integer-coordinate line from (x1, y1) to (x2, y2)."""
        x1 = _require_int(x1, "x1")
        y1 = _require_int(y1, "y1")
        x2 = _require_int(x2, "x2")
        y2 = _require_int(y2, "y2")
        thickness = _require_int(thickness, "thickness")
        if thickness < 1:
            raise ValueError(f"line thickness must be >= 1, got {thickness}")

        rgba = self.resolve_color(color)
        if not (self._in_bounds(x1, y1) and self._in_bounds(x2, y2)):
            self.warnings.append(
                f"line: endpoints ({x1}, {y1}) -> ({x2}, {y2}) exceed canvas bounds "
                f"0..{self.width - 1} x 0..{self.height - 1}"
            )
        self._draw.line([(x1, y1), (x2, y2)], fill=rgba, width=thickness)

    def pixels(self, coords: Sequence[Sequence[int]], color: ColorInput) -> None:
        """Plot individual pixels at exact integer (x, y) coordinates."""
        if not isinstance(coords, Sequence) or isinstance(coords, (str, bytes)):
            raise ValueError("pixels 'coords' must be a list of [x, y] integer pairs")

        rgba = self.resolve_color(color)
        px = self.image.load()
        oob: list[Point] = []
        for idx, pt in enumerate(coords):
            if not isinstance(pt, Sequence) or isinstance(pt, (str, bytes)) or len(pt) != 2:
                raise ValueError(f"pixels coord[{idx}] must be a [x, y] pair, got {pt!r}")
            x = _require_int(pt[0], f"coords[{idx}][0]")
            y = _require_int(pt[1], f"coords[{idx}][1]")
            if self._in_bounds(x, y):
                px[x, y] = rgba
            else:
                oob.append((x, y))

        if oob:
            sample = oob[:4]
            suffix = f" (+{len(oob) - 4} more)" if len(oob) > 4 else ""
            self.warnings.append(
                f"pixels: {len(oob)} coordinate(s) out of bounds {sample}{suffix} "
                f"(canvas {self.width}x{self.height})"
            )

    def mirror_horizontal(self, axis_x: int | None = None) -> None:
        """Mirror the left side of axis_x (0 <= x < axis_x) onto the right side.

        Defaults to ``width // 2``, mirroring pixel ``x`` to ``2 * axis_x - 1 - x``.
        """
        axis = self.width // 2 if axis_x is None else _require_int(axis_x, "axis_x")
        if axis <= 0 or axis >= self.width:
            self.warnings.append(
                f"mirror_horizontal: axis_x={axis} is at or outside canvas width 0..{self.width}"
            )
        elif 2 * axis > self.width:
            self.warnings.append(
                f"mirror_horizontal: axis_x={axis} exceeds half width ({self.width // 2}); "
                "leftmost mirrored pixels clip past canvas right edge"
            )

        px = self.image.load()
        max_src_x = min(axis, self.width)
        for y in range(self.height):
            for x in range(max_src_x):
                dst_x = 2 * axis - 1 - x
                if 0 <= dst_x < self.width:
                    px[dst_x, y] = px[x, y]

    def add_outline(self, color: ColorInput, *, diagonal: bool = False) -> None:
        """Add a 1px solid border around all non-transparent pixels without overwriting them."""
        outline_rgba = self.resolve_color(color)
        snapshot = self.image.copy()
        src = snapshot.load()
        dst = self.image.load()

        offsets: tuple[Point, ...] = (
            ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
            if diagonal
            else ((-1, 0), (1, 0), (0, -1), (0, 1))
        )

        for y in range(self.height):
            for x in range(self.width):
                if src[x, y][3] == 0:
                    continue
                for dx, dy in offsets:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height and src[nx, ny][3] == 0:
                        dst[nx, ny] = outline_rgba

    def to_preview(self, scale: int = 4) -> Image.Image:
        """Return a nearest-neighbor upscaled copy of this frame for visual inspection."""
        scale = _require_int(scale, "scale")
        if scale < 1:
            raise ValueError(f"Preview scale must be >= 1, got {scale}")
        return self.image.resize(
            (self.width * scale, self.height * scale),
            resample=Image.Resampling.NEAREST,
        )


@dataclass
class FrameExport:
    """Rendered frame canvas paired with optional fighting-game collision boxes."""

    canvas: PixelCanvas
    name: str | None = None
    hurtboxes: list[list[int]] = field(default_factory=list)
    hitboxes: list[list[int]] = field(default_factory=list)


def export_sprite_sheet(
    name: str,
    frames: Sequence[FrameExport],
    output_dir: str | Path,
    *,
    preview_scale: int = 4,
) -> dict[str, Any]:
    """Pack frames horizontally into a transparent sprite sheet, 4x preview, and hitbox JSON."""
    safe_name = validate_sprite_name(name)
    if not frames:
        raise ValueError("Cannot export a sprite sheet with zero frames")
    preview_scale = _require_int(preview_scale, "preview_scale")
    if preview_scale < 1:
        raise ValueError(f"preview_scale must be >= 1, got {preview_scale}")

    frame_width = frames[0].canvas.width
    frame_height = frames[0].canvas.height
    for idx, frame in enumerate(frames):
        if frame.canvas.width != frame_width or frame.canvas.height != frame_height:
            raise ValueError(
                f"Frame {idx} size {frame.canvas.width}x{frame.canvas.height} does not match "
                f"expected {frame_width}x{frame_height}"
            )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    sheet_width = frame_width * len(frames)
    sheet_height = frame_height
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))

    frame_metadata: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        x_offset = idx * frame_width
        sheet.paste(frame.canvas.image, (x_offset, 0))
        entry: dict[str, Any] = {
            "index": idx,
            "x_offset": x_offset,
            "y_offset": 0,
            "hurtboxes": frame.hurtboxes,
            "hitboxes": frame.hitboxes,
        }
        if frame.name is not None:
            entry["name"] = frame.name
        frame_metadata.append(entry)

    preview = sheet.resize(
        (sheet_width * preview_scale, sheet_height * preview_scale),
        resample=Image.Resampling.NEAREST,
    )

    sheet_file = out_path / f"{safe_name}.png"
    preview_file = out_path / f"{safe_name}_preview.png"
    metadata_file = out_path / f"{safe_name}.json"

    metadata = {
        "name": safe_name,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frame_count": len(frames),
        "sheet_width": sheet_width,
        "sheet_height": sheet_height,
        "frames": frame_metadata,
    }

    sheet.save(sheet_file, format="PNG")
    preview.save(preview_file, format="PNG")
    metadata_file.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    return {
        "name": safe_name,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frame_count": len(frames),
        "sheet_width": sheet_width,
        "sheet_height": sheet_height,
        "sprite_sheet_path": str(sheet_file),
        "preview_path": str(preview_file),
        "metadata_path": str(metadata_file),
    }
