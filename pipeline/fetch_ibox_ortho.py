#!/usr/bin/env python3
"""Build an Okayama circuit texture from the OKAYAMA INFRA BOX 2024 orthophotos.

Dataset 250 "岡山県_3次元点群データ_オルソ画像_2024" on
https://i-box.pref.okayama.jp/ ships ~9,993 ZIP orthophoto resources, one per
1/2500-derived 国土基本図 map frame (code2500), each a 25 cm/px GeoTIFF in
JGD2000 / Japan Plane Rectangular CS V (EPSG:2447). Only the frames that
intersect satellite_meta.json are downloaded, warped to EPSG:3857 and the exact
existing bbox, so the output drops into Replay without moving its ground mesh.

Frame codes are decoded analytically (国土基本図図郭), verified against the
GeoTIFF georeference, so no per-resource page scraping is needed. The mesh ->
S3 URL map comes from the site's public /api/mesh_resources endpoint.

License: CC BY 4.0 (i-box terms of use, art. 9). Attribution recorded in meta.
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
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject, transform, transform_bounds
except ImportError:
    _install("rasterio")
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject, transform, transform_bounds
try:
    from PIL import Image
except ImportError:
    _install("pillow")
    from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OKAYAMA_DIR = ROOT / "public" / "data" / "tracks" / "okayama"
MESH_API = "https://i-box.pref.okayama.jp/api/mesh_resources"
# Ortho GeoTIFFs are published in Japan Plane Rectangular CS V (JGD2000).
FRAME_CRS = CRS.from_epsg(2447)
DST_CRS = CRS.from_epsg(3857)
USER_AGENT = "replay-viewer-v2 ibox-okayama/1.0"
SOURCE = (
    "おかやまインフラボックス（岡山県_3次元点群_オルソ画像_2024）を加工して作成"
)
SOURCE_URL = "https://i-box.pref.okayama.jp/datasets/250"
LICENSE = "CC BY 4.0"
ATTRIBUTION = (
    "「おかやまインフラボックス（岡山県_3次元点群_オルソ画像_2024）」（岡山県）"
    "を加工して作成（CC BY 4.0）"
)


def decode_frame(code: str) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) in EPSG:2447 for a code2500 frame.

    Format: <系2><L1><L2><y><x>[<sub>...]. Block = 40 km (E-W) x 30 km (N-S);
    the 10x10 level-5000 grid gives 4000x3000 m; each trailing 1-4 quadrant
    (1=NW 2=NE 3=SW 4=SE) halves both extents.
    """
    l1, l2, digits = code[2], code[3], code[4:]
    east = (-160 + (ord(l2) - 65) * 40) * 1000.0
    north = (300 - (ord(l1) - 65) * 30) * 1000.0
    west = east + int(digits[1]) * 4000.0
    ntop = north - int(digits[0]) * 3000.0
    ww, hh = 4000.0, 3000.0
    for ch in digits[2:]:
        sub = int(ch)
        ww /= 2
        hh /= 2
        if sub in (2, 4):
            west += ww
        if sub in (3, 4):
            ntop -= hh
    return west, ntop - hh, west + ww, ntop


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


def find_meshes(bbox: dict, cache: Path, offline: bool) -> list[tuple[str, str]]:
    """Return sorted (mesh_code, zip_url) for ortho frames intersecting bbox."""
    index = cache / "mesh_resources.json"
    if not index.exists():
        if offline:
            raise RuntimeError(f"--skip-download but {index} is missing")
        download(MESH_API, index)
    mesh_map = json.loads(index.read_text(encoding="utf-8"))
    # Circuit bbox corners projected into the frame CRS (EPSG:2447).
    lngs = [bbox["minLng"], bbox["maxLng"], bbox["minLng"], bbox["maxLng"]]
    lats = [bbox["minLat"], bbox["minLat"], bbox["maxLat"], bbox["maxLat"]]
    xs, ys = transform("EPSG:4326", FRAME_CRS, lngs, lats)
    min_e, max_e, min_n, max_n = min(xs), max(xs), min(ys), max(ys)
    found: dict[str, str] = {}
    for code, resources in mesh_map.items():
        if not code.startswith("05"):
            continue
        try:
            west, south, east, north = decode_frame(code)
        except (ValueError, IndexError):
            continue
        if west > max_e or east < min_e or south > max_n or north < min_n:
            continue
        for res in resources:
            name = res.get("name", "")
            if not name.startswith("オルソ"):
                continue
            parts = name.split("_")
            seq = parts[2] if len(parts) > 2 else "01"
            found[code] = (
                f"https://gic-okayama.s3.ap-northeast-1.amazonaws.com/"
                f"2024/photo/{seq}/{code}.zip"
            )
            break
    result = sorted(found.items())
    print(f"intersecting orthophoto frames: {len(result)} ({[c for c, _ in result]})")
    return result


