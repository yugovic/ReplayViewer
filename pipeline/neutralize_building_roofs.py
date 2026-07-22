"""Erase building roofs from the fuji ground imagery (step 2 of the
building/terrain fix, docs/proposal-building-terrain-2026-07-22.md).

After step 1 flattened the DTM, the roofs are still painted on the draped
photo, which reads as "building drawn on the floor". This inpaints every
features3d building footprint (small buffer, includes an offset strip for
the baked shadow) from its surroundings, so the ground shows plausible
pavement/grass and the 3D extruded boxes (step 3) become the only building
representation. Deterministic, no API.

Usage:
  python pipeline/neutralize_building_roofs.py            # writes images (backups kept)
  python pipeline/neutralize_building_roofs.py --dry-run  # writes preview only
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
TARGETS = [
    ("satellite_shizuoka.jpg", "satellite_shizuoka_meta.json"),
    ("satellite_shizuoka_4k.webp", "satellite_shizuoka_4k_meta.json"),
]
BUFFER_M = 2.0        # grow footprints to cover misregistration
SHADOW_OFFSET_M = 6.0 # extend mask toward NW to swallow the baked shadow


def to_px(lat: float, lng: float, meta: dict) -> tuple[float, float]:
    bbox = meta["bbox"]
    u = (lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"])
    y_north = math.asinh(math.tan(math.radians(bbox["maxLat"])))
    y_south = math.asinh(math.tan(math.radians(bbox["minLat"])))
    v = (y_north - math.asinh(math.tan(math.radians(lat)))) / (y_north - y_south)
    return u * (meta["imageWidth"] - 1), v * (meta["imageHeight"] - 1)


def footprint_px(footprint: list[list[float]], origin: dict, meta: dict) -> np.ndarray:
    metres_per_degree = 111_320.0
    lng_scale = metres_per_degree * math.cos(math.radians(origin["lat"]))
    pts = []
    for x, z in footprint:
        lat = origin["lat"] - z / metres_per_degree
        lng = origin["lng"] + x / lng_scale
        pts.append(to_px(lat, lng, meta))
    return np.array(pts, dtype=np.float64)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    feats = json.loads((TRACK_DIR / "features3d.json").read_text(encoding="utf-8"))
    origin = feats["origin"]

    for image_name, meta_name in TARGETS:
        path = TRACK_DIR / image_name
        if not path.exists():
            print(f"skip {image_name} (missing)")
            continue
        meta = json.loads((TRACK_DIR / meta_name).read_text(encoding="utf-8"))
        im = cv2.imread(str(path), cv2.IMREAD_COLOR)
        h, w = im.shape[:2]
        assert (w, h) == (meta["imageWidth"], meta["imageHeight"]), image_name

        # metres per pixel varies slightly with latitude; a middle value is fine
        mpp = (meta["bbox"]["maxLng"] - meta["bbox"]["minLng"]) * 111_320.0 * math.cos(
            math.radians(origin["lat"])
        ) / w
        buf = max(1, int(round(BUFFER_M / mpp)))
        shadow = int(round(SHADOW_OFFSET_M / mpp))

        mask = np.zeros((h, w), np.uint8)
        n = 0
        for b in feats["buildings"]:
            pts = footprint_px(b["footprint"], origin, meta)
            if pts[:, 0].min() < -50 or pts[:, 0].max() > w + 50 or pts[:, 1].min() < -50 or pts[:, 1].max() > h + 50:
                continue
            poly = np.round(pts).astype(np.int32)
            cv2.fillPoly(mask, [poly], 255)
            # baked shadows fall away from the sun; cover a NW-shifted copy
            cv2.fillPoly(mask, [poly + np.array([-shadow, -shadow // 2])], 255)
            n += 1
        mask = cv2.dilate(mask, np.ones((2 * buf + 1,) * 2, np.uint8))
        print(f"{image_name}: {n} buildings, mask {mask.mean() / 2.55:.1f}% of image, inpainting...")

        out = cv2.inpaint(im, mask, inpaintRadius=6, flags=cv2.INPAINT_TELEA)
        # Telea can leave streaks on big roofs; soften the filled area
        blur = cv2.GaussianBlur(out, (9, 9), 0)
        m3 = (mask > 0)
        out[m3] = blur[m3]

        if args.dry_run:
            prev = cv2.resize(out, (min(w, 2048), min(h, 2048)))
            cv2.imwrite(str(TRACK_DIR / f"preview_neutralized_{path.stem}.jpg"), prev)
            continue
        backup = TRACK_DIR / f"{path.stem}.pre-neutralize{path.suffix}"
        if not backup.exists():
            shutil.copy2(path, backup)
        if path.suffix == ".webp":
            cv2.imwrite(str(path), out, [cv2.IMWRITE_WEBP_QUALITY, 80])
        else:
            cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"wrote {path.name} (backup: {backup.name})")


if __name__ == "__main__":
    main()
