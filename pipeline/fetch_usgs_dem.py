#!/usr/bin/env python3
"""Rebuild Barber's terrain.png from a USGS 3DEP 1 m LiDAR DEM.

The default AWS Open Data Terrarium tiles used by fetch_terrain_tiles.py top
out at ~4 m/px effective resolution over Barber (zoom 15). This script instead
pulls the native 1 m 3DEP GeoTIFFs (2023 LiDAR, project AL_11County_B23),
mosaics them, reprojects to EPSG:3857 (Web Mercator), crops to the EXACT
satellite_meta.json bbox, and re-encodes as a Terrarium RGB height PNG so the
viewer's TerrainSampler.ts / groundMath.ts decode it unchanged.

Why EPSG:3857 and node registration: groundMath.latLngToTerrainUv maps a point
to a UV where u is LINEAR in longitude and v is LINEAR in Web-Mercator y, then
bilinearSample maps u,v -> pixel (u*(W-1), v*(H-1)). So the destination grid is
a regular grid in EPSG:3857 whose CORNER PIXEL CENTERS sit exactly on the bbox
corners: column 0 center = minLng, column W-1 center = maxLng, row 0 center =
maxLat (north), row H-1 center = minLat. This mirrors fetch_terrain_tiles.py's
"reuse satellite_meta.json's bbox verbatim" contract.

Mirrors the flow/idioms of fetch_terrain_tiles.py and fetch_bing_tiles.py:
argparse, default path constants, pipeline/cache/ caching (no re-download if a
complete copy exists), retries, and self-installing the one heavy dependency
(rasterio) with `pip install --user` when it is missing (like the Pillow flow
in fetch_terrain_tiles.py).

Usage:
    python3 pipeline/fetch_usgs_dem.py
    python3 pipeline/fetch_usgs_dem.py --skip-download   # use cached GeoTIFFs
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("numpy not found, installing with 'pip install --user numpy'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "numpy"], check=True)
    import numpy as np

try:
    import rasterio
    from rasterio.fill import fillnodata
    from rasterio.merge import merge
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject
    from rasterio.warp import transform as warp_transform
except ImportError:
    print("rasterio not found, installing with 'pip install --user rasterio'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "rasterio"], check=True)
    import rasterio
    from rasterio.fill import fillnodata
    from rasterio.merge import merge
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject
    from rasterio.warp import transform as warp_transform

try:
    from PIL import Image
except ImportError:
    print("Pillow not found, installing with 'pip install --user pillow'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pillow"], check=True)
    from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "pipeline"
BARBER_DIR = PROJECT_ROOT / "public" / "data" / "tracks" / "barber"
SATELLITE_META_PATH = BARBER_DIR / "satellite_meta.json"
OUT_IMAGE_PATH = BARBER_DIR / "terrain.png"
OUT_META_PATH = BARBER_DIR / "terrain_meta.json"
BACKUP_IMAGE_PATH = BARBER_DIR / "terrain.z15.bak.png"
BACKUP_META_PATH = BARBER_DIR / "terrain_meta.z15.bak.json"
DEM_CACHE_DIR = PIPELINE_DIR / "cache" / "usgs_dem"

# Verified 2026-07-05 via TNM API (dataset "Digital Elevation Model (DEM) 1 meter").
# Project AL_11County_B23 (2023 LiDAR, published 2026-06-09), UTM zone 16. The
# Barber bbox straddles the shared N-S edge of these two adjacent tiles.
DEM_URLS = [
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
    "AL_11County_B23/TIFF/USGS_1M_16_x53y371_AL_11County_B23.tif",
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
    "AL_11County_B23/TIFF/USGS_1M_16_x53y372_AL_11County_B23.tif",
]
# Fallback projects (same tile names) if AL_11County_B23 becomes unavailable:
#   AL_25Co_B2_2017, AL_JeffersonCo_2013
# Re-query TNM: https://tnmaccess.nationalmap.gov/api/v1/products?datasets=
#   Digital Elevation Model (DEM) 1 meter&bbox=-86.63,33.52,-86.60,33.54&outputFormat=JSON

USER_AGENT = "replay-viewer-v2 usgs-dem fetch/1.0"
DST_CRS = "EPSG:3857"
SRC_MARGIN_M = 80.0  # pad the source-CRS read window so bilinear edges have neighbours
RETRIES = 4
DOWNLOAD_CHUNK = 8 * 1024 * 1024


# ---------- Download (cached, resumable, size-verified) ----------

def content_length(url: str) -> int | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def download_dem(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = content_length(url)
    if dest.exists() and expected is not None and dest.stat().st_size == expected:
        print(f"  Cached (verified {dest.stat().st_size / 1e6:.1f} MB): {dest.name}")
        return dest
    if dest.exists() and expected is None and dest.stat().st_size > 50_000_000:
        print(f"  Cached (size unverified, {dest.stat().st_size / 1e6:.1f} MB): {dest.name}")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as handle:
                total = int(response.headers.get("Content-Length", expected or 0))
                got = 0
                next_report = 50_000_000
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    got += len(chunk)
                    if got >= next_report:
                        pct = f" ({got / total * 100:.0f}%)" if total else ""
                        print(f"    {dest.name}: {got / 1e6:.0f} MB{pct}")
                        next_report += 50_000_000
            if expected is not None and part.stat().st_size != expected:
                raise RuntimeError(f"size mismatch: got {part.stat().st_size}, expected {expected}")
            part.replace(dest)
            print(f"  Downloaded {dest.stat().st_size / 1e6:.1f} MB: {dest.name}")
            return dest
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as exc:
            last_err = exc
            print(f"  attempt {attempt}/{RETRIES} failed for {dest.name}: {exc}", file=sys.stderr)
            if part.exists():
                part.unlink()
            if attempt < RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to download {url} after {RETRIES} tries ({last_err})")


# ---------- Reproject + crop to the exact bbox in EPSG:3857 ----------

def build_terrain(bbox: dict, tif_paths: list[Path]) -> tuple[np.ndarray, dict]:
    min_lng, max_lng = bbox["minLng"], bbox["maxLng"]
    min_lat, max_lat = bbox["minLat"], bbox["maxLat"]
    mid_lat = (min_lat + max_lat) / 2.0

    srcs = [rasterio.open(p) for p in tif_paths]
    try:
        src_crs = srcs[0].crs
        src_nodata = srcs[0].nodata
        print(f"Source CRS: {src_crs}, nodata: {src_nodata}, tiles: {len(srcs)}")

        # Restrict the mosaic to a small padded window around the bbox (in the
        # source CRS) so we only touch the blocks we need, not two ~10k x 10k
        # tiles in full. Transform all four corners; UTM is not axis-aligned to
        # lat/lng, so take the min/max envelope, then pad.
        corner_lngs = [min_lng, max_lng, min_lng, max_lng]
        corner_lats = [max_lat, max_lat, min_lat, min_lat]
        xs, ys = warp_transform("EPSG:4326", src_crs, corner_lngs, corner_lats)
        s_left = min(xs) - SRC_MARGIN_M
        s_right = max(xs) + SRC_MARGIN_M
        s_bottom = min(ys) - SRC_MARGIN_M
        s_top = max(ys) + SRC_MARGIN_M

        mosaic, mosaic_transform = merge(srcs, bounds=(s_left, s_bottom, s_right, s_top))
    finally:
        for s in srcs:
            s.close()
    src_band = mosaic[0].astype(np.float32)
    print(f"Mosaic window (source CRS): {src_band.shape[1]} x {src_band.shape[0]} px")

    # Destination grid: node-registered to the bbox corners in EPSG:3857.
    dxs, dys = warp_transform("EPSG:4326", DST_CRS, [min_lng, max_lng], [max_lat, min_lat])
    x_min, x_max = dxs[0], dxs[1]
    y_north, y_south = dys[0], dys[1]  # y_north corresponds to maxLat (row 0)

    cos_lat = math.cos(math.radians(mid_lat))
    ground_w = (x_max - x_min) * cos_lat  # EPSG:3857 metres are inflated by 1/cos(lat)
    ground_h = (y_north - y_south) * cos_lat
    width = max(2, round(ground_w))
    height = max(2, round(ground_h))

    dx = (x_max - x_min) / (width - 1)   # pixel-centre spacing (node registration)
    dy = (y_north - y_south) / (height - 1)
    # rasterio Affine maps pixel (col,row) upper-left corner -> (x,y); shift the
    # origin by half a pixel so corner pixel CENTRES land on the bbox corners.
    dst_transform = Affine(dx, 0.0, x_min - dx / 2.0, 0.0, -dy, y_north + dy / 2.0)

    dst = np.full((height, width), np.nan, dtype=np.float32)
    reproject(
        source=src_band,
        destination=dst,
        src_transform=mosaic_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=DST_CRS,
        src_nodata=src_nodata,
        dst_nodata=float("nan"),
        resampling=Resampling.bilinear,
    )
    print(f"Reprojected to EPSG:3857 grid: {width} x {height} px")
    print(f"  ground resolution ~{ground_w / (width - 1):.3f} m/px (E-W), "
          f"~{ground_h / (height - 1):.3f} m/px (N-S)")

    # void / NoData fill: NaNs would encode to nonsense and trip the viewer's
    # corner sanity check, forcing a full fallback. Barber has ~no water.
    void_mask = ~np.isfinite(dst)
    n_void = int(void_mask.sum())
    if n_void:
        valid = (~void_mask).astype(np.uint8)
        dst = fillnodata(dst, mask=valid, max_search_distance=200.0, smoothing_iterations=0)
        still_void = ~np.isfinite(dst)
        if still_void.any():
            dst[still_void] = float(np.nanmean(dst))
            print(f"  {int(still_void.sum())} px could not be interpolated; filled with mean")
    print(f"Void/NoData pixels filled: {n_void} / {width * height}")

    finite = dst[np.isfinite(dst)]
    stats = {
        "width": width,
        "height": height,
        "n_void": n_void,
        "min": float(finite.min()),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
        "res_ew": ground_w / (width - 1),
        "res_ns": ground_h / (height - 1),
    }
    print(f"Elevation range: {stats['min']:.2f} .. {stats['max']:.2f} m (mean {stats['mean']:.2f})")
    return dst, stats


def encode_terrarium(heights: np.ndarray) -> Image.Image:
    """Terrarium RGB: height = R*256 + G + B/256 - 32768 (1/256 m precision)."""
    clamped = np.clip(heights, -32768.0, 32767.0)
    t = np.rint((clamped + 32768.0) * 256.0).astype(np.int64)
    t = np.clip(t, 0, (1 << 24) - 1)
    r = ((t >> 16) & 0xFF).astype(np.uint8)
    g = ((t >> 8) & 0xFF).astype(np.uint8)
    b = (t & 0xFF).astype(np.uint8)
    rgb = np.stack([r, g, b], axis=-1)
    return Image.fromarray(rgb, mode="RGB")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Barber terrain.png from a USGS 3DEP 1 m DEM.")
    parser.add_argument("--satellite-meta", type=Path, default=SATELLITE_META_PATH)
    parser.add_argument("--out-image", type=Path, default=OUT_IMAGE_PATH)
    parser.add_argument("--out-meta", type=Path, default=OUT_META_PATH)
    parser.add_argument("--cache-dir", type=Path, default=DEM_CACHE_DIR)
    parser.add_argument("--skip-download", action="store_true", help="Use cached GeoTIFFs only.")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up the existing terrain assets.")
    parser.add_argument("--source-label", default="USGS 3DEP 1m AL_11County_B23")
    args = parser.parse_args()

    if not args.satellite_meta.exists():
        print(f"ERROR: {args.satellite_meta} not found.", file=sys.stderr)
        return 1
    meta = json.loads(args.satellite_meta.read_text())
    bbox = meta["bbox"]
    print(f"Barber bbox: {bbox}")

    tif_paths: list[Path] = []
    for url in DEM_URLS:
        dest = args.cache_dir / url.rsplit("/", 1)[-1]
        if args.skip_download:
            if not dest.exists():
                print(f"ERROR: --skip-download but {dest} is missing.", file=sys.stderr)
                return 1
            print(f"  Using cached: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        else:
            print(f"Fetching {url}")
            dest = download_dem(url, dest)
        tif_paths.append(dest)

    heights, stats = build_terrain(bbox, tif_paths)
    image = encode_terrarium(heights)

    # Back up the existing z15 assets before overwriting (spec constraint).
    if not args.no_backup:
        if args.out_image.exists() and not BACKUP_IMAGE_PATH.exists():
            shutil.copy2(args.out_image, BACKUP_IMAGE_PATH)
            print(f"Backed up: {args.out_image.name} -> {BACKUP_IMAGE_PATH.name}")
        elif BACKUP_IMAGE_PATH.exists():
            print(f"Backup already exists, keeping it: {BACKUP_IMAGE_PATH.name}")
        if args.out_meta.exists() and not BACKUP_META_PATH.exists():
            shutil.copy2(args.out_meta, BACKUP_META_PATH)
            print(f"Backed up: {args.out_meta.name} -> {BACKUP_META_PATH.name}")
        elif BACKUP_META_PATH.exists():
            print(f"Backup already exists, keeping it: {BACKUP_META_PATH.name}")

    args.out_image.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out_image, "PNG", optimize=True)  # lossless: heights live in RGB
    size_mb = args.out_image.stat().st_size / 1e6
    print(f"Saved: {args.out_image} ({image.size[0]}x{image.size[1]}, {size_mb:.2f} MB)")
    if size_mb > 10.0:
        print(f"WARNING: terrain.png is {size_mb:.2f} MB (> 10 MB); consider compression tuning.", file=sys.stderr)

    out_meta = {
        "imageFile": args.out_image.name,
        "imageWidth": image.size[0],
        "imageHeight": image.size[1],
        "bbox": bbox,  # verbatim from satellite_meta.json
        "mercator": True,
        "encoding": "terrarium",
        "zoom": 22,  # unused by TS; nominal, ~1 m/px effective resolution
        "source": args.source_label,
    }
    args.out_meta.write_text(json.dumps(out_meta, indent=2) + "\n")
    print(f"Wrote: {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
