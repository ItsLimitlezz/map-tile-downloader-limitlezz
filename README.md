# MeshStudio Lite (Map Tile Downloader)

MeshStudio Lite is a Python application for downloading map tiles from multiple providers. It runs as a local desktop app by default (Qt) and supports direct tile export while downloading.

## Features

- Custom area downloads via map drawing tools.
- World basemap downloads (zoom 0-7).
- 8-bit PNG conversion for Meshtastic UI maps.
- Optional RGB565 `.bin` export for LVGL (`rgb565/z/x/y.bin`).
- Direct output mode: tiles are copied to the selected output folder during download (no zip wait).
- Built-in cache viewing and cache deletion.
- Live progress panel with estimated tiles/size/time and counters.

## Installation

1. Clone the repo:

```bash
git clone https://github.com/mattdrum/map-tile-downloader.git
cd map-tile-downloader
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Local desktop app (default)

```bash
python src/TileDL.py
```

### Browser mode (optional)

```bash
python src/TileDL.py --browser
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000).

## Workflow

1. Pick a map style.
2. Draw polygons (or use world basemap download).
3. Set min/max zoom.
4. Set output folder.
5. Optionally enable:
- Convert to 8 bit
- Export RGB565 `.bin` for LVGL (automatically enforces 8-bit)

Tiles are cached locally and copied into the selected output folder as they are processed.

## Notes

- Map sources are configured in `config/map_sources.json`.
- App title and desktop window title are `MeshStudio Lite`.

## License

MIT.
