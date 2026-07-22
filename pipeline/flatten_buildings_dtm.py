"""Flatten building bumps out of the fuji DTM (terrain.png).

Root cause (docs/proposal-building-terrain-2026-07-22.md): the VS2019 LP
point cloud only has classes 1/2, and large flat roofs leak into class 2
(ground), so the "DTM" contains grandstand-shaped bumps. The photo draped
over them reads as photo-textured hills.

This is a deterministic post-process: for every building footprint in
features3d.json, replace the DTM cells inside the (buffered) footprint with
the median ground level of a surrounding ring, then feather the edge. The
output is the same Terrarium PNG, so the viewer needs no changes.

Usage:
  python pipeline/flatten_buildings_dtm.py            # writes terrain.png (backup kept)
  python pipeline/flatten_buildings_dtm.py --dry-run  # stats only
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np

TRACK_DIR = Path(__file__).resolve().parent.parent / "public" / "data" / "tracks" / "fuji"
BUFFER_PX = 2      # grow footprint ~2 m to swallow misregistration
RING_PX = 5        # ring width outside the buffered footprint used as ground level
FEATHER_PX = 3     # smooth transition band at the footprint edge


def decode_terrarium(img: np.ndarray) -> np.ndarray:
    r, g, b = img[:, :, 0].astype(np.float64), img[:, :, 1].astype(np.float64), img[:, :, 2].astype(np.float64)
    return r * 256.0 + g + b / 256.0 - 32768.0


def encode_terrarium(elev: np.ndarray) -> np.ndarray:
    v = elev + 32768.0
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.round((v - r * 256.0 - g) * 256.0)
    # carry: b can round to 256
    carry = b >= 256
    b[carry] = 0
    g[carry] += 1
    carry = g >= 256
    g[carry] = 0
    r[carry] += 1
    return np.stack([r, g, b], axis=-1).clip(0, 255).astype(np.uint8)


def local_xz_to_px(x: float, z: float, origin: dict, meta: dict) -> tuple[float, float]:
    metres_per_degree = 111_320.0
    lng_scale = metres_per_degree * math.cos(math.radians(origin["lat"]))
    lat = origin["lat"] - z / metres_per_degree
    lng = origin["lng"] + x / lng_scale
    bbox = meta["bbox"]
    u = (lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"])
    y_north = math.asinh(math.tan(math.radians(bbox["maxLat"])))
    y_south = math.asinh(math.tan(math.radians(bbox["minLat"])))
    v = (y_north - math.asinh(math.tan(math.radians(lat)))) / (y_north - y_south)
    return u * (meta["imageWidth"] - 1), v * (meta["imageHeight"] - 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = json.loads((TRACK_DIR / "terrain_meta.json").read_text(encoding="utf-8"))
    feats = json.loads((TRACK_DIR / "features3d.json").read_text(encoding="utf-8"))
    origin = feats["origin"]
    img = cv2.cvtColor(cv2.imread(str(TRACK_DIR / "terrain.png"), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    elev = decode_terrarium(img)
    h, w = elev.shape

    flattened = 0
    total_drop = 0.0
    for i, b in enumerate(feats["buildings"]):
        pts = np.array(
            [local_xz_to_px(x, z, origin, meta) for x, z in b["footprint"]], dtype=np.float64
        )
        if not ((0 <= pts[:, 0]).all() and (pts[:, 0] < w).all() and (0 <= pts[:, 1]).all() and (pts[:, 1] < h).all()):
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
        mask = cv2.dilate(mask, np.ones((2 * BUFFER_PX + 1,) * 2, np.uint8))
        ring = cv2.dilate(mask, np.ones((2 * RING_PX + 1,) * 2, np.uint8)) & ~mask
        if mask.sum() == 0 or ring.sum() == 0:
            continue
        ground = float(np.median(elev[ring > 0]))
        inside = mask > 0
        before = elev[inside]
        drop = float((before - ground).clip(min=0).mean())
        if drop < 0.3:
            continue  # no bump baked in here; leave untouched
        # Replace, then feather the edge band toward the surroundings.
        elev[inside] = ground
        soft = cv2.GaussianBlur(elev, (2 * FEATHER_PX + 1,) * 2, 0)
        band = (cv2.dilate(mask, np.ones((2 * FEATHER_PX + 1,) * 2, np.uint8)) > 0) & ~cv2.erode(
            mask, np.ones((2 * FEATHER_PX + 1,) * 2, np.uint8)
        ).astype(bool)
        elev[band] = soft[band]
        flattened += 1
        total_drop += drop
        tag = b.get("tags", {}).get("building", "?")
        print(f"[{i:02d}] {tag:12s} cells={int(inside.sum()):5d} ground={ground:7.2f}m mean_bump={drop:5.2f}m")

    print(f"\nflattened {flattened}/{len(feats['buildings'])} buildings, mean bump {total_drop / max(flattened, 1):.2f} m")
    if args.dry_run:
        return

    backup = TRACK_DIR / "terrain.pre-flatten.png"
    if not backup.exists():
        shutil.copy2(TRACK_DIR / "terrain.png", backup)
    out = cv2.cvtColor(encode_terrarium(elev), cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(TRACK_DIR / "terrain.png"), out)
    print(f"wrote {TRACK_DIR / 'terrain.png'} (backup: {backup.name})")


if __name__ == "__main__":
    main()
