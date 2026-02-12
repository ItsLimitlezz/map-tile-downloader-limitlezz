#!/usr/bin/env python3
import argparse
import random
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mercantile
import requests
from flask import Flask, jsonify, render_template, send_file
from flask_socketio import SocketIO, emit
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
import json


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / 'templates'
CONFIG_DIR = BASE_DIR / 'config'
MAP_SOURCES_FILE = CONFIG_DIR / 'map_sources.json'
CACHE_DIR = BASE_DIR / 'tile-cache'
DOWNLOADS_DIR = BASE_DIR / 'downloads'

preferred_output = Path.home() / 'Downloads' / 'MapTileDownloader'
DEFAULT_OUTPUT_ROOT = preferred_output if preferred_output.parent.exists() else DOWNLOADS_DIR / 'output'

CACHE_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
socketio = SocketIO(app)

if not MAP_SOURCES_FILE.exists():
    print('Warning: map_sources.json not found. No map sources available.')
    sys.exit(1)

with open(MAP_SOURCES_FILE, 'r', encoding='utf-8') as f:
    MAP_SOURCES = json.load(f)

# Global event for cancellation
DOWNLOAD_EVENT = threading.Event()


def sanitize_style_name(style_name):
    """Convert map style name to a filesystem-safe directory name."""
    style_name = re.sub(r'\s+', '-', style_name)
    style_name = re.sub(r'[^a-zA-Z0-9-_]', '', style_name)
    return style_name


def resolve_style_name(map_style_url):
    """Resolve map style display name from tile URL."""
    for name, url in MAP_SOURCES.items():
        if url == map_style_url:
            return name
    raise ValueError('Map style URL not found in configured map sources')


def get_style_cache_dir(style_name):
    """Get the cache directory path for a given map style name."""
    return CACHE_DIR / sanitize_style_name(style_name)


def get_style_rgb565_dir(style_cache_dir):
    """Store RGB565 artifacts in a dedicated subdirectory."""
    return style_cache_dir / 'rgb565'


def get_style_output_dir(style_name, output_root_input):
    """Resolve the destination folder for exported tiles."""
    output_root = Path(output_root_input).expanduser() if output_root_input else DEFAULT_OUTPUT_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root / sanitize_style_name(style_name)


def emit_progress(progress_callback, event_name, payload):
    """Emit downloader events either to Socket.IO or an injected callback."""
    if progress_callback:
        progress_callback(event_name, payload)


def copy_to_output(source_path, style_cache_dir, style_output_dir):
    """Mirror a cached artifact into the selected output directory."""
    if not style_output_dir:
        return

    source_resolved = source_path.resolve()
    output_resolved = style_output_dir.resolve()
    if source_resolved.is_relative_to(output_resolved):
        return

    relative_path = source_path.relative_to(style_cache_dir)
    destination_path = style_output_dir / relative_path
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)


def convert_to_rgb565_bin(tile_path, rgb565_output_path):
    """Convert a PNG tile to raw RGB565 binary (big-endian bytes) for LVGL workflows."""
    rgb565_output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(tile_path) as img:
        rgb_img = img.convert('RGB')
        buffer = bytearray()
        for red, green, blue in rgb_img.getdata():
            rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            buffer.append((rgb565 >> 8) & 0xFF)
            buffer.append(rgb565 & 0xFF)

    with open(rgb565_output_path, 'wb') as output_file:
        output_file.write(buffer)


