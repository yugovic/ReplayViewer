#!/usr/bin/env python3
"""Fetch Esri World Imagery satellite tiles for a track bbox and stitch them.

New in v2 (the old replay-viewer/fetch_satellite_tiles.py stitched a raw PNG
and stashed the bbox on the input track JSON; this version writes a
satellite.jpg + satellite_meta.json pair directly in the schema
convert_satellite.py produces, and crops the tile-aligned mosaic down to the
exact requested bbox in continuous pixel space -- the same approach
fetch_terrain_tiles.py uses so satellite_meta.json's bbox can be reused
verbatim by the terrain fetcher).

Individual tiles are cached under pipeline/cache/<cache-name>/<z>/<x>/<y>.jpg
so re-runs (or a later zoom fallback) don't re-fetch already-downloaded tiles.

Usage:
    python3 pipeline/fetch_satellite_tiles.py \\
        --min-lat 35.3647 --max-lat 35.3776 --min-lng 138.9187 --max-lng 138.9351 \\
        --margin 250 --zoom 18 \\
        --out-image public/data/tracks/fuji/satellite.jpg \\
        --out-meta public/data/tracks/fuji/satellite_meta.json \\
        --cache-name fuji
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: python3 -m pip install Pillow", file=sys.stderr)
    sys.exit(1)

import json

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_ROOT = PIPELINE_DIR / "cache" / "satellite_tiles"

TILE_SIZE = 256
ESRI_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
USER_AGENT = "replay-viewer-v2 satellite fetch/1.0"
EARTH_METERS_PER_DEGREE = 111_320.0
EARTH_CIRCUMFERENCE_M = 2 * math.pi * 6378137.0
RETRIES_PER_TILE = 3
REQUEST_DELAY_S = 0.25  # ~4 req/s, polite to the free Esri endpoint
SOFT_MAX_LONG_EDGE_PX = 8192

# A genuine "no imagery at this zoom" placeholder tile from Esri is a small,
# near-solid-gray JPEG (the "Map data not yet available" tile). Real aerial
# photos compress much less uniformly. Flag any candidate tile whose file
# size is suspiciously small so a bad zoom choice fails loudly instead of
# silently baking placeholder tiles into the mosaic.
BLANK_TILE_MAX_BYTES = 3000


def lng_to_tile_x(lng: float, z: int) -> float:
    return (lng + 180.0) / 360.0 * (2 ** z)


def lat_to_tile_y(lat: float, z: int) -> float:
    lat_rad = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2 ** z)


def tile_x_to_lng(x: float, z: int) -> float:
    return x / (2 ** z) * 360.0 - 180.0


def tile_y_to_lat(y: float, z: int) -> float:
    n = math.pi - 2.0 * math.pi * y / (2 ** z)
    return math.degrees(math.atan(math.sinh(n)))


def meters_per_pixel(lat: float, z: int) -> float:
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (TILE_SIZE * (2 ** z))


def download_tile_bytes(z: int, x: int, y: int, retries: int = RETRIES_PER_TILE) -> bytes:
    url = ESRI_URL.format(z=z, y=y, x=x)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Failed to download tile after {retries} tries: {url} ({last_err})")


def get_tile(z: int, x: int, y: int, cache_dir: Path) -> Image.Image:
    tile_path = cache_dir / str(z) / str(x) / f"{y}.jpg"
    if tile_path.exists():
        data = tile_path.read_bytes()
    else:
        data = download_tile_bytes(z, x, y)
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        tile_path.write_bytes(data)
        time.sleep(REQUEST_DELAY_S)
    if len(data) <= BLANK_TILE_MAX_BYTES:
        raise RuntimeError(
            f"Tile z{z}/x{x}/y{y} is only {len(data)} bytes -- looks like an Esri "
            "'no imagery available' placeholder, not real photography. Try a lower --zoom."
        )
    return Image.open(io.BytesIO(data)).convert("RGB")


def probe_tile(z: int, x: int, y: int, cache_dir: Path) -> tuple[bool, int]:
    """Return (looks_real, byte_size) without raising on a placeholder tile."""
    tile_path = cache_dir / str(z) / str(x) / f"{y}.jpg"
    if tile_path.exists():
        data = tile_path.read_bytes()
    else:
        data = download_tile_bytes(z, x, y)
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        tile_path.write_bytes(data)
        time.sleep(REQUEST_DELAY_S)
    return len(data) > BLANK_TILE_MAX_BYTES, len(data)


def fetch_and_stitch(
    bbox: dict[str, float], zoom: int, cache_dir: Path
) -> tuple[Image.Image, dict[str, float]]:
    min_lng, max_lng = bbox["minLng"], bbox["maxLng"]
    min_lat, max_lat = bbox["minLat"], bbox["maxLat"]

    px_min_lng = lng_to_tile_x(min_lng, zoom) * TILE_SIZE
    px_max_lng = lng_to_tile_x(max_lng, zoom) * TILE_SIZE
    px_north = lat_to_tile_y(max_lat, zoom) * TILE_SIZE  # smaller y = further north
    px_south = lat_to_tile_y(min_lat, zoom) * TILE_SIZE

    x_min = math.floor(px_min_lng / TILE_SIZE)
    x_max = math.floor(px_max_lng / TILE_SIZE)
    y_min = math.floor(px_north / TILE_SIZE)
    y_max = math.floor(px_south / TILE_SIZE)

    n_x = x_max - x_min + 1
    n_y = y_max - y_min + 1
    total = n_x * n_y
    print(f"Tile grid: {n_x} x {n_y} = {total} tiles at zoom {zoom}")

    canvas = Image.new("RGB", (n_x * TILE_SIZE, n_y * TILE_SIZE))
    done = 0
    t0 = time.time()
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            tile = get_tile(zoom, tx, ty, cache_dir)
            canvas.paste(tile, ((tx - x_min) * TILE_SIZE, (ty - y_min) * TILE_SIZE))
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  Downloaded/cached {done}/{total} tiles ({time.time() - t0:.0f}s elapsed)")

    # Crop the tile-aligned mosaic down to the exact requested bbox in
    # continuous pixel space (mirrors fetch_terrain_tiles.py).
    crop_box = (
        round(px_min_lng - x_min * TILE_SIZE),
        round(px_north - y_min * TILE_SIZE),
        round(px_max_lng - x_min * TILE_SIZE),
        round(px_south - y_min * TILE_SIZE),
    )
    cropped = canvas.crop(crop_box)
    print(f"Cropped to exact bbox: {cropped.size[0]} x {cropped.size[1]}px")

    exact_bbox = {
        "minLng": round(min_lng, 8),
        "maxLng": round(max_lng, 8),
        "minLat": round(min_lat, 8),
        "maxLat": round(max_lat, 8),
    }
    return cropped, exact_bbox


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch and stitch Esri World Imagery tiles for a track bbox")
    p.add_argument("--min-lat", type=float, required=True)
    p.add_argument("--max-lat", type=float, required=True)
    p.add_argument("--min-lng", type=float, required=True)
    p.add_argument("--max-lng", type=float, required=True)
    p.add_argument("--margin", type=float, default=250.0, help="Margin in metres added on every side")
    p.add_argument("--zoom", type=int, default=19, help="Preferred tile zoom; falls back automatically")
    p.add_argument(
        "--zoom-fallback",
        type=int,
        nargs="*",
        default=[18, 17],
        help="Zoom levels to try in order if --zoom's centre tile is a placeholder",
    )
    p.add_argument("--out-image", type=Path, required=True, help="Output satellite.jpg path")
    p.add_argument("--out-meta", type=Path, required=True, help="Output satellite_meta.json path")
    p.add_argument("--cache-name", default="satellite", help="Subdirectory under pipeline/cache/satellite_tiles/")
    p.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    p.add_argument("--jpeg-quality", type=int, default=90)
    p.add_argument("--max-long-edge", type=int, default=SOFT_MAX_LONG_EDGE_PX)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    mid_lat = (args.min_lat + args.max_lat) / 2.0
    dlat = args.margin / EARTH_METERS_PER_DEGREE
    dlng = args.margin / (EARTH_METERS_PER_DEGREE * math.cos(math.radians(mid_lat)))
    bbox = {
        "minLat": args.min_lat - dlat,
        "maxLat": args.max_lat + dlat,
        "minLng": args.min_lng - dlng,
        "maxLng": args.max_lng + dlng,
    }
    print(
        f"Bbox (+{args.margin:.0f} m margin): "
        f"lat [{bbox['minLat']:.6f}, {bbox['maxLat']:.6f}], lng [{bbox['minLng']:.6f}, {bbox['maxLng']:.6f}]"
    )

    cache_dir = args.cache_root / args.cache_name

    mid_lng = (bbox["minLng"] + bbox["maxLng"]) / 2.0
    zoom_candidates = [args.zoom, *args.zoom_fallback]
    chosen_zoom = None
    for z in zoom_candidates:
        cx = lng_to_tile_x(mid_lng, z)
        cy = lat_to_tile_y(mid_lat, z)
        try:
            ok, size = probe_tile(z, int(cx), int(cy), cache_dir)
        except RuntimeError as exc:
            print(f"  zoom {z}: probe failed ({exc})")
            continue
        resolution = meters_per_pixel(mid_lat, z)
        if ok:
            print(f"  zoom {z}: OK (probe tile {size} bytes, ~{resolution:.3f} m/px) -- using this zoom")
            chosen_zoom = z
            break
        print(f"  zoom {z}: placeholder tile ({size} bytes, ~{resolution:.3f} m/px) -- falling back")

    if chosen_zoom is None:
        print("ERROR: no zoom in the candidate list returned real imagery.", file=sys.stderr)
        return 1

    image, exact_bbox = fetch_and_stitch(bbox, chosen_zoom, cache_dir)

    w, h = image.size
    if max(w, h) > args.max_long_edge:
        scale = args.max_long_edge / max(w, h)
        new_size = (round(w * scale), round(h * scale))
        image = image.resize(new_size, Image.LANCZOS)
        print(f"Downsampled {w}x{h} -> {new_size[0]}x{new_size[1]} (soft cap {args.max_long_edge}px)")

    args.out_image.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out_image, format="JPEG", quality=args.jpeg_quality, optimize=True, progressive=True)
    print(f"Saved: {args.out_image} ({image.size[0]}x{image.size[1]}px, {args.out_image.stat().st_size / 1024:.0f} KB)")

    resolution = meters_per_pixel(mid_lat, chosen_zoom) * (max(w, h) / max(image.size))
    meta = {
        "imageFile": args.out_image.name,
        "imageWidth": image.size[0],
        "imageHeight": image.size[1],
        "bbox": exact_bbox,
        "mercator": True,
    }
    args.out_meta.parent.mkdir(parents=True, exist_ok=True)
    args.out_meta.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Wrote: {args.out_meta}")
    print(f"Zoom used: {chosen_zoom}, effective resolution ~{resolution:.3f} m/px at lat {mid_lat:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
