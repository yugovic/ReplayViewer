#!/usr/bin/env python3
"""Populate a freshly-built track.json's flat elevation profile from a terrain raster.

build_track_osm.py's fresh-build mode (no prior track.json to transfer an
elevation profile from) writes every centerline point with alt = origin
altitude and y = 0. This script fills in real elevations by bilinearly
sampling the AWS/Mapzen Terrarium terrain.png raster (see
fetch_terrain_tiles.py) at each centerline point's (lat, lng), using the
exact same lat/lng -> UV convention the viewer's client-side terrain sampler
uses (src/engine/track/groundMath.ts: linear in longitude, Web-Mercator in
latitude). This mirrors the sampling half of refine_elevation.py, minus that
script's US-only EPQS/3DEP fetch path, which does not apply outside the US.

After this script runs, `smooth_elevation.py --track <path>` should be run
to gradient-clamp and smooth the raw sampled profile (Terrarium heights are
noisy at corner-scale spacing).

Usage:
    python3 pipeline/populate_terrain_elevation.py \\
        --track public/data/tracks/fuji/track.json \\
        --terrain-image public/data/tracks/fuji/terrain.png \\
        --terrain-meta public/data/tracks/fuji/terrain_meta.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: python3 -m pip install Pillow", file=sys.stderr)
    sys.exit(1)


# ---------- Terrain sampling (mirrors src/engine/track/groundMath.ts) ----------

def decode_terrarium(r: int, g: int, b: int) -> float:
    return r * 256 + g + b / 256 - 32768


def lat_lng_to_terrain_uv(lat: float, lng: float, bbox: dict) -> tuple[float, float]:
    u = (lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"])

    def mercator_y(lat_deg: float) -> float:
        return math.asinh(math.tan(math.radians(lat_deg)))

    y_north = mercator_y(bbox["maxLat"])
    y_south = mercator_y(bbox["minLat"])
    v = (y_north - mercator_y(lat)) / (y_north - y_south)
    return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))


def bilinear_sample(heights: list[float], width: int, height: int, u: float, v: float) -> float:
    px = u * (width - 1)
    py = v * (height - 1)
    x0 = int(math.floor(px))
    y0 = int(math.floor(py))
    x1 = min(x0 + 1, width - 1)
    y1 = min(y0 + 1, height - 1)
    fx = px - x0
    fy = py - y0

    h00 = heights[y0 * width + x0]
    h10 = heights[y0 * width + x1]
    h01 = heights[y1 * width + x0]
    h11 = heights[y1 * width + x1]
    return h00 * (1 - fx) * (1 - fy) + h10 * fx * (1 - fy) + h01 * (1 - fx) * fy + h11 * fx * fy


class TerrainGrid:
    def __init__(self, image_path: Path, meta: dict) -> None:
        self.bbox = meta["bbox"]
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            self.width, self.height = img.size
            pixels = list(img.getdata())
        self.heights = [decode_terrarium(r, g, b) for (r, g, b) in pixels]

    def sample(self, lat: float, lng: float) -> float:
        u, v = lat_lng_to_terrain_uv(lat, lng, self.bbox)
        return bilinear_sample(self.heights, self.width, self.height, u, v)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Populate track.json elevation from a Terrarium terrain raster")
    p.add_argument("--track", type=Path, required=True, help="track.json to update in place")
    p.add_argument("--terrain-image", type=Path, required=True, help="terrain.png (Terrarium-encoded)")
    p.add_argument("--terrain-meta", type=Path, required=True, help="terrain_meta.json (bbox)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.track.exists():
        print(f"ERROR: {args.track} not found", file=sys.stderr)
        return 1
    if not args.terrain_image.exists() or not args.terrain_meta.exists():
        print("ERROR: terrain image/meta not found", file=sys.stderr)
        return 1

    track = json.loads(args.track.read_text())
    centerline = track["centerline"]
    terrain_meta = json.loads(args.terrain_meta.read_text())
    grid = TerrainGrid(args.terrain_image, terrain_meta)

    is_closed_duplicate = (
        len(centerline) > 1
        and abs(centerline[0]["lat"] - centerline[-1]["lat"]) < 1e-9
        and abs(centerline[0]["lng"] - centerline[-1]["lng"]) < 1e-9
    )
    open_points = centerline[:-1] if is_closed_duplicate else centerline

    sampled = [grid.sample(p["lat"], p["lng"]) for p in open_points]

    origin_alt = sampled[0]
    track["origin"]["alt"] = round(origin_alt, 2)

    for point, alt in zip(open_points, sampled):
        point["alt"] = round(alt, 2)
        point["y"] = round(alt - origin_alt, 2)

    if is_closed_duplicate:
        centerline[-1]["alt"] = centerline[0]["alt"]
        centerline[-1]["y"] = centerline[0]["y"]

    alts = [p["alt"] for p in centerline]
    track["elevationRange"] = [round(min(alts), 2), round(max(alts), 2)]

    backup = args.track.with_name("track.pre-terrain.bak.json")
    if not backup.exists():
        backup.write_text(json.dumps(json.loads(args.track.read_text()), indent=2) + "\n")
        print(f"Backed up pre-terrain track.json to {backup}")

    args.track.write_text(json.dumps(track, indent=2) + "\n")
    print(f"Wrote {args.track}")
    print(f"Sampled elevation range: {min(alts):.2f} .. {max(alts):.2f} m ASL (origin alt {origin_alt:.2f} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