def download_tile(
    tile,
    map_style,
    style_cache_dir,
    convert_to_8bit,
    convert_to_rgb565_bin_files=False,
    style_output_dir=None,
    progress_callback=None,
    max_retries=3,
):
    """Download a single tile and optionally convert to 8-bit PNG and RGB565 .bin."""
    if not DOWNLOAD_EVENT.is_set():
        return None

    tile_dir = style_cache_dir / str(tile.z) / str(tile.x)
    tile_path = tile_dir / f'{tile.y}.png'
    rgb565_tile_path = get_style_rgb565_dir(style_cache_dir) / str(tile.z) / str(tile.x) / f'{tile.y}.bin'

    if tile_path.exists():
        converted_this_tile = False
        if convert_to_8bit:
            with Image.open(tile_path) as img:
                if img.mode != 'P':
                    img = img.quantize(colors=256)
                    img.save(tile_path)
                    converted_this_tile = True

        if convert_to_rgb565_bin_files and not rgb565_tile_path.exists():
            convert_to_rgb565_bin(tile_path, rgb565_tile_path)
            converted_this_tile = True

        copy_to_output(tile_path, style_cache_dir, style_output_dir)
        if convert_to_rgb565_bin_files:
            copy_to_output(rgb565_tile_path, style_cache_dir, style_output_dir)

        bounds = mercantile.bounds(tile)
        emit_progress(progress_callback, 'tile_skipped', {
            'west': bounds.west,
            'south': bounds.south,
            'east': bounds.east,
            'north': bounds.north,
            'size_bytes': tile_path.stat().st_size,
        })
        if converted_this_tile:
            emit_progress(progress_callback, 'tile_converted', {'tile': f'{tile.z}/{tile.x}/{tile.y}'})
        return tile_path

    subdomain = random.choice(['a', 'b', 'c']) if '{s}' in map_style else ''
    url = (
        map_style.replace('{s}', subdomain)
        .replace('{z}', str(tile.z))
        .replace('{x}', str(tile.x))
        .replace('{y}', str(tile.y))
    )

    headers = {'User-Agent': 'MapTileDownloader/1.0'}

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                tile_dir.mkdir(parents=True, exist_ok=True)
                with open(tile_path, 'wb') as f:
                    f.write(response.content)

                converted_this_tile = False
                if convert_to_8bit:
                    with Image.open(tile_path) as img:
                        if img.mode != 'P':
                            img = img.quantize(colors=256)
                            img.save(tile_path)
                            converted_this_tile = True

                if convert_to_rgb565_bin_files:
                    convert_to_rgb565_bin(tile_path, rgb565_tile_path)
                    converted_this_tile = True

                copy_to_output(tile_path, style_cache_dir, style_output_dir)
                if convert_to_rgb565_bin_files:
                    copy_to_output(rgb565_tile_path, style_cache_dir, style_output_dir)

                bounds = mercantile.bounds(tile)
                emit_progress(progress_callback, 'tile_downloaded', {
                    'west': bounds.west,
                    'south': bounds.south,
                    'east': bounds.east,
                    'north': bounds.north,
                    'size_bytes': tile_path.stat().st_size,
                })
                if converted_this_tile:
                    emit_progress(progress_callback, 'tile_converted', {'tile': f'{tile.z}/{tile.x}/{tile.y}'})
                return tile_path

            time.sleep(2 ** attempt)
        except requests.RequestException:
            time.sleep(2 ** attempt)

    emit_progress(progress_callback, 'tile_failed', {'tile': f'{tile.z}/{tile.x}/{tile.y}'})
    return None


def get_world_tiles():
    """Generate list of tiles for zoom levels 0 to 7 for the entire world."""
    tiles = []
    for z in range(8):
        for x in range(2 ** z):
            for y in range(2 ** z):
                tiles.append(mercantile.Tile(x, y, z))
    return tiles


def get_tiles_for_polygons(polygons_data, min_zoom, max_zoom):
    """Generate list of tiles that intersect with the given polygons for the specified zoom range."""
    polygons = [Polygon([(lng, lat) for lat, lng in poly]) for poly in polygons_data]
    overall_polygon = unary_union(polygons)
    west, south, east, north = overall_polygon.bounds
    all_tiles = []

    for z in range(min_zoom, max_zoom + 1):
        tiles = mercantile.tiles(west, south, east, north, zooms=[z])
        for tile in tiles:
            tile_bbox = mercantile.bounds(tile)
            tile_box = box(tile_bbox.west, tile_bbox.south, tile_bbox.east, tile_bbox.north)
            if any(tile_box.intersects(poly) for poly in polygons):
                all_tiles.append(tile)

    all_tiles.sort(key=lambda tile: (tile.z, -tile.x, tile.y))
    return all_tiles


