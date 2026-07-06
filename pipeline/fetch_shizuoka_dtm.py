#!/usr/bin/env python3
"""Rebuild Fuji Speedway's terrain.png from VIRTUAL SHIZUOKA 2019 LiDAR ground points.

Fuji's terrain.png ships as an AWS/Mapzen Terrarium z15 raster (~3.7 m/px
effective). This script instead builds a ~1 m DTM from the VIRTUAL SHIZUOKA
2019 airborne-LiDAR open dataset (静岡県, CC BY 4.0). It:

  1. Reads the exact satellite_meta.json bbox (verbatim output contract).
  2. Fetches the LP00 index vector tile(s) covering the bbox
     (Mapbox Vector Tile PBF, "Ground" layer), whose features carry MESH_NO and
     a direct .zip URL for each 国土基本図 mesh's point cloud.
  3. Downloads the bbox-intersecting mesh zips into pipeline/cache/shizuoka_lp/
     (size-verified, resumable, retried — same idioms as fetch_usgs_dem.py).
  4. Reads each LAS, keeps GROUND-classified points (ASPRS class 2 — see below),
     reprojects EPSG:6676 -> EPSG:3857, and bins them into a node-registered
     ~1 m grid (cell mean).
  5. Fills voids (rasterio.fill.fillnodata), Terrarium-encodes, writes
     terrain.png + terrain_meta.json (old z15 backed up to *.z15.bak.*).

Why filter to ground class (deviation from spec, documented in REPORT.md):
The spec assumed the "Ground" product was pre-classified to bare earth only, so
"no class filter needed". The real LAS carries BOTH class 1 (unclassified,
~70%) and class 2 (ground, ~30%); class-1 points sit up to ~14 m above ground
(tree canopy / structures). Averaging all points would inflate the DTM by
several metres under vegetation — the opposite of a bare-earth surface. Keeping
class 2 (~19.7 pts/m^2, only ~1.8% empty 1 m cells) is the faithful way to
honour the spec's stated goal of a Ground/地表 DTM. Use --all-classes to
override.

Why EPSG:3857 + node registration (identical contract to fetch_usgs_dem.py):
groundMath.latLngToTerrainUv maps a point to UV where u is LINEAR in longitude
and v is LINEAR in Web-Mercator y, then bilinearSample maps u,v -> pixel
(u*(W-1), v*(H-1)). So the destination grid is a regular EPSG:3857 grid whose
CORNER PIXEL CENTRES sit exactly on the bbox corners: column 0 center = minLng,
column W-1 center = maxLng, row 0 center = maxLat (north), row H-1 = minLat.

VIRTUAL SHIZUOKA CRS note: the LAS files carry NO CRS (no GeoKey VLR). Per the
dataset spec (平面直角座標系第8系, JGD2011) we assume EPSG:6676 with
(X=easting, Y=northing). Verified 2026-07-05: a sample point round-trips to
(138.923E, 35.369N) inside the Fuji bbox, and its ground Z (580.5 m) matches the
GSI 1 m DEM to 0.02 m — orthometric, no ellipsoidal offset.

Usage:
    python3 pipeline/fetch_shizuoka_dtm.py
    python3 pipeline/fetch_shizuoka_dtm.py --skip-download   # use cached zips
    python3 pipeline/fetch_shizuoka_dtm.py --target-res 1.25 # coarser grid
"""

from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def _pip_install(pkg: str) -> None:
    print(f"{pkg} not found, installing with 'pip install --user {pkg}'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", pkg], check=True)


try:
    import numpy as np
except ImportError:
    _pip_install("numpy")
    import numpy as np

try:
    from rasterio.fill import fillnodata
except ImportError:
    _pip_install("rasterio")
    from rasterio.fill import fillnodata

try:
    from PIL import Image
except ImportError:
    _pip_install("pillow")
    from PIL import Image

try:
    import mapbox_vector_tile
except ImportError:
    _pip_install("mapbox-vector-tile")
    import mapbox_vector_tile

try:
    import laspy
except ImportError:
    _pip_install("laspy[lazrs]")
    import laspy

try:
    from pyproj import Transformer
except ImportError:
    _pip_install("pyproj")
    from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "pipeline"
FUJI_DIR = PROJECT_ROOT / "public" / "data" / "tracks" / "fuji"
SATELLITE_META_PATH = FUJI_DIR / "satellite_meta.json"
OUT_IMAGE_PATH = FUJI_DIR / "terrain.png"
OUT_META_PATH = FUJI_DIR / "terrain_meta.json"
BACKUP_IMAGE_PATH = FUJI_DIR / "terrain.z15.bak.png"
BACKUP_META_PATH = FUJI_DIR / "terrain_meta.z15.bak.json"
LP_CACHE_DIR = PIPELINE_DIR / "cache" / "shizuoka_lp"

