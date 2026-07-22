#!/usr/bin/env python3
"""Build a Fuji texture from VIRTUAL SHIZUOKA's 2019 LP orthophotos.

The public PBF index points to 20 cm ground-resolution orthophotos by Japanese
national-grid mesh. Only meshes intersecting satellite_meta.json are fetched.
They are warped to EPSG:3857 and the exact existing bbox, so the output can be
swapped into Replay without moving its ground mesh.

Dataset: VIRTUAL SHIZUOKA 2019 LP orthophoto, Shizuoka Prefecture
License: CC BY 4.0 / ODbL; source CRS: JGD2011 plane rectangular VIII
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
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


def _install(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


try:
    import numpy as np
except ImportError:
    _install("numpy")
    import numpy as np
try:
    import mapbox_vector_tile
except ImportError:
    _install("mapbox-vector-tile")
    import mapbox_vector_tile
try:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject, transform_bounds
except ImportError:
    _install("rasterio")
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject, transform_bounds
try:
    from PIL import Image
except ImportError:
    _install("pillow")
    from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
FUJI_DIR = ROOT / "public" / "data" / "tracks" / "fuji"
INDEX_URL = (
    "https://gic-shizuoka.s3.ap-northeast-1.amazonaws.com/"
    "2020/Vectortile2025/LPORTHO/{z}/{x}/{y}.pbf"
)
INDEX_ZOOM = 12
SRC_CRS = CRS.from_epsg(6676)
DST_CRS = CRS.from_epsg(3857)
USER_AGENT = "replay-viewer-v2 shizuoka-ortho/1.0"
SOURCE = "VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm (CC BY 4.0, Shizuoka Prefecture)"
SOURCE_URL = "https://www.geospatial.jp/ckan/dataset/shizuoka-2019-pointcloud"


def lnglat_to_tile(lng: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return ((lng + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def tile_to_lnglat(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return x / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi - 2 * math.pi * y / n)))


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  cached {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    error: Exception | None = None
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=240) as response, part.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
            part.replace(dest)
            print(f"  downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
            return dest
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            error = exc
            part.unlink(missing_ok=True)
            if attempt < 4:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"download failed: {url} ({error})")


def prop(properties: dict, name: str) -> str | None:
    for key, value in properties.items():
        if str(key).casefold() == name.casefold() and value is not None:
            return str(value)
    return None


def find_meshes(bbox: dict, cache: Path, offline: bool) -> list[tuple[str, str]]:
    z = INDEX_ZOOM
    x0, y0 = lnglat_to_tile(bbox["minLng"], bbox["maxLat"], z)
    x1, y1 = lnglat_to_tile(bbox["maxLng"], bbox["minLat"], z)
    found: dict[str, str] = {}
    for tx in range(math.floor(x0), math.floor(x1) + 1):
        for ty in range(math.floor(y0), math.floor(y1) + 1):
            path = cache / f"index_{z}_{tx}_{ty}.pbf"
            if not path.exists():
                if offline:
                    raise RuntimeError(f"--skip-download but {path} is missing")
                download(INDEX_URL.format(z=z, x=tx, y=ty), path)
            # Layer names vary by release, so inspect every URL-bearing layer.
            for layer in mapbox_vector_tile.decode(path.read_bytes()).values():
                extent = layer.get("extent", 4096)
                for feature in layer.get("features", []):
                    properties = feature.get("properties", {})
                    url = prop(properties, "URL")
                    mesh = prop(properties, "MESH_NO") or prop(properties, "MESH")
                    geometry = feature.get("geometry", {})
                    if not url or not mesh or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                        continue
                    coords = geometry.get("coordinates", [])
                    rings = coords if geometry["type"] == "Polygon" else [r for p in coords for r in p]
                    points = [tile_to_lnglat(tx + px / extent, ty + (extent - py) / extent, z)
                              for ring in rings for px, py in ring]
                    lngs = [p[0] for p in points]
                    lats = [p[1] for p in points]
                    if (lngs and min(lngs) <= bbox["maxLng"] and max(lngs) >= bbox["minLng"]
                            and min(lats) <= bbox["maxLat"] and max(lats) >= bbox["minLat"]):
                        found[mesh] = url
    result = sorted(found.items())
    print(f"intersecting orthophoto meshes: {len(result)}")
    return result


def fetch_and_extract(meshes: list[tuple[str, str]], cache: Path, offline: bool) -> list[Path]:
    rasters: list[Path] = []
    image_exts = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
    for mesh, url in meshes:
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".zip"
        resource = cache / "downloads" / f"{mesh}{suffix}"
        if not resource.exists():
            if offline:
                raise RuntimeError(f"--skip-download but {resource} is missing")
            download(url, resource)
        if resource.suffix.lower() != ".zip":
            rasters.append(resource)
            continue
        target = cache / "extracted" / mesh
        marker = target / ".complete"
        if not marker.exists():
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            with zipfile.ZipFile(resource) as archive:
                archive.extractall(target)
            marker.write_text("ok\n", encoding="utf-8")
        rasters.extend(p for p in target.rglob("*") if p.suffix.lower() in image_exts)
    if not rasters:
        raise RuntimeError("downloaded resources contain no supported rasters")
    return sorted(rasters)


def warp(bbox: dict, paths: list[Path], resolution: float, max_edge: int) -> tuple[Image.Image, float, float]:
    west, south, east, north = transform_bounds(
        "EPSG:4326", DST_CRS, bbox["minLng"], bbox["minLat"], bbox["maxLng"], bbox["maxLat"]
    )
    mid_lat = (bbox["minLat"] + bbox["maxLat"]) / 2
    projected_pixel = resolution / math.cos(math.radians(mid_lat))
    width = max(2, round((east - west) / projected_pixel))
    height = max(2, round((north - south) / projected_pixel))
    if max(width, height) > max_edge:
        scale = max_edge / max(width, height)
        width, height = round(width * scale), round(height * scale)
    dst_transform = from_bounds(west, south, east, north, width, height)
    rgb = np.zeros((3, height, width), np.uint8)
    coverage = np.zeros((height, width), np.uint8)
    used = 0
    for path in paths:
        with rasterio.open(path) as src:
            src_crs = src.crs or SRC_CRS
            b = transform_bounds(src_crs, DST_CRS, *src.bounds, densify_pts=21)
            if b[2] <= west or b[0] >= east or b[3] <= south or b[1] >= north:
                continue
            temp = np.zeros_like(rgb)
            temp_mask = np.zeros_like(coverage)
            bands = min(src.count, 3)
            for band in range(bands):
                reproject(src.read(band + 1), temp[band],
                          src_transform=src.transform, src_crs=src_crs,
                          dst_transform=dst_transform, dst_crs=DST_CRS,
                          resampling=Resampling.lanczos)
            if bands == 1:
                temp[1:] = temp[0]
            reproject(src.dataset_mask(), temp_mask,
                      src_transform=src.transform, src_crs=src_crs,
                      dst_transform=dst_transform, dst_crs=DST_CRS,
                      resampling=Resampling.nearest)
            valid = temp_mask > 0
            rgb[:, valid] = temp[:, valid]
            coverage[valid] = 255
            used += 1
    ratio = np.count_nonzero(coverage) / coverage.size
    if ratio < 0.995:
        raise RuntimeError(f"orthophoto coverage is only {ratio:.2%}; refusing partial output")
    effective = (east - west) * math.cos(math.radians(mid_lat)) / width
    print(f"warped {used} rasters: {width}x{height}, coverage {ratio:.3%}, {effective:.3f} m/px")
    return Image.fromarray(np.moveaxis(rgb, 0, 2)), ratio, effective


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--satellite-meta", type=Path, default=FUJI_DIR / "satellite_meta.json")
    parser.add_argument("--out-image", type=Path, default=FUJI_DIR / "satellite_shizuoka.jpg")
    parser.add_argument("--out-meta", type=Path, default=FUJI_DIR / "satellite_shizuoka_meta.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "pipeline/cache/shizuoka_ortho")
    parser.add_argument("--target-res", type=float, default=0.20)
    parser.add_argument("--max-long-edge", type=int, default=8192)
    parser.add_argument("--jpeg-quality", type=int, default=94)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    original_meta = json.loads(args.satellite_meta.read_text(encoding="utf-8"))
    bbox = original_meta["bbox"]
    meshes = find_meshes(bbox, args.cache_dir, args.skip_download)
    if not meshes:
        raise RuntimeError("LPORTHO index has no coverage for the Replay bbox")
    image, coverage, effective = warp(
        bbox, fetch_and_extract(meshes, args.cache_dir, args.skip_download),
        args.target_res, args.max_long_edge,
    )
    args.out_image.parent.mkdir(parents=True, exist_ok=True)
    if args.out_image.suffix.lower() == ".png":
        # Lossless intermediate for downstream processing (e.g. the SR corridor
        # pipeline): no JPEG artifacts, and with --max-long-edge above the
        # native extent no downscale either.
        image.save(args.out_image, "PNG", compress_level=3)
    else:
        image.save(args.out_image, "JPEG", quality=args.jpeg_quality, optimize=True, progressive=True)
    metadata = {
        "imageFile": args.out_image.name,
        "imageWidth": image.width,
        "imageHeight": image.height,
        "bbox": bbox,
        "mercator": True,
        "source": SOURCE,
        "sourceUrl": SOURCE_URL,
        "license": "CC BY 4.0 / ODbL",
        "nominalSourceResolutionMetersPerPixel": args.target_res,
        "effectiveResolutionMetersPerPixel": round(effective, 4),
        "coverage": round(coverage, 6),
    }
    args.out_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {args.out_image} and {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
