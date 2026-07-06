#!/usr/bin/env python3
"""Refine the Barber track.json elevation profile from real terrain data.

By default, samples public/data/tracks/barber/terrain.png (AWS Terrarium
tiles, see fetch_terrain_tiles.py) by bilinear interpolation at every
centerline point, using the same lat/lng -> UV mapping the viewer uses
(src/engine/track/groundMath.ts). With --offline, or when the terrain assets
are missing, the existing track.json alt values are used as the input
instead. Either way, the resulting profile is then smoothed and gradient-
clamped so it can't reintroduce the physically-impossible spikes raw SRTM/
Open-Elevation data produced.

Usage:
    python3 pipeline/refine_elevation.py
    python3 pipeline/refine_elevation.py --offline
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # only required for the online (terrain.png) path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BARBER_DIR = PROJECT_ROOT / "public" / "data" / "tracks" / "barber"
TRACK_PATH = BARBER_DIR / "track.json"
TERRAIN_IMAGE_PATH = BARBER_DIR / "terrain.png"
TERRAIN_META_PATH = BARBER_DIR / "terrain_meta.json"

MAX_GRADE = 0.12
MAX_GRADE_EXIT_TOLERANCE = 0.125
ELEVATION_SPAN_RANGE = (10.0, 45.0)
MOVING_AVERAGE_WINDOW = 5
MAX_CLAMP_ITERATIONS = 200
REPORT_ZONE = (3140.0, 3280.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refine the Barber centerline elevation profile.")
    parser.add_argument("--track", default=str(TRACK_PATH), help="Track JSON file to update.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip terrain.png sampling; smooth/clamp the existing alt values instead.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def is_closing_duplicate(points: list[dict]) -> bool:
    if len(points) < 2:
        return False
    first, last = points[0], points[-1]
    return all(abs(float(first[key]) - float(last[key])) < 1e-9 for key in ("lat", "lng", "x", "z"))


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


# ---------- Circular smoothing / gradient clamp ----------

def segment_length(dists: list[float], total_length: float, index: int) -> float:
    n = len(dists)
    nxt = (index + 1) % n
    if nxt != 0:
        return dists[nxt] - dists[index]
    return (dists[0] + total_length) - dists[index]


def max_abs_grade(alts: list[float], dists: list[float], total_length: float) -> float:
    n = len(alts)
    maximum = 0.0
    for i in range(n):
        length = segment_length(dists, total_length, i)
        if length <= 0:
            continue
        nxt = (i + 1) % n
        maximum = max(maximum, abs(alts[nxt] - alts[i]) / length)
    return maximum


def circular_moving_average(values: list[float], window: int) -> list[float]:
    n = len(values)
    half = window // 2
    return [sum(values[(i + j) % n] for j in range(-half, half + 1)) / window for i in range(n)]


def clamp_gradients_circular(
    values: list[float],
    dists: list[float],
    total_length: float,
    max_grade: float = MAX_GRADE,
    max_iterations: int = MAX_CLAMP_ITERATIONS,
) -> tuple[list[float], int]:
    """Move both endpoints of every over-steep segment toward each other
    (50/50 split) until every segment satisfies max_grade, or the iteration
    cap is hit."""
    values = values[:]
    n = len(values)
    lengths = [segment_length(dists, total_length, i) for i in range(n)]

    for iteration in range(1, max_iterations + 1):
        max_violation = 0.0
        for i in range(n):
            length = lengths[i]
            if length <= 0:
                continue
            nxt = (i + 1) % n
            diff = values[nxt] - values[i]
            grade = diff / length
            if abs(grade) > max_grade:
                max_violation = max(max_violation, abs(grade) - max_grade)
                target_diff = math.copysign(max_grade * length, diff)
                correction = diff - target_diff
                values[i] += correction / 2
                values[nxt] -= correction / 2
        if max_violation <= 1e-9:
            return values, iteration

    print(f"Warning: gradient clamp did not fully converge after {max_iterations} iterations.", file=sys.stderr)
    return values, max_iterations


# ---------- Reporting ----------

def stats(alts: list[float], dists: list[float], total_length: float) -> tuple[float, float, float]:
    return min(alts), max(alts), max_abs_grade(alts, dists, total_length)


def print_zone(label: str, dists: list[float], alts: list[float], zone: tuple[float, float]) -> None:
    print(f"  {label}:")
    for dist, alt in zip(dists, alts):
        if zone[0] <= dist <= zone[1]:
            print(f"    dist={dist:8.2f}  alt={alt:7.2f}")


def main() -> int:
    args = parse_args()
    track_path = Path(args.track)
    if not track_path.exists():
        print(f"ERROR: track file not found: {track_path}", file=sys.stderr)
        return 1

    backup_path = track_path.with_name(track_path.name + ".bak")
    if backup_path.exists():
        print(f"Backup already exists: {backup_path}")
    else:
        shutil.copy2(track_path, backup_path)
        print(f"Created backup: {backup_path}")

    data = read_json(track_path)
    centerline = data.get("centerline")
    if not isinstance(centerline, list) or len(centerline) < 3:
        print("Track JSON must contain a centerline with at least 3 points.", file=sys.stderr)
        return 2
    if not is_closing_duplicate(centerline):
        print("Centerline does not appear to end with a duplicate closing point.", file=sys.stderr)
        return 2

    open_points = centerline[:-1]
    total_length = float(data.get("totalLength", centerline[-1]["dist"]))
    dists = [float(p["dist"]) for p in open_points]
    old_alts = [float(p["alt"]) for p in open_points]
    old_origin_alt = float(data["origin"]["alt"])

    use_terrain = not args.offline and TERRAIN_IMAGE_PATH.exists() and TERRAIN_META_PATH.exists()
    if not args.offline and not use_terrain:
        print("Terrain assets not found; falling back to --offline behaviour (existing alt values).")

    if use_terrain:
        if Image is None:
            print("ERROR: Pillow is required to sample terrain.png (pip3 install --user pillow).", file=sys.stderr)
            return 1
        terrain_meta = read_json(TERRAIN_META_PATH)
        grid = TerrainGrid(TERRAIN_IMAGE_PATH, terrain_meta)
        raw_alts = [grid.sample(float(p["lat"]), float(p["lng"])) for p in open_points]
        origin_alt = round(grid.sample(float(data["origin"]["lat"]), float(data["origin"]["lng"])), 2)
        mode = f"online: bilinear-sampled from {TERRAIN_IMAGE_PATH.name} ({terrain_meta.get('zoom')})"
    else:
        raw_alts = old_alts[:]
        origin_alt = old_origin_alt
        mode = "offline: existing track.json alt values"

    smoothed = circular_moving_average(raw_alts, MOVING_AVERAGE_WINDOW)
    clamped, clamp_iterations = clamp_gradients_circular(smoothed, dists, total_length)
    rounded_alts = [round(alt, 2) for alt in clamped]

    before = stats(old_alts, dists, total_length)
    after = stats(rounded_alts, dists, total_length)
    deltas = [abs(new - old) for new, old in zip(rounded_alts, old_alts)]
    mean_delta = sum(deltas) / len(deltas)
    max_delta = max(deltas)

    print(f"Mode: {mode}")
    print(f"Gradient clamp: converged after {clamp_iterations} iteration(s) (cap {MAX_CLAMP_ITERATIONS})")
    print(f"Before: alt min={before[0]:.2f} m, max={before[1]:.2f} m, max |grade|={before[2]:.4f} ({before[2] * 100:.1f}%)")
    print(f"After:  alt min={after[0]:.2f} m, max={after[1]:.2f} m, max |grade|={after[2]:.4f} ({after[2] * 100:.1f}%)")
    print(f"Delta vs old profile: mean |Δalt|={mean_delta:.3f} m, max |Δalt|={max_delta:.3f} m")
    print(f"Origin alt: {old_origin_alt:.2f} m -> {origin_alt:.2f} m")
    print(f"Zone dist {REPORT_ZONE[0]:.0f}-{REPORT_ZONE[1]:.0f} m:")
    print_zone("before", dists, old_alts, REPORT_ZONE)
    print_zone("after ", dists, rounded_alts, REPORT_ZONE)

    for point, alt in zip(open_points, rounded_alts):
        point["alt"] = alt
        point["y"] = round(alt - origin_alt, 2)
    centerline[-1]["alt"] = centerline[0]["alt"]
    centerline[-1]["y"] = centerline[0]["y"]
    data["origin"]["alt"] = origin_alt
    data["elevationRange"] = [round(min(rounded_alts), 2), round(max(rounded_alts), 2)]

    write_json(track_path, data)
    print(f"Wrote: {track_path}")

    elevation_span = after[1] - after[0]
    ok = True
    if after[2] > MAX_GRADE_EXIT_TOLERANCE:
        print(f"ERROR: max |grade| after processing {after[2]:.4f} exceeds {MAX_GRADE_EXIT_TOLERANCE}", file=sys.stderr)
        ok = False
    if not (ELEVATION_SPAN_RANGE[0] <= elevation_span <= ELEVATION_SPAN_RANGE[1]):
        print(
            f"ERROR: elevation span {elevation_span:.2f} m outside "
            f"[{ELEVATION_SPAN_RANGE[0]}, {ELEVATION_SPAN_RANGE[1]}] m",
            file=sys.stderr,
        )
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
