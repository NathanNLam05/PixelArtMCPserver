# Pixel Forge MCP Server

Programmatic pixel-art sprite generator and `stdio` MCP server for drawing crisp, grid-aligned fighting game sprites and multi-frame animations with zero anti-aliasing.

## Repository Structure

```text
PixelArtMCPserver/
├── pixel_forge/
│   ├── __init__.py
│   ├── canvas.py      # PixelCanvas hard-pixel RGBA engine + sprite sheet/preview/JSON exporter
│   ├── renderer.py    # Declarative JSON spec parser, coordinate validator, and CLI
│   └── server.py      # FastMCP / MCPServer stdio server
├── requirements.txt
└── README.md
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## MCP Server Registration

Register Pixel Forge as a `stdio` MCP server in your client configuration:

```json
{
  "mcpServers": {
    "pixel-forge": {
      "command": "C:/Users/natha/Documents/GitHub/PixelArtMCPserver/.venv/Scripts/python.exe",
      "args": ["-m", "pixel_forge.server"],
      "cwd": "C:/Users/natha/Documents/GitHub/PixelArtMCPserver"
    }
  }
}
```

### Where Output Files Are Written

Because `"cwd"` is pinned to `PixelArtMCPserver`:
- **Relative `output_dir`** (including the default `"assets/sprites"`) writes inside `PixelArtMCPserver/assets/sprites/`.
- **Absolute `output_dir`** (e.g., `"C:/path/to/your-game/assets/sprites"`) writes directly into your target project directory. When using the MCP server inside another repository, instruct or let the AI pass that project's absolute sprite directory as `output_dir`.

### Exposed MCP Tools

- `get_canvas_spec(width=128, height=128)` — Returns the JSON schema, coordinate bounds (`0 <= x < width`, `0 <= y < height`, origin top-left), available drawing ops (`rect`, `ellipse`, `polygon`, `line`, `pixels`, `mirror_horizontal`, `add_outline`), and fighting-game proportion guidelines for the requested frame size.
- `render_sprite_sheet(spec_json, output_dir="assets/sprites")` — Validates coordinates, renders `<name>.png` (`(width * frame_count) x height`), `<name>_preview.png` (4x nearest-neighbor upscale), and `<name>.json` (frame dimensions, frame count, and per-frame `hurtboxes` / `hitboxes`), returning absolute file paths plus any out-of-bounds warnings.

## CLI Usage

You can also invoke the renderer directly from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pixel_forge.renderer --spec path/to/spec.json --out-dir path/to/output
```
