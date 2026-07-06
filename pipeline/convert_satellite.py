#!/usr/bin/env python3
"""Convert the raw 99 MB satellite PNG to a 4096-px JPEG for use as WebGL ground texture.

Usage:
    python3 pipeline/convert_satellite.py

Output: public/data/tracks/barber/satellite.jpg
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = Path(__file__).resolve().parents[1]

SRC_IMAGE = PROJECT_ROOT / "replay-viewer" / "data" / "tracks" / "barber_motorsports_park_satellite.png"
SRC_TRACK_JSON = PROJECT_ROOT / "replay-viewer" / "data" / "tracks" / "barber_motorsports_park.json"
OUT_IMAGE = V2_ROOT / "public" / "data" / "tracks" / "barber" / "satellite.jpg"
OUT_META = V2_ROOT / "public" / "data" / "tracks" / "barber" / "satellite_meta.json"

TARGET_MAX_PX = 4096
JPEG_QUALITY = 88


def main() -> None:
    if not SRC_IMAGE.exists():
        raise SystemExit(f"Source satellite image not found: {SRC_IMAGE}")

    src_size_mb = SRC_IMAGE.stat().st_size / (1024 * 1024)
    print(f"Source: {SRC_IMAGE.name}  ({src_size_mb:.1f} MB)")

    with Image.open(SRC_IMAGE) as img:
        orig_w, orig_h = img.size
        print(f"Original size: {orig_w} x {orig_h} px")

        # Downsample to fit within TARGET_MAX_PX on the longest side
        scale = TARGET_MAX_PX / max(orig_w, orig_h)
        new_w = round(orig_w * scale)
        new_h = round(orig_h * scale)
        print(f"Resampling to: {new_w} x {new_h} px ...")

        img_resized = img.resize((new_w, new_h), Image.LANCZOS)
        # Convert to RGB for JPEG
        if img_resized.mode != "RGB":
            img_resized = img_resized.convert("RGB")

        OUT_IMAGE.parent.mkdir(parents=True, exist_ok=True)
        img_resized.save(OUT_IMAGE, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)

    out_size_mb = OUT_IMAGE.stat().st_size / (1024 * 1024)
    print(f"Output:  {OUT_IMAGE.name}  ({out_size_mb:.1f} MB)")
    print(f"Reduction: {src_size_mb / out_size_mb:.1f}x smaller")

    # Write satellite metadata so the 3-D viewer can place the texture
    with SRC_TRACK_JSON.open() as f:
        track_def = json.load(f)
    sat = track_def.get("satellite", {})
    bbox = sat.get("bbox", {})

    meta = {
        "imageFile": "satellite.jpg",
        "imageWidth": new_w,
        "imageHeight": new_h,
        "bbox": {
            "minLng": bbox.get("minLng"),
            "maxLng": bbox.get("maxLng"),
            "minLat": bbox.get("minLat"),
            "maxLat": bbox.get("maxLat"),
        },
        "mercator": sat.get("mercator", True),
    }
    with OUT_META.open("w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"Metadata: {OUT_META.name}")


if __name__ == "__main__":
    main()