# Verified 2026-07-05 (Fable 5) with real index data. The 2019 dataset (LP00
# index) covers Fuji Speedway; the 2021 (LP22) index does NOT reach the FSW area
# to the west. Do not swap datasets.
INDEX_URL_TEMPLATE = (
    "https://gic-shizuoka.s3.ap-northeast-1.amazonaws.com/2020/Vectortile2025/LP00/{z}/{x}/{y}.pbf"
)
INDEX_ZOOM = 12
INDEX_LAYER = "Ground"

USER_AGENT = "replay-viewer-v2 shizuoka-dtm fetch/1.0"
SRC_CRS = "EPSG:6676"  # JGD2011 / Japan Plane Rectangular CS VIII (assumed; no CRS in LAS)
DST_CRS = "EPSG:3857"
GROUND_CLASS = 2  # ASPRS classification code for ground / bare earth
RETRIES = 4
DOWNLOAD_CHUNK = 8 * 1024 * 1024
POINT_CHUNK = 4_000_000  # LAS points processed per chunk (memory bound)


# ---------- Slippy-tile <-> lng/lat helpers (Web Mercator) ----------

def lnglat_to_tile_frac(lng: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lng + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_frac_to_lnglat(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lng = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lng, lat


# ---------- Download (cached, resumable, size-verified) ----------

def content_length(url: str) -> int | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def download_file(url: str, dest: Path, min_cached_size: int = 1000) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = content_length(url)
    if dest.exists() and expected is not None and dest.stat().st_size == expected:
        print(f"  Cached (verified {dest.stat().st_size / 1e6:.1f} MB): {dest.name}")
        return dest
    if dest.exists() and expected is None and dest.stat().st_size > min_cached_size:
        print(f"  Cached (size unverified, {dest.stat().st_size / 1e6:.1f} MB): {dest.name}")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=180) as response, part.open("wb") as handle:
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


# ---------- Index PBF -> bbox-intersecting mesh zip URLs ----------

def find_mesh_urls(bbox: dict, cache_dir: Path, skip_download: bool) -> list[tuple[str, str]]:
    z = INDEX_ZOOM
    x0f, y0f = lnglat_to_tile_frac(bbox["minLng"], bbox["maxLat"], z)  # NW
    x1f, y1f = lnglat_to_tile_frac(bbox["maxLng"], bbox["minLat"], z)  # SE
    tx_min, tx_max = int(math.floor(x0f)), int(math.floor(x1f))
    ty_min, ty_max = int(math.floor(y0f)), int(math.floor(y1f))
    print(f"Index tiles z{z}: x {tx_min}..{tx_max}, y {ty_min}..{ty_max}")

    found: dict[str, str] = {}
    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = INDEX_URL_TEMPLATE.format(z=z, x=tx, y=ty)
            dest = cache_dir / f"index_{z}_{tx}_{ty}.pbf"
            if skip_download and not dest.exists():
                print(f"ERROR: --skip-download but index {dest.name} is missing.", file=sys.stderr)
                raise SystemExit(1)
            if not dest.exists():
                print(f"Fetching index {url}")
                download_file(url, dest)
            data = dest.read_bytes()
            tile = mapbox_vector_tile.decode(data)
            layer = tile.get(INDEX_LAYER)
            if not layer:
                print(f"  WARNING: index tile {tx}/{ty} has no '{INDEX_LAYER}' layer", file=sys.stderr)
                continue
            extent = layer["extent"]
            for feat in layer["features"]:
                geom = feat["geometry"]
                if geom["type"] == "Polygon":
                    rings = geom["coordinates"]
                elif geom["type"] == "MultiPolygon":
                    rings = [ring for poly in geom["coordinates"] for ring in poly]
                else:
                    continue
                # Convert tile-local (y-up) verts -> lng/lat, then bbox-overlap test.
                lngs: list[float] = []
                lats: list[float] = []
                for ring in rings:
                    for lx, ly_up in ring:
                        gx = tx + lx / extent
                        gy = ty + (extent - ly_up) / extent  # y-up -> y-down
                        lng, lat = tile_frac_to_lnglat(gx, gy, z)
                        lngs.append(lng)
                        lats.append(lat)
                if not lngs:
                    continue
                if (min(lngs) <= bbox["maxLng"] and max(lngs) >= bbox["minLng"]
                        and min(lats) <= bbox["maxLat"] and max(lats) >= bbox["minLat"]):
                    props = feat["properties"]
                    found[props["MESH_NO"]] = props["URL"]

    meshes = sorted(found.items())
    print(f"bbox-intersecting mesh cells: {len(meshes)}")
    return meshes


# ---------- LAS -> node-registered EPSG:3857 grid (ground-point binning) ----------

def build_grid_dims(bbox: dict) -> dict:
    min_lng, max_lng = bbox["minLng"], bbox["maxLng"]
    min_lat, max_lat = bbox["minLat"], bbox["maxLat"]
    mid_lat = (min_lat + max_lat) / 2.0
    dxs, dys = Transformer.from_crs("EPSG:4326", DST_CRS, always_xy=True).transform(
        [min_lng, max_lng], [max_lat, min_lat]
    )
    x_min, x_max = dxs[0], dxs[1]
    y_north, y_south = dys[0], dys[1]  # y_north <-> maxLat (row 0)
    return {
        "x_min": x_min, "x_max": x_max, "y_north": y_north, "y_south": y_south,
        "cos_lat": math.cos(math.radians(mid_lat)),
    }


def read_las_from_zip(zip_path: Path) -> "laspy.LasData":
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        las_names = [n for n in names if n.lower().endswith((".las", ".laz"))]
        if las_names:
            return laspy.read(io.BytesIO(zf.read(las_names[0])))
        txt_names = [n for n in names if n.lower().endswith((".txt", ".csv", ".xyz"))]
        if txt_names:
            raise NotImplementedError(
                f"{zip_path.name} contains a text point file ({txt_names[0]}); "
                "add a text parser if this occurs."
            )
    raise RuntimeError(f"No .las/.laz/.txt point file found in {zip_path.name}")


def build_terrain(bbox: dict, zip_paths: list[Path], target_res: float,
                  ground_only: bool) -> tuple[np.ndarray, dict]:
    dims = build_grid_dims(bbox)
    x_min, x_max = dims["x_min"], dims["x_max"]
    y_north, y_south = dims["y_north"], dims["y_south"]
    cos_lat = dims["cos_lat"]

    ground_w = (x_max - x_min) * cos_lat  # EPSG:3857 metres inflated by 1/cos(lat)
    ground_h = (y_north - y_south) * cos_lat
    width = max(2, round(ground_w / target_res))
    height = max(2, round(ground_h / target_res))
    dx = (x_max - x_min) / (width - 1)  # node-registration pixel-centre spacing
    dy = (y_north - y_south) / (height - 1)
    print(f"Destination grid (EPSG:3857, node-registered): {width} x {height} px")
    print(f"  ground resolution ~{ground_w / (width - 1):.3f} m/px (E-W), "
          f"~{ground_h / (height - 1):.3f} m/px (N-S), target {target_res} m")

    ncells = width * height
    sum_grid = np.zeros(ncells, dtype=np.float64)
    cnt_grid = np.zeros(ncells, dtype=np.int64)
    transformer = Transformer.from_crs(SRC_CRS, DST_CRS, always_xy=True)

    class_hist: dict[int, int] = {}
    total_pts = 0
    used_pts = 0
    for i, zp in enumerate(zip_paths, start=1):
        las = read_las_from_zip(zp)
        n = las.header.point_count
        total_pts += n
        cls_all = np.asarray(las.classification)
        vals, counts = np.unique(cls_all, return_counts=True)
        for v, c in zip(vals.tolist(), counts.tolist()):
            class_hist[v] = class_hist.get(v, 0) + c
        # process in chunks to bound peak memory
        x_all = np.asarray(las.x)
        y_all = np.asarray(las.y)
        z_all = np.asarray(las.z)
        for start in range(0, n, POINT_CHUNK):
            end = min(start + POINT_CHUNK, n)
            sx = x_all[start:end]
            sy = y_all[start:end]
            sz = z_all[start:end]
            if ground_only:
                keep = cls_all[start:end] == GROUND_CLASS
                sx, sy, sz = sx[keep], sy[keep], sz[keep]
            if sx.size == 0:
                continue
            mx, my = transformer.transform(sx, sy)
            col = np.rint((mx - x_min) / dx).astype(np.int64)
            row = np.rint((y_north - my) / dy).astype(np.int64)
            inside = (col >= 0) & (col < width) & (row >= 0) & (row < height)
            if not inside.any():
                continue
            cell = row[inside] * width + col[inside]
            sum_grid += np.bincount(cell, weights=sz[inside], minlength=ncells)
            cnt_grid += np.bincount(cell, minlength=ncells)
            used_pts += int(inside.sum())
        del las, x_all, y_all, z_all, cls_all
        print(f"  [{i}/{len(zip_paths)}] {zp.name}: {n:,} pts "
              f"(kept-in-bbox running total {used_pts:,})")

    print(f"Classification histogram (all read points): "
          f"{ {int(k): int(v) for k, v in sorted(class_hist.items())} }")
    print(f"Total points read: {total_pts:,}; binned into grid: {used_pts:,} "
          f"({'ground class %d only' % GROUND_CLASS if ground_only else 'all classes'})")

    filled = cnt_grid > 0
    heights = np.full(ncells, np.nan, dtype=np.float32)
    heights[filled] = (sum_grid[filled] / cnt_grid[filled]).astype(np.float32)
    heights = heights.reshape(height, width)

    void_mask = ~np.isfinite(heights)
    n_void = int(void_mask.sum())
    void_rate = n_void / ncells
    print(f"Empty (no-point) cells BEFORE fill: {n_void} / {ncells} ({void_rate * 100:.2f}%)")
    if void_rate > 0.30:
        print("WARNING: empty-cell rate > 30% — spec suggests coarsening --target-res "
              "(1.25-1.5). Continuing with fill; see REPORT.", file=sys.stderr)

    if n_void:
        valid = filled.reshape(height, width).astype(np.uint8)
        heights = fillnodata(heights, mask=valid, max_search_distance=200.0, smoothing_iterations=0)
        still_void = ~np.isfinite(heights)
        if still_void.any():
            heights[still_void] = float(np.nanmean(heights))
            print(f"  {int(still_void.sum())} cells could not be interpolated; filled with mean")

    finite = heights[np.isfinite(heights)]
    corners = {
        "NW(row0,col0)": float(heights[0, 0]),
        "NE(row0,colW-1)": float(heights[0, width - 1]),
        "SW(rowH-1,col0)": float(heights[height - 1, 0]),
        "SE(rowH-1,colW-1)": float(heights[height - 1, width - 1]),
    }
    print(f"Corner heights (m ASL): {corners}")
    stats = {
        "width": width, "height": height,
        "n_void": n_void, "void_rate": void_rate,
        "min": float(finite.min()), "max": float(finite.max()), "mean": float(finite.mean()),
        "res_ew": ground_w / (width - 1), "res_ns": ground_h / (height - 1),
        "class_hist": {int(k): int(v) for k, v in class_hist.items()},
        "total_pts": total_pts, "used_pts": used_pts, "corners": corners,
    }
    print(f"Elevation range: {stats['min']:.2f} .. {stats['max']:.2f} m ASL "
          f"(mean {stats['mean']:.2f})")
    return heights, stats


def encode_terrarium(heights: np.ndarray) -> Image.Image:
    """Terrarium RGB: height = R*256 + G + B/256 - 32768 (1/256 m precision)."""
    clamped = np.clip(heights, -32768.0, 32767.0)
    t = np.rint((clamped + 32768.0) * 256.0).astype(np.int64)
    t = np.clip(t, 0, (1 << 24) - 1)
    r = ((t >> 16) & 0xFF).astype(np.uint8)
    g = ((t >> 8) & 0xFF).astype(np.uint8)
    b = (t & 0xFF).astype(np.uint8)
    return Image.fromarray(np.stack([r, g, b], axis=-1), mode="RGB")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Fuji terrain.png from VIRTUAL SHIZUOKA 2019 LP ground points."
    )
    parser.add_argument("--satellite-meta", type=Path, default=SATELLITE_META_PATH)
    parser.add_argument("--out-image", type=Path, default=OUT_IMAGE_PATH)
    parser.add_argument("--out-meta", type=Path, default=OUT_META_PATH)
    parser.add_argument("--cache-dir", type=Path, default=LP_CACHE_DIR)
    parser.add_argument("--target-res", type=float, default=1.0,
                        help="Target ground grid resolution in metres (default 1.0).")
    parser.add_argument("--all-classes", action="store_true",
                        help="Bin ALL points (skip ground-class filter). Default: ground class 2 only.")
    parser.add_argument("--skip-download", action="store_true", help="Use cached zips only.")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up existing terrain assets.")
    parser.add_argument("--source-label",
                        default="VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県)")
    args = parser.parse_args()

    if not args.satellite_meta.exists():
        print(f"ERROR: {args.satellite_meta} not found.", file=sys.stderr)
        return 1
    meta = json.loads(args.satellite_meta.read_text())
    bbox = meta["bbox"]
    print(f"Fuji bbox: {bbox}")

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    meshes = find_mesh_urls(bbox, args.cache_dir, args.skip_download)
    if not meshes:
        print("ERROR: no mesh cells intersect the bbox.", file=sys.stderr)
        return 1

    zip_paths: list[Path] = []
    for mesh_no, url in meshes:
        dest = args.cache_dir / f"{mesh_no}.zip"
        if args.skip_download:
            if not dest.exists():
                print(f"ERROR: --skip-download but {dest.name} is missing.", file=sys.stderr)
                return 1
            print(f"  Using cached: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        else:
            print(f"Fetching {mesh_no}: {url}")
            dest = download_file(url, dest, min_cached_size=1_000_000)
        zip_paths.append(dest)

    heights, stats = build_terrain(bbox, zip_paths, args.target_res,
                                   ground_only=not args.all_classes)
    image = encode_terrarium(heights)

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
        print(f"WARNING: terrain.png is {size_mb:.2f} MB (> 10 MB).", file=sys.stderr)

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
