#!/usr/bin/env python3
"""Fetch Bing Maps aerial ("Virtual Earth") tiles aligned to a track's
EXISTING satellite bbox, and stitch/crop them into a satellite_bing.jpg
companion image.

LOCAL EVALUATION ONLY. Bing tile access via the public
ecn.t{n}.tiles.virtualearth.net CDN endpoint (no API key, no attribution
flow -- unlike the official Bing Maps REST API) sits in a ToS gray zone.
This script exists purely to pull an independently-registered aerial image
for offline registration-accuracy comparisons against the existing Esri
World Imagery satellite.jpg; its output is not wired into the running app.

Unlike fetch_satellite_tiles.py (which derives its own bbox from
--min-lat/--max-lat/--min-lng/--max-lng plus a margin), this script reads
the bbox directly out of the track's already-published satellite_meta.json
so the Bing output is pixel-registered to the EXACT same geographic
rectangle as satellite.jpg -- critical because the viewer drapes every
satellite variant on one shared mesh built from that meta.

Usage:
    python3 pipeline/fetch_bing_tiles.py --track barber --zoom 20 --max-size 8192
    python3 pipeline/fetch_bing_tiles.py --track fuji --zoom 19
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import math
import sys
import time
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: pipeline/.venv-aim/bin/pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests is required. Install with: pipeline/.venv-aim/bin/pip install requests", file=sys.stderr)
    sys.exit(1)

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
DEFAULT_CACHE_ROOT = PIPELINE_DIR / "cache"
DEFAULT_TRACKS_ROOT = REPO_ROOT / "public" / "data" / "tracks"

TILE_SIZE = 256
BING_URL_TMPL = "https://ecn.t{sub}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=1"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
RETRIES_PER_TILE = 3
REQUEST_DELAY_S = 0.25  # ~4 req/s, polite to the CDN
EARTH_METERS_PER_DEGREE = 111_320.0
EARTH_CIRCUMFERENCE_M = 2 * math.pi * 6378137.0
SOFT_MAX_LONG_EDGE_PX = 8192
PLACEHOLDER_ABORT_FRACTION = 0.05

_subdomains = itertools.cycle([0, 1, 2, 3])
_session = requests.Session()


def lng_to_tile_x(lng: float, z: int) -> float:
    return (lng + 180.0) / 360.0 * (2 ** z)


def lat_to_tile_y(lat: float, z: int) -> float:
    lat_rad = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2 ** z)


def meters_per_pixel(lat: float, z: int) -> float:
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (TILE_SIZE * (2 ** z))


def tile_xy_to_quadkey(tx: int, ty: int, z: int) -> str:
    """Standard Bing Maps Tile System quadkey (Microsoft's published algorithm).
    Uses the same top-left-origin, y-increases-south tile scheme as OSM/Esri
    slippy tiles, so tx/ty computed via lng_to_tile_x/lat_to_tile_y above map
    directly onto Bing's quadkeys with no extra transform."""
    digits = []
    for i in range(z, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if tx & mask:
            digit += 1
        if ty & mask:
            digit += 2
        digits.append(str(digit))
    return "".join(digits)


def download_tile_bytes(z: int, x: int, y: int, retries: int = RETRIES_PER_TILE) -> tuple[bytes, bool]:
    quadkey = tile_xy_to_quadkey(x, y, z)
    last_err: Exception | None = None
    for attempt in range(retries):
        sub = next(_subdomains)
        url = BING_URL_TMPL.format(sub=sub, quadkey=quadkey)
        try:
            resp = _session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            is_placeholder = resp.headers.get("X-VE-Tile-Info", "").strip().lower() == "no-tile"
            return resp.content, is_placeholder
        except requests.RequestException as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Failed to download tile after {retries} tries: {url} ({last_err})")


def get_tile(z: int, x: int, y: int, cache_dir: Path) -> tuple[Image.Image, bool]:
    """Return (tile_image, is_placeholder). A sidecar {x}_{y}.status.json
    records placeholder status alongside the cached bytes so re-runs are
    resume-safe without re-requesting (and re-throttling on) tiles that were
    already classified on a prior run."""
    tile_path = cache_dir / f"{x}_{y}.jpeg"
    status_path = cache_dir / f"{x}_{y}.status.json"
    if tile_path.exists() and status_path.exists():
        data = tile_path.read_bytes()
        status = json.loads(status_path.read_text())
        is_placeholder = bool(status.get("placeholder", False))
    else:
        data, is_placeholder = download_tile_bytes(z, x, y)
        cache_dir.mkdir(parents=True, exist_ok=True)
        tile_path.write_bytes(data)
        status_path.write_text(json.dumps({"placeholder": is_placeholder, "bytes": len(data)}))
        time.sleep(REQUEST_DELAY_S)
    return Image.open(io.BytesIO(data)).convert("RGB"), is_placeholder


def load_track_bbox(track: str, tracks_root: Path) -> dict:
    meta_path = tracks_root / track / "satellite_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"{meta_path} not found -- expected an existing satellite_meta.json to align to"
        )
    meta = json.loads(meta_path.read_text())
    if meta.get("mercator") is False:
        raise RuntimeError(
            f"{meta_path} has mercator=false; this script assumes the standard "
            "Web Mercator UV mapping used by groundMath.ts"
        )
    return meta


def fetch_and_stitch(bbox: dict, zoom: int, cache_dir: Path, abort_fraction: float):
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
    placeholders: list[tuple[int, int]] = []
    done = 0
    t0 = time.time()
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            tile, is_placeholder = get_tile(zoom, tx, ty, cache_dir)
            if is_placeholder:
                placeholders.append((tx, ty))
            canvas.paste(tile, ((tx - x_min) * TILE_SIZE, (ty - y_min) * TILE_SIZE))
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  Downloaded/cached {done}/{total} tiles ({time.time() - t0:.0f}s elapsed)")

    frac = len(placeholders) / total if total else 0.0
    if placeholders:
        preview = placeholders[:20]
        print(
            f"WARNING: {len(placeholders)}/{total} tiles ({frac:.1%}) are Bing "
            f"'no-tile' placeholders: {preview}{'...' if len(placeholders) > 20 else ''}"
        )
    if frac > abort_fraction:
        raise RuntimeError(
            f"ABORT: {frac:.1%} of tiles are 'no-tile' placeholders (threshold {abort_fraction:.1%}). "
            "Bing has no real imagery at this zoom/bbox -- try a lower --zoom."
        )

    # Crop to the EXACT bbox in continuous global-pixel space at this zoom,
    # rounding each edge to the nearest integer pixel. The rounding residual
    # (< 1 native pixel by construction) is returned so the caller can report
    # it in metres.
    crop_left = px_min_lng - x_min * TILE_SIZE
    crop_top = px_north - y_min * TILE_SIZE
    crop_right = px_max_lng - x_min * TILE_SIZE
    crop_bottom = px_south - y_min * TILE_SIZE
    crop_box = (round(crop_left), round(crop_top), round(crop_right), round(crop_bottom))
    residual_px = {
        "left": crop_left - crop_box[0],
        "top": crop_top - crop_box[1],
        "right": crop_right - crop_box[2],
        "bottom": crop_bottom - crop_box[3],
    }

    cropped = canvas.crop(crop_box)
    print(f"Cropped to exact bbox: {cropped.size[0]} x {cropped.size[1]}px (crop box {crop_box})")
    return cropped, residual_px, total, len(placeholders)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch Bing aerial tiles aligned to an existing track's satellite bbox")
    p.add_argument("--track", required=True, help="Track directory name under public/data/tracks/, e.g. barber")
    p.add_argument("--zoom", type=int, required=True)
    p.add_argument(
        "--max-size", type=int, default=SOFT_MAX_LONG_EDGE_PX,
        help="Soft cap on the long edge in px; Lanczos-downscaled if exceeded",
    )
    p.add_argument("--jpeg-quality", type=int, default=90)
    p.add_argument("--tracks-root", type=Path, default=DEFAULT_TRACKS_ROOT)
    p.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    p.add_argument("--placeholder-abort-fraction", type=float, default=PLACEHOLDER_ABORT_FRACTION)
    p.add_argument("--request-delay", type=float, default=REQUEST_DELAY_S)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    global REQUEST_DELAY_S
    REQUEST_DELAY_S = args.request_delay

    meta = load_track_bbox(args.track, args.tracks_root)
    bbox = meta["bbox"]
    mid_lat = (bbox["minLat"] + bbox["maxLat"]) / 2.0
    print(f"Track '{args.track}': aligning to existing satellite_meta.json bbox {bbox}")
    print(f"(existing satellite.jpg is {meta['imageWidth']}x{meta['imageHeight']}px)")

    cache_dir = args.cache_root / f"bing_{args.track}_z{args.zoom}"

    image, residual_px, total, n_placeholder = fetch_and_stitch(
        bbox, args.zoom, cache_dir, args.placeholder_abort_fraction
    )

    mpp = meters_per_pixel(mid_lat, args.zoom)
    residual_m = {k: v * mpp for k, v in residual_px.items()}
    print("Crop rounding residual (must be < 1 native pixel):")
    for k in ("left", "top", "right", "bottom"):
        print(f"  {k}: {residual_px[k]:+.4f} px = {residual_m[k]:+.4f} m")
    max_residual_m = max(abs(v) for v in residual_m.values())
    print(f"  max |residual|: {max_residual_m:.4f} m (native pixel = {mpp:.4f} m)")
    if max_residual_m >= mpp:
        print(
            f"WARNING: max crop residual {max_residual_m:.4f} m exceeds one native pixel "
            f"({mpp:.4f} m) -- unexpected",
            file=sys.stderr,
        )

    w, h = image.size
    if max(w, h) > args.max_size:
        scale = args.max_size / max(w, h)
        new_size = (round(w * scale), round(h * scale))
        image = image.resize(new_size, Image.LANCZOS)
        print(f"Downsampled {w}x{h} -> {new_size[0]}x{new_size[1]} (soft cap {args.max_size}px)")

    out_image = args.tracks_root / args.track / "satellite_bing.jpg"
    out_image.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_image, format="JPEG", quality=args.jpeg_quality, optimize=True, progressive=True)

    eff_mpp_x = (
        (bbox["maxLng"] - bbox["minLng"]) * EARTH_METERS_PER_DEGREE * math.cos(math.radians(mid_lat))
        / image.size[0]
    )
    eff_mpp_y = (bbox["maxLat"] - bbox["minLat"]) * EARTH_METERS_PER_DEGREE / image.size[1]
    print(f"Saved: {out_image} ({image.size[0]}x{image.size[1]}px, {out_image.stat().st_size / 1024:.0f} KB)")
    print(f"Zoom used: {args.zoom}. Tiles: {total} total, {n_placeholder} placeholders.")
    print(f"Effective resolution: ~{eff_mpp_x:.4f} m/px (E-W) x ~{eff_mpp_y:.4f} m/px (N-S)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
