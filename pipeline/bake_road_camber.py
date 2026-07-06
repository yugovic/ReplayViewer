#!/usr/bin/env python3
"""Bake road left/right edge elevations (camber / cross-slope) into track.json.

P2 (2026-07-05). The track.json centerline carries a single smoothed
elevation profile (`alt`/`y` per point), so the rendered road ribbon and the
replayed car are flat across the road's width — no camber, no banking. Since
P1a/P1b both tracks now have 1 m LiDAR terrain rasters (terrain.png,
Terrarium-encoded), the true cross-slope of the road can be read directly from
the raster instead of estimated from telemetry.

Method (must match the viewer / replay conventions):
  1. For each centerline point, take the local XZ tangent (direction of
     increasing arc length) and its horizontal LEFT/RIGHT normals. LEFT is the
     driver's left = up x forward = (tz, -tx) in (x, z); RIGHT = -LEFT. This is
     the SAME left/right the replay layer uses for signedLateralDistance/roll
     and the viewer uses for the road ribbon — see the chirality tests.
  2. Place the two edge points at center +/- (width/2) along those normals,
     unproject (local XZ -> lat/lng, the exact inverse of build_track_osm.py's
     equirectangular projection) and bilinearly sample terrain.png at each edge
     (same sampling convention as populate_terrain_elevation.py).
  3. Treat the result as a CROSS-SLOPE, not two independent heights: compute
     slope_i = (rawLeft_i - rawRight_i) / width, smooth it with a circular
     arc-length moving average (window ~20 m, closed loop) and clamp |slope|
     to <= MAX_SLOPE. This keeps the centerline's own (already-smoothed) alt
     profile authoritative and only adds the differential tilt.
  4. Write altLeft_i = alt_i + slope_i * width/2, altRight_i = alt_i - slope_i * width/2.
     `alt`/`y` and every other existing field are left untouched (so old
     consumers — Minimap, projection, etc. — are unaffected and the schema
     addition is backward compatible).

`altLeft`/`altRight` are absolute metres ASL, same datum as `alt`.

Usage:
    python3 pipeline/bake_road_camber.py \\
        --track public/data/tracks/barber/track.json \\
        --terrain-image public/data/tracks/barber/terrain.png \\
        --terrain-meta public/data/tracks/barber/terrain_meta.json

Self-test (chirality assertion, no files touched):
    python3 pipeline/bake_road_camber.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: Pillow is required. Install with: python3 -m pip install Pillow", file=sys.stderr)
    sys.exit(1)

EARTH_METERS_PER_DEGREE = 111_320.0
DEFAULT_WINDOW_M = 20.0
DEFAULT_MAX_SLOPE = 0.08


# ---------- Terrain sampling (mirrors populate_terrain_elevation.py) ----------

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


# ---------- Local XZ <-> lat/lng (exact inverse of build_track_osm.project) ----------

def unproject(x: float, z: float, origin: dict) -> tuple[float, float]:
    """local (x, z) -> (lat, lng). Matches src/replay/projection.ts localToLatLng
    and build_track_osm.py's unproject (north = -z)."""
    lng_scale = EARTH_METERS_PER_DEGREE * math.cos(math.radians(origin["lat"]))
    return origin["lat"] - z / EARTH_METERS_PER_DEGREE, origin["lng"] + x / lng_scale


# ---------- Core camber baking ----------

def _open_points(centerline: list[dict]) -> tuple[list[dict], bool]:
    """Drop the closed-loop duplicate closing point for circular processing."""
    is_closed_duplicate = (
        len(centerline) > 1
        and abs(centerline[0]["x"] - centerline[-1]["x"]) < 1e-6
        and abs(centerline[0]["z"] - centerline[-1]["z"]) < 1e-6
    )
    return (centerline[:-1] if is_closed_duplicate else centerline), is_closed_duplicate


def left_normal_xz(tx: float, tz: float) -> tuple[float, float]:
    """Driver's LEFT direction in (x, z): up x forward = (tz, -tx).

    For a tangent pointing east (+x), left = (0, -1) ... wait, (tz, -tx) with
    (tx,tz)=(1,0) gives (0, -1) => -z. In scene space north = -z, so left of an
    east-heading car is NORTH. This is the sign the chirality tests pin down."""
    n = math.hypot(tx, tz) or 1.0
    return tz / n, -tx / n


