#!/usr/bin/env python3
"""Build a track ground texture from GSI 全国最新写真（シームレス）tiles.

The GSI photo tiles are published under the 国土地理院コンテンツ利用規約
(attribution required). This script records source/attribution in the output
meta; the fixed-texture usage classification is tracked separately in docs
(承認要否の確認状況). Do NOT point this at Google/Bing tile servers.

Example:
    python pipeline/fetch_gsi_ortho.py --track suzuka \
        --min-lat 34.8355 --max-lat 34.8580 --min-lng 136.5240 --max-lng 136.5500
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
USER_AGENT = "replay-viewer gsi-ortho/1.0"
SOURCE = "国土地理院 全国最新写真（シームレス）"
SOURCE_URL = "https://maps.gsi.go.jp/development/ichiran.html#seamlessphoto"
LICENSE = ("国土地理院コンテンツ利用規約（出典明示）。固定画像化の測量法上の"
           "扱いは docs の確認状況を参照")


def lnglat_to_tile(lng: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return ((lng + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def fetch_tile(z: int, x: int, y: int, cache: Path) -> Image.Image | None:
    path = cache / f"{z}_{x}_{y}.jpg"
    if not path.exists():
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                path.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        time.sleep(0.05)  # be polite to the tile server
    return Image.open(path).convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True, help="track id (output dir name)")
    parser.add_argument("--min-lat", type=float, required=True)
    parser.add_argument("--max-lat", type=float, required=True)
    parser.add_argument("--min-lng", type=float, required=True)
    parser.add_argument("--max-lng", type=float, required=True)
    parser.add_argument("--zoom", type=int, default=18)
    parser.add_argument("--out-image", type=Path, default=None)
    parser.add_argument("--out-meta", type=Path, default=None)
    args = parser.parse_args()

    cache = REPO_ROOT / "pipeline" / "cache" / f"gsi_{args.track}" / f"z{args.zoom}"
    cache.mkdir(parents=True, exist_ok=True)
    out_image = args.out_image or REPO_ROOT / "pipeline" / "cache" / f"gsi_{args.track}" / "native_mosaic.png"
    out_meta = args.out_meta or out_image.with_name(out_image.stem + "_meta.json")

    z = args.zoom
    x0f, y0f = lnglat_to_tile(args.min_lng, args.max_lat, z)  # top-left
    x1f, y1f = lnglat_to_tile(args.max_lng, args.min_lat, z)  # bottom-right
    x0, y0 = math.floor(x0f), math.floor(y0f)
    x1, y1 = math.ceil(x1f), math.ceil(y1f)
    cols, rows = x1 - x0, y1 - y0
    print(f"tiles: {cols} x {rows} = {cols * rows} at z{z}")

    mosaic = np.zeros((rows * 256, cols * 256, 3), np.uint8)
    missing = 0
    for ty in range(y0, y1):
        for tx in range(x0, x1):
            tile = fetch_tile(z, tx, ty, cache)
            if tile is None:
                missing += 1
                continue
            mosaic[(ty - y0) * 256:(ty - y0 + 1) * 256,
                   (tx - x0) * 256:(tx - x0 + 1) * 256] = np.asarray(tile)
    if missing:
        print(f"WARNING: {missing} tiles missing (left black)")

    # The mosaic covers exact tile boundaries; recompute its true bbox.
    n = 2 ** z
    def tile_to_lnglat(x: float, y: float) -> tuple[float, float]:
        return (x / n * 360 - 180,
                math.degrees(math.atan(math.sinh(math.pi - 2 * math.pi * y / n))))
    west, north = tile_to_lnglat(x0, y0)
    east, south = tile_to_lnglat(x1, y1)

    Image.fromarray(mosaic).save(out_image, "PNG", compress_level=3)
    mid_lat = (north + south) / 2
    metres_per_px = (east - west) * 111_320 * math.cos(math.radians(mid_lat)) / mosaic.shape[1]
    meta = {
        "imageFile": out_image.name,
        "imageWidth": int(mosaic.shape[1]),
        "imageHeight": int(mosaic.shape[0]),
        "bbox": {"minLng": west, "maxLng": east, "minLat": south, "maxLat": north},
        "mercator": True,
        "source": SOURCE,
        "sourceUrl": SOURCE_URL,
        "license": LICENSE,
        "gsiTileZoom": z,
        "nominalSourceResolutionMetersPerPixel": round(metres_per_px, 4),
        "effectiveResolutionMetersPerPixel": round(metres_per_px, 4),
        "coverage": round(1 - missing / (cols * rows), 6),
    }
    out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {out_image} ({mosaic.shape[1]}x{mosaic.shape[0]}, {metres_per_px:.3f} m/px)")
    print(f"saved {out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