def download_tiles_with_retries(
    tiles,
    map_style,
    style_cache_dir,
    convert_to_8bit,
    convert_to_rgb565_bin_files=False,
    style_output_dir=None,
    progress_callback=None,
):
    """Download tiles in parallel; each tile already has per-request retry handling."""
    emit_progress(progress_callback, 'download_started', {'total_tiles': len(tiles)})

    max_workers = 5
    batch_size = 10

    def process_batch(batch):
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    download_tile,
                    tile,
                    map_style,
                    style_cache_dir,
                    convert_to_8bit,
                    convert_to_rgb565_bin_files,
                    style_output_dir,
                    progress_callback,
                ): tile
                for tile in batch
            }
            for future in as_completed(futures):
                future.result()

    if tiles and DOWNLOAD_EVENT.is_set():
        for i in range(0, len(tiles), batch_size):
            if not DOWNLOAD_EVENT.is_set():
                break
            process_batch(tiles[i:i + batch_size])

    if DOWNLOAD_EVENT.is_set():
        emit_progress(progress_callback, 'tiles_downloaded', {})


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')


@app.route('/get_map_sources')
def get_map_sources():
    """Return configured map sources."""
    return jsonify(MAP_SOURCES)


@app.route('/get_default_output_dir')
def get_default_output_dir():
    """Return default local output root folder."""
    return jsonify({'output_dir': str(DEFAULT_OUTPUT_ROOT)})


@socketio.on('start_download')
def handle_start_download(data):
    """Handle download request for tiles within polygons."""
    try:
        polygons_data = data['polygons']
        min_zoom = data['min_zoom']
        max_zoom = data['max_zoom']
        map_style_url = data['map_style']
        convert_to_8bit = data.get('convert_to_8bit', False)
        convert_to_rgb565_bin_files = data.get('convert_to_rgb565_bin_files', False)
        if convert_to_rgb565_bin_files:
            convert_to_8bit = True

        output_root_input = data.get('output_dir', '')
        style_name = resolve_style_name(map_style_url)
        style_cache_dir = get_style_cache_dir(style_name)
        style_output_dir = get_style_output_dir(style_name, output_root_input)
        style_output_dir.mkdir(parents=True, exist_ok=True)

        if min_zoom < 0 or max_zoom > 19 or min_zoom > max_zoom:
            emit('error', {'message': 'Invalid zoom range (must be 0-19, min <= max)'})
            return
        if not polygons_data:
            emit('error', {'message': 'No polygons provided'})
            return

        tiles = get_tiles_for_polygons(polygons_data, min_zoom, max_zoom)
        DOWNLOAD_EVENT.set()
        download_tiles_with_retries(
            tiles,
            map_style_url,
            style_cache_dir,
            convert_to_8bit,
            convert_to_rgb565_bin_files=convert_to_rgb565_bin_files,
            style_output_dir=style_output_dir,
            progress_callback=socketio.emit,
        )

        if DOWNLOAD_EVENT.is_set():
            emit('download_complete', {'output_dir': str(style_output_dir)})
    except Exception as exc:
        print(f'Error processing download: {exc}')
        emit('error', {'message': 'An error occurred while processing your request'})