def compute_edge_slopes(
    open_pts: list[dict],
    grid: "TerrainGridLike",
    width: float,
    origin: dict,
) -> tuple[list[float], list[float]]:
    """Return (raw_slope_per_point, raw_left_minus_right_debug)."""
    n = len(open_pts)
    half = width / 2.0
    raw_slopes: list[float] = []
    for i in range(n):
        p = open_pts[i]
        prev = open_pts[(i - 1) % n]
        nxt = open_pts[(i + 1) % n]
        tx = nxt["x"] - prev["x"]
        tz = nxt["z"] - prev["z"]
        lx, lz = left_normal_xz(tx, tz)
        # LEFT edge = center + half * leftDir ; RIGHT edge = center - half * leftDir
        left_x, left_z = p["x"] + half * lx, p["z"] + half * lz
        right_x, right_z = p["x"] - half * lx, p["z"] - half * lz
        lat_l, lng_l = unproject(left_x, left_z, origin)
        lat_r, lng_r = unproject(right_x, right_z, origin)
        h_left = grid.sample(lat_l, lng_l)
        h_right = grid.sample(lat_r, lng_r)
        raw_slopes.append((h_left - h_right) / width)
    return raw_slopes, raw_slopes


def circular_arclength_smooth(values: list[float], dists: list[float], total: float, window_m: float) -> list[float]:
    """Circular moving average over an arc-length window (closed loop)."""
    n = len(values)
    half_w = window_m / 2.0
    out: list[float] = []
    for i in range(n):
        di = dists[i]
        acc = 0.0
        cnt = 0
        for j in range(n):
            dd = abs(dists[j] - di)
            dd = min(dd, total - dd)  # circular
            if dd <= half_w:
                acc += values[j]
                cnt += 1
        out.append(acc / cnt if cnt else values[i])
    return out


def bake(
    track: dict,
    grid: "TerrainGridLike",
    window_m: float,
    max_slope: float,
) -> dict:
    """Populate altLeft/altRight on every centerline point. Returns a stats dict."""
    centerline = track["centerline"]
    width = float(track["width"])
    origin = track["origin"]
    open_pts, is_closed = _open_points(centerline)
    n = len(open_pts)

    raw_slopes, _ = compute_edge_slopes(open_pts, grid, width, origin)
    dists = [p["dist"] for p in open_pts]
    total = float(track.get("totalLength") or (dists[-1] if dists else 0.0))

    smooth = circular_arclength_smooth(raw_slopes, dists, total, window_m)

    clamped = 0
    slopes: list[float] = []
    for s in smooth:
        if abs(s) > max_slope:
            clamped += 1
            s = math.copysign(max_slope, s)
        slopes.append(s)

    half = width / 2.0
    max_abs = 0.0
    max_idx = 0
    for i, p in enumerate(open_pts):
        alt = p["alt"]
        s = slopes[i]
        p["altLeft"] = round(alt + s * half, 3)
        p["altRight"] = round(alt - s * half, 3)
        if abs(s) > max_abs:
            max_abs = abs(s)
            max_idx = i

    if is_closed:
        centerline[-1]["altLeft"] = centerline[0]["altLeft"]
        centerline[-1]["altRight"] = centerline[0]["altRight"]

    abs_slopes = sorted(abs(s) for s in slopes)
    p50 = statistics.median(abs_slopes) if abs_slopes else 0.0
    p90 = abs_slopes[min(len(abs_slopes) - 1, int(round(0.9 * (len(abs_slopes) - 1))))] if abs_slopes else 0.0
    mp = open_pts[max_idx]
    return {
        "count": n,
        "p50": p50,
        "p90": p90,
        "max": max_abs,
        "clamped": clamped,
        "max_loc": {"dist": mp["dist"], "x": mp["x"], "z": mp["z"], "lat": mp["lat"], "lng": mp["lng"]},
        "raw_slopes": raw_slopes,
        "slopes": slopes,
    }


# ---------- Self-test (chirality) ----------

class _SyntheticNorthHigherGrid:
    """Terrain whose altitude rises to the NORTH (higher latitude)."""

    def sample(self, lat: float, lng: float) -> float:  # noqa: D401
        return 100.0 + 1000.0 * lat  # +1 m per 0.001 deg lat -> north higher