def fetch_and_extract(meshes: list[tuple[str, str]], cache: Path, offline: bool) -> list[Path]:
    rasters: list[Path] = []
    image_exts = {".tif", ".tiff"}
    for mesh, url in meshes:
        resource = cache / "downloads" / f"{mesh}.zip"
        if not resource.exists():
            if offline:
                raise RuntimeError(f"--skip-download but {resource} is missing")
            download(url, resource)
            time.sleep(0.1)
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
        raise RuntimeError("downloaded resources contain no GeoTIFF rasters")
    return sorted(rasters)


def warp(
    bbox: dict,
    paths: list[Path],
    resolution: float,
    max_edge: int,
    base_mosaic: Path | None,
) -> tuple[Image.Image, float, float]:
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
            src_crs = src.crs or FRAME_CRS
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
    filled = 0.0
    if ratio < 0.999 and base_mosaic is not None:
        # Composite i-box over the existing GSI graded mosaic so holes (areas
        # the erosion-control survey did not cover) are never black.
        base = Image.open(base_mosaic).convert("RGB").resize((width, height), Image.LANCZOS)
        base_arr = np.moveaxis(np.asarray(base), 2, 0)
        holes = coverage == 0
        rgb[:, holes] = base_arr[:, holes]
        filled = np.count_nonzero(holes) / coverage.size
        print(f"filled {filled:.2%} holes from GSI base {base_mosaic.name}")
    elif ratio < 0.999:
        raise RuntimeError(f"coverage only {ratio:.2%} and no --base-mosaic to fill holes")
    effective = (east - west) * math.cos(math.radians(mid_lat)) / width
    print(f"warped {used} rasters: {width}x{height}, i-box coverage {ratio:.3%}, {effective:.3f} m/px")
    return Image.fromarray(np.moveaxis(rgb, 0, 2)), ratio, effective


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--satellite-meta", type=Path, default=OKAYAMA_DIR / "satellite_meta.json")
    parser.add_argument("--out-image", type=Path, default=OKAYAMA_DIR / "satellite_ibox.png")
    parser.add_argument("--out-meta", type=Path, default=OKAYAMA_DIR / "satellite_ibox_meta.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "pipeline/cache/ibox_okayama")
    parser.add_argument("--base-mosaic", type=Path, default=None,
                        help="GSI graded mosaic PNG used to fill any i-box holes")
    parser.add_argument("--target-res", type=float, default=0.25)
    parser.add_argument("--max-long-edge", type=int, default=8192)
    parser.add_argument("--jpeg-quality", type=int, default=94)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    original_meta = json.loads(args.satellite_meta.read_text(encoding="utf-8"))
    bbox = original_meta["bbox"]
    meshes = find_meshes(bbox, args.cache_dir, args.skip_download)
    if not meshes:
        raise RuntimeError("i-box dataset 250 has no ortho coverage for the Replay bbox")
    image, coverage, effective = warp(
        bbox, fetch_and_extract(meshes, args.cache_dir, args.skip_download),
        args.target_res, args.max_long_edge, args.base_mosaic,
    )
    args.out_image.parent.mkdir(parents=True, exist_ok=True)
    if args.out_image.suffix.lower() == ".png":
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
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "sourceMeshFrames": [c for c, _ in meshes],
        "sourceCrs": "EPSG:2447 (JGD2000 / Japan Plane Rectangular CS V)",
        "nominalSourceResolutionMetersPerPixel": args.target_res,
        "effectiveResolutionMetersPerPixel": round(effective, 4),
        "coverage": round(coverage, 6),
    }
    if args.base_mosaic is not None and coverage < 0.999:
        metadata["holeFillSource"] = f"GSI graded mosaic ({args.base_mosaic.name})"
    args.out_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {args.out_image} and {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