@socketio.on('start_world_download')
def handle_start_world_download(data):
    """Handle download request for world basemap tiles (zoom 0-7)."""
    try:
        map_style_url = data['map_style']
        convert_to_8bit = data.get('convert_to_8bit', False)
        convert_to_rgb565_bin_files = data.get('convert_to_rgb565_bin_files', False)
        if convert_to_rgb565_bin_files:
            convert_to_8bit = True

        output_root_input = data.get('output_dir', '')
        style_name = resolve_style_name(map_style_url)
        style_cache_dir = get_style_cache_dir(style_name)
        style_output_dir = get_style_output_dir(style_name, output_root_input)
        style_output_dir.mkdir(parents=True, exist_ok=True)

        tiles = get_world_tiles()
        DOWNLOAD_EVENT.set()
        download_tiles_with_retries(
            tiles,
            map_style_url,
            style_cache_dir,
            convert_to_8bit,
            convert_to_rgb565_bin_files=convert_to_rgb565_bin_files,
            style_output_dir=style_output_dir,
            progress_callback=socketio.emit,
        )

        if DOWNLOAD_EVENT.is_set():
            emit('download_complete', {'output_dir': str(style_output_dir)})
    except Exception as exc:
        print(f'Error processing world download: {exc}')
        emit('error', {'message': 'An error occurred while processing your request'})


@socketio.on('cancel_download')
def handle_cancel_download():
    """Handle cancellation of the download."""
    DOWNLOAD_EVENT.clear()
    emit('download_cancelled')


@app.route('/tiles/<style_name>/<int:z>/<int:x>/<int:y>.png')
def serve_tile(style_name, z, x, y):
    """Serve a cached tile if it exists."""
    style_cache_dir = get_style_cache_dir(style_name)
    tile_path = style_cache_dir / str(z) / str(x) / f'{y}.png'
    if tile_path.exists():
        return send_file(tile_path)
    return '', 404


@app.route('/delete_cache/<style_name>', methods=['DELETE'])
def delete_cache(style_name):
    """Delete the cache directory for a specific style."""
    cache_dir = get_style_cache_dir(style_name)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        return '', 204
    return 'Cache not found', 404


@app.route('/get_cached_tiles/<style_name>')
def get_cached_tiles_route(style_name):
    """Return a list of [z, x, y] for cached tiles of the given style."""
    style_cache_dir = get_style_cache_dir(style_name)
    if not style_cache_dir.exists():
        return jsonify([])

    cached_tiles = []
    for z_dir in style_cache_dir.iterdir():
        if z_dir.is_dir():
            try:
                z = int(z_dir.name)
                for x_dir in z_dir.iterdir():
                    if x_dir.is_dir():
                        try:
                            x = int(x_dir.name)
                            for y_file in x_dir.glob('*.png'):
                                try:
                                    y = int(y_file.stem)
                                    cached_tiles.append([z, x, y])
                                except ValueError:
                                    pass
                        except ValueError:
                            pass
            except ValueError:
                pass
    return jsonify(cached_tiles)


def run_server(host='127.0.0.1', port=5000, debug=False):
    """Start the Flask-SocketIO server."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


def main():
    """CLI entrypoint for local desktop mode and optional browser mode."""
    parser = argparse.ArgumentParser(description='Map Tile Downloader')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind the local server')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind the local server')
    parser.add_argument('--debug', action='store_true', help='Enable Flask debug mode')
    parser.add_argument(
        '--browser',
        action='store_true',
        help='Run browser mode (Flask server only) instead of the default local Qt app',
    )
    parser.add_argument(
        '--qt',
        action='store_true',
        help='Launch the native Qt desktop app (default when --browser is not set)',
    )
    parser.add_argument(
        '--server-only',
        action='store_true',
        help='Run only the local Flask server (used internally by Qt launcher)',
    )
    args = parser.parse_args()

    if args.server_only or args.browser:
        run_server(host=args.host, port=args.port, debug=args.debug)
        return 0

    try:
        from qt_app import launch_qt_app
    except ImportError as exc:
        print(
            'Qt dependencies are not installed. Run: pip install -r requirements.txt',
            file=sys.stderr,
        )
        print(f'Details: {exc}', file=sys.stderr)
        print('Fallback available: run with --browser', file=sys.stderr)
        return 1

    return launch_qt_app(args.host, args.port)


if __name__ == '__main__':
    raise SystemExit(main())