TerrainGridLike = TerrainGrid  # for type hints only


def self_test() -> int:
    """East-heading straight, terrain higher to the NORTH.
    North is the driver's LEFT (up x forward, north = -z), so altLeft must
    exceed altRight everywhere. This pins the pipeline's left/right chirality."""
    origin = {"lat": 35.0, "lng": 139.0, "alt": 100.0}
    # East-heading centerline: x increases, z constant. dist along x.
    pts = []
    for k in range(11):
        x = k * 10.0
        z = 0.0
        lat, lng = unproject(x, z, origin)
        pts.append({"x": x, "z": z, "dist": float(k * 10), "lat": lat, "lng": lng, "alt": 100.0})
    track = {"width": 12.0, "origin": origin, "totalLength": 100.0, "centerline": pts + [dict(pts[0])]}
    track["centerline"][-1]["dist"] = 110.0
    # break the closed-duplicate detection off intentionally: use open list
    stats = bake(track, _SyntheticNorthHigherGrid(), window_m=20.0, max_slope=0.5)
    mid = track["centerline"][5]
    ok_lr = mid["altLeft"] > mid["altRight"]
    # slope should be positive (left higher)
    ok_slope = stats["slopes"][5] > 0
    print(f"[self-test] east heading, north higher: altLeft={mid['altLeft']:.3f} "
          f"altRight={mid['altRight']:.3f} slope[5]={stats['slopes'][5]:+.4f}")
    if ok_lr and ok_slope:
        print("[self-test] PASS: altLeft > altRight when north (driver-left) is higher")
        return 0
    print("[self-test] FAIL: chirality wrong", file=sys.stderr)
    return 1


# ---------- CLI ----------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bake road camber (altLeft/altRight) into track.json from terrain.png")
    p.add_argument("--track", type=Path, help="track.json to update in place")
    p.add_argument("--terrain-image", type=Path, help="terrain.png (Terrarium-encoded)")
    p.add_argument("--terrain-meta", type=Path, help="terrain_meta.json (bbox)")
    p.add_argument("--window", type=float, default=DEFAULT_WINDOW_M, help="Cross-slope smoothing window (m)")
    p.add_argument("--max-slope", type=float, default=DEFAULT_MAX_SLOPE, help="Cross-slope clamp (|slope| <=)")
    p.add_argument("--self-test", action="store_true", help="Run the chirality self-test and exit")
    p.add_argument("--dry-run", action="store_true", help="Compute + log stats but do not write files")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    if not (args.track and args.terrain_image and args.terrain_meta):
        print("ERROR: --track, --terrain-image and --terrain-meta are required", file=sys.stderr)
        return 2
    if not args.track.exists():
        print(f"ERROR: {args.track} not found", file=sys.stderr)
        return 1
    if not args.terrain_image.exists() or not args.terrain_meta.exists():
        print("ERROR: terrain image/meta not found", file=sys.stderr)
        return 1

    track = json.loads(args.track.read_text())
    terrain_meta = json.loads(args.terrain_meta.read_text())
    grid = TerrainGrid(args.terrain_image, terrain_meta)

    stats = bake(track, grid, args.window, args.max_slope)

    print(f"Track: {args.track}")
    print(f"  points (open loop):     {stats['count']}")
    print(f"  |slope| p50 / p90 / max: {stats['p50']*100:.2f}% / {stats['p90']*100:.2f}% / {stats['max']*100:.2f}%")
    print(f"  clamped (>{args.max_slope*100:.0f}%):        {stats['clamped']} / {stats['count']}")
    ml = stats["max_loc"]
    print(f"  max-camber location:    dist={ml['dist']:.1f} m  x={ml['x']:.1f} z={ml['z']:.1f}  "
          f"lat={ml['lat']:.6f} lng={ml['lng']:.6f}")

    if args.dry_run:
        print("  (dry-run: no files written)")
        return 0

    backup = args.track.with_name("track.p2-pre.bak.json")
    if not backup.exists():
        backup.write_text(json.dumps(json.loads(args.track.read_text()), indent=2) + "\n")
        print(f"  backed up pre-P2 track.json to {backup}")
    else:
        print(f"  backup already exists: {backup} (left untouched)")

    args.track.write_text(json.dumps(track, indent=2) + "\n")
    print(f"  wrote {args.track}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
