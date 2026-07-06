#!/usr/bin/env python3
"""Re-crop the Fuji Bing aerial from the already-cached z19 tiles, applying a
small registration-compensation shift so the cropped CONTENT lines up with
satellite_meta.json's bbox georeference (which satellite.jpg / GPS ground
truth at Fuji already correctly matches).

Background: fetch_bing_tiles.py derives its crop window purely from standard
Web Mercator math (same formulas Esri/OSM slippy tiles use). Phase-correlation
against the trusted Esri satellite.jpg (see scratchpad bing/analyze_shift.py,
shift_fuji.json) showed the naive Bing crop's content sits appreciably off
from where the bbox says it should be:
    median_north_m = -1.049  (Bing content ~1.05m further SOUTH than truth)
    median_east_m  = -3.305  (Bing content ~3.31m further WEST than truth)
This is consistent with Bing's own tile content having a small fixed
registration bias relative to standard Web Mercator (not a bug in our crop
math -- the crop-rounding residual logged by fetch_bing_tiles.py was <0.05m).

Fix: translate the crop window (not the bbox math) by the amount needed to
"give back" the missing north/east content, i.e. shift crop start DOWN by
1.049m (compensating the south bias) and LEFT/west by 3.305m (compensating
the west bias), converted to native tile-canvas pixels. Output dimensions are
kept IDENTICAL to the original crop (same width/height, just translated).

This script re-stitches the canvas from the existing tile cache only
(pipeline/cache/bing_fuji_z19/) -- no network calls needed unless the shifted
crop window needs tiles outside the previously-cached tile range, in which
case it fetches just the missing tiles via fetch_bing_tiles.get_tile (same
cache dir, resume-safe).

Usage:
    pipeline/.venv-aim/bin/python pipeline/recrop_bing_fuji.py
    pipeline/.venv-aim/bin/python pipeline/recrop_bing_fuji.py --dry-run
    pipeline/.venv-aim/bin/python pipeline/recrop_bing_fuji.py \
        --shift-north-m -1.049291525013579 --shift-east-m -3.3052648985556554
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_bing_tiles as fbt  # noqa: E402
from PIL import Image  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKS_ROOT = REPO_ROOT / "public" / "data" / "tracks"
CACHE_ROOT = Path(__file__).resolve().parent / "cache"
EARTH_METERS_PER_DEGREE = 111_320.0

TILE = fbt.TILE_SIZE
ZOOM = 19
TRACK = "fuji"

# Measured via scratchpad/bing/analyze_shift.py --track fuji (median across 5
# interior crops, phase correlation of Esri vs Bing resampled onto Esri's
# pixel grid). See shift_fuji.json. Bing content sits SOUTH and WEST of where
# the bbox georeference says it should be, by this many metres.
DEFAULT_SHIFT_NORTH_M = -1.049291525013579
DEFAULT_SHIFT_EAST_M = -3.3052648985556554


def load_bbox(track: str) -> dict:
    meta = json.loads((TRACKS_ROOT / track / "satellite_meta.json").read_text())
    return meta["bbox"]


def compute_original_crop(bbox: dict, zoom: int):
    px_min_lng = fbt.lng_to_tile_x(bbox["minLng"], zoom) * TILE
    px_max_lng = fbt.lng_to_tile_x(bbox["maxLng"], zoom) * TILE
    px_north = fbt.lat_to_tile_y(bbox["maxLat"], zoom) * TILE
    px_south = fbt.lat_to_tile_y(bbox["minLat"], zoom) * TILE

    x_min = math.floor(px_min_lng / TILE)
    x_max = math.floor(px_max_lng / TILE)
    y_min = math.floor(px_north / TILE)
    y_max = math.floor(px_south / TILE)

    crop_left = px_min_lng - x_min * TILE
    crop_top = px_north - y_min * TILE
    crop_right = px_max_lng - x_min * TILE
    crop_bottom = px_south - y_min * TILE
    crop_box = (round(crop_left), round(crop_top), round(crop_right), round(crop_bottom))

    return {
        "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
        "crop_box": crop_box,
    }


def build_canvas(x_min, x_max, y_min, y_max, zoom, cache_dir) -> Image.Image:
    n_x = x_max - x_min + 1
    n_y = y_max - y_min + 1
    canvas = Image.new("RGB", (n_x * TILE, n_y * TILE))
    total = n_x * n_y
    done = 0
    placeholders = 0
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            tile_img, is_placeholder = fbt.get_tile(zoom, tx, ty, cache_dir)
            if is_placeholder:
                placeholders += 1
            canvas.paste(tile_img, ((tx - x_min) * TILE, (ty - y_min) * TILE))
            done += 1
    print(f"  Canvas built: {n_x}x{n_y} tiles ({done} total, {placeholders} placeholders)")
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shift-north-m", type=float, default=DEFAULT_SHIFT_NORTH_M,
                     help="Measured north_m bias (Bing minus truth); negative means Bing is south of truth")
    ap.add_argument("--shift-east-m", type=float, default=DEFAULT_SHIFT_EAST_M,
                     help="Measured east_m bias (Bing minus truth); negative means Bing is west of truth")
    ap.add_argument("--jpeg-quality", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true", help="Compute and print, do not write output")
    ap.add_argument("--output", type=Path, default=TRACKS_ROOT / TRACK / "satellite_bing.jpg")
    args = ap.parse_args()

    bbox = load_bbox(TRACK)
    mid_lat = (bbox["minLat"] + bbox["maxLat"]) / 2.0

    orig = compute_original_crop(bbox, ZOOM)
    crop_box = orig["crop_box"]
    width = crop_box[2] - crop_box[0]
    height = crop_box[3] - crop_box[1]
    print(f"Original tile range: x[{orig['x_min']},{orig['x_max']}] y[{orig['y_min']},{orig['y_max']}]")
    print(f"Original crop box (canvas-relative): {crop_box}  size {width}x{height}")

    native_mpp_x = (bbox["maxLng"] - bbox["minLng"]) * EARTH_METERS_PER_DEGREE * math.cos(math.radians(mid_lat)) / width
    native_mpp_y = (bbox["maxLat"] - bbox["minLat"]) * EARTH_METERS_PER_DEGREE / height
    print(f"Native resolution: {native_mpp_x:.5f} x {native_mpp_y:.5f} m/px")

    # Bing content sits (shift_north_m, shift_east_m) away from truth. To
    # compensate, shift the crop window SOUTH by -shift_north_m and EAST by
    # -shift_east_m... wait: derivation (see module docstring / task notes):
    #   shift_south_m = -shift_north_m   (if Bing is south of truth i.e.
    #                                      shift_north_m<0, we must move the
    #                                      crop window further south/down to
    #                                      "catch up" with the missing content)
    #   shift_east_m  = shift_east_m     (if Bing is west of truth i.e.
    #                                      shift_east_m<0, the crop's east/west
    #                                      position must move west, i.e. crop
    #                                      column decreases -- same sign as
    #                                      the measured east_m itself)
    shift_south_m = -args.shift_north_m
    shift_east_m = args.shift_east_m

    shift_row_px = shift_south_m / native_mpp_y   # + => move crop down
    shift_col_px = shift_east_m / native_mpp_x    # - => move crop left (west)
    print(f"Applying shift: row(south)={shift_row_px:+.3f}px  col(east)={shift_col_px:+.3f}px "
          f"(south={shift_south_m:+.3f}m east={shift_east_m:+.3f}m)")

    new_left_f = crop_box[0] + shift_col_px
    new_top_f = crop_box[1] + shift_row_px
    new_left = round(new_left_f)
    new_top = round(new_top_f)
    new_right = new_left + width
    new_bottom = new_top + height
    print(f"New crop box (canvas-relative): ({new_left},{new_top},{new_right},{new_bottom}) size {width}x{height}")

    canvas_w = (orig["x_max"] - orig["x_min"] + 1) * TILE
    canvas_h = (orig["y_max"] - orig["y_min"] + 1) * TILE

    x_min, x_max = orig["x_min"], orig["x_max"]
    y_min, y_max = orig["y_min"], orig["y_max"]
    extra_left = extra_top = extra_right = extra_bottom = 0
    if new_left < 0:
        extra_left = math.ceil(-new_left / TILE)
    if new_top < 0:
        extra_top = math.ceil(-new_top / TILE)
    if new_right > canvas_w:
        extra_right = math.ceil((new_right - canvas_w) / TILE)
    if new_bottom > canvas_h:
        extra_bottom = math.ceil((new_bottom - canvas_h) / TILE)

    if extra_left or extra_top or extra_right or extra_bottom:
        print(f"WARNING: shifted crop needs extra tiles: left={extra_left} top={extra_top} "
              f"right={extra_right} bottom={extra_bottom} -- fetching via cache mechanism")
        x_min -= extra_left
        x_max += extra_right
        y_min -= extra_top
        y_max += extra_bottom
        # Re-express crop box relative to the (possibly expanded) canvas origin
        new_left += extra_left * TILE
        new_top += extra_top * TILE
        new_right = new_left + width
        new_bottom = new_top + height
        canvas_w = (x_max - x_min + 1) * TILE
        canvas_h = (y_max - y_min + 1) * TILE
    else:
        print("Shifted crop fits entirely within the already-cached tile range -- no fetch needed.")

    if args.dry_run:
        print("[dry-run] stopping before canvas build/save")
        return 0

    cache_dir = CACHE_ROOT / f"bing_{TRACK}_z{ZOOM}"
    print(f"Building canvas from cache dir {cache_dir} ...")
    canvas = build_canvas(x_min, x_max, y_min, y_max, ZOOM, cache_dir)

    new_crop_box = (new_left, new_top, new_right, new_bottom)
    print(f"Cropping to {new_crop_box} (expect size {width}x{height})")
    cropped = canvas.crop(new_crop_box)
    assert cropped.size == (width, height), f"unexpected crop size {cropped.size} != {(width, height)}"

    out_path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out_path, format="JPEG", quality=args.jpeg_quality, optimize=True, progressive=True)
    print(f"Saved: {out_path} ({cropped.size[0]}x{cropped.size[1]}px, {out_path.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
