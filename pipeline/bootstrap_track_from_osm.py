#!/usr/bin/env python3
"""Bootstrap a track.json from OSM raceway geometry + GSI DEM elevation.

Produces a VISUALIZATION-GRADE centerline (OSM positional accuracy ~1-5 m,
no camber), clearly flagged as not-surveyed in the output. Attribution:
OpenStreetMap contributors (ODbL) for geometry, 国土地理院 標高タイル for
elevation.

Input: the raw Overpass JSON cached by an earlier query
(pipeline/cache/osm_<track>_raceway.json with `out tags geom;`).
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
METRES_PER_DEGREE = 111_320.0
DEM_SOURCES = ["dem5a", "dem5b", "dem"]  # try finest first
DEM_ZOOM = {"dem5a": 15, "dem5b": 15, "dem": 14}
USER_AGENT = "replay-viewer track-bootstrap/1.0"


# ── loop assembly ────────────────────────────────────────────────────────────

def way_length_m(geometry: list[dict], ref_lat: float) -> float:
    total = 0.0
    for a, b in zip(geometry, geometry[1:]):
        total += math.hypot((b["lon"] - a["lon"]) * METRES_PER_DEGREE * math.cos(math.radians(ref_lat)),
                            (b["lat"] - a["lat"]) * METRES_PER_DEGREE)
    return total


def key(point: dict) -> tuple[float, float]:
    return (round(point["lat"], 7), round(point["lon"], 7))


def assemble_loop(ways: list[dict], start_way_id: int, ref_lat: float) -> list[dict]:
    """Walk from `start_way_id`, at each junction choosing the straightest
    continuation, until the loop closes. Works on the mostly-degree-2 graph
    left after name filtering."""
    by_endpoint: dict[tuple[float, float], list[dict]] = {}
    for way in ways:
        for endpoint in (key(way["geometry"][0]), key(way["geometry"][-1])):
            by_endpoint.setdefault(endpoint, []).append(way)

    start = next(w for w in ways if w["id"] == start_way_id)
    loop = list(start["geometry"])
    used = {start["id"]}
    guard = 0
    while key(loop[-1]) != key(loop[0]) and guard < len(ways) + 5:
        guard += 1
        tail = key(loop[-1])
        # incoming direction
        ax = (loop[-1]["lon"] - loop[-2]["lon"]) * math.cos(math.radians(ref_lat))
        az = loop[-1]["lat"] - loop[-2]["lat"]
        norm = math.hypot(ax, az) or 1.0
        ax, az = ax / norm, az / norm
        candidates = []
        for way in by_endpoint.get(tail, []):
            if way["id"] in used:
                continue
            geometry = way["geometry"]
            if key(geometry[-1]) == tail:
                geometry = list(reversed(geometry))
            if key(geometry[0]) != tail:
                continue
            bx = (geometry[1]["lon"] - geometry[0]["lon"]) * math.cos(math.radians(ref_lat))
            bz = geometry[1]["lat"] - geometry[0]["lat"]
            bnorm = math.hypot(bx, bz) or 1.0
            candidates.append((ax * bx / bnorm + az * bz / bnorm, way, geometry))
        if not candidates:
            raise SystemExit(f"loop assembly dead-ends after {len(used)} ways "
                             f"({way_length_m(loop, ref_lat):.0f} m) at {tail}")
        candidates.sort(key=lambda c: -c[0])
        _, way, geometry = candidates[0]
        used.add(way["id"])
        loop.extend(geometry[1:])
    if key(loop[-1]) != key(loop[0]):
        raise SystemExit("loop failed to close")
    print(f"assembled loop: {len(used)} ways, {len(loop)} pts, "
          f"{way_length_m(loop, ref_lat):.0f} m")
    return loop[:-1]  # drop duplicated closing point


# ── resampling / orientation ─────────────────────────────────────────────────

def resample(points: list[dict], step: float, ref_lat: float) -> list[tuple[float, float]]:
    """Closed-loop resample to ~`step` metres, returns (lat, lon) tuples."""
    closed = points + [points[0]]
    out: list[tuple[float, float]] = []
    carry = 0.0
    for a, b in zip(closed, closed[1:]):
        seg = math.hypot((b["lon"] - a["lon"]) * METRES_PER_DEGREE * math.cos(math.radians(ref_lat)),
                         (b["lat"] - a["lat"]) * METRES_PER_DEGREE)
        if seg <= 1e-9:
            continue
        position = carry
        while position < seg:
            t = position / seg
            out.append((a["lat"] + (b["lat"] - a["lat"]) * t,
                        a["lon"] + (b["lon"] - a["lon"]) * t))
            position += step
        carry = position - seg
    return out


def ensure_clockwise(latlngs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Racing direction at both target circuits is clockwise in map view."""
    area = 0.0
    for (lat0, lon0), (lat1, lon1) in zip(latlngs, latlngs[1:] + latlngs[:1]):
        area += (lon1 - lon0) * (lat1 + lat0)  # shoelace on (x=lon, y=lat)
    # area > 0 -> clockwise in (lon, lat) plane
    return latlngs if area > 0 else list(reversed(latlngs))


def rotate_to_start(latlngs: list[tuple[float, float]], sf_lat: float, sf_lng: float) -> list[tuple[float, float]]:
    best = min(range(len(latlngs)),
               key=lambda i: (latlngs[i][0] - sf_lat) ** 2 + (latlngs[i][1] - sf_lng) ** 2)
    return latlngs[best:] + latlngs[:best]


# ── elevation (GSI DEM tiles) ────────────────────────────────────────────────

class DemSampler:
    def __init__(self, track: str) -> None:
        self.cache_dir = REPO_ROOT / "pipeline" / "cache" / f"gsi_{track}" / "dem"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tiles: dict[tuple[str, int, int, int], list[list[float]] | None] = {}

    def _tile(self, source: str, z: int, x: int, y: int):
        cache_key = (source, z, x, y)
        if cache_key in self.tiles:
            return self.tiles[cache_key]
        path = self.cache_dir / f"{source}_{z}_{x}_{y}.txt"
        if not path.exists():
            url = f"https://cyberjapandata.gsi.go.jp/xyz/{source}/{z}/{x}/{y}.txt"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    path.write_bytes(response.read())
                time.sleep(0.05)
            except urllib.error.HTTPError:
                path.write_text("MISSING", encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        if text.startswith("MISSING"):
            self.tiles[cache_key] = None
            return None
        grid = [[float("nan") if value == "e" else float(value)
                 for value in line.split(",")] for line in text.strip().split("\n")]
        self.tiles[cache_key] = grid
        return grid

    def elevation(self, lat: float, lng: float) -> float | None:
        for source in DEM_SOURCES:
            z = DEM_ZOOM[source]
            n = 2 ** z
            fx = (lng + 180) / 360 * n
            fy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
            grid = self._tile(source, z, int(fx), int(fy))
            if grid is None:
                continue
            px = min(255, int((fx - int(fx)) * 256))
            py = min(255, int((fy - int(fy)) * 256))
            value = grid[py][px]
            if not math.isnan(value):
                return value
        return None


def smooth(values: list[float], window: int = 9) -> list[float]:
    half = window // 2
    n = len(values)
    return [sum(values[(i + j) % n] for j in range(-half, half + 1)) / window
            for i in range(n)]


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True)
    parser.add_argument("--track-name", required=True)
    parser.add_argument("--osm-json", type=Path, required=True)
    parser.add_argument("--start-way", type=int, required=True,
                        help="OSM way id to start the loop walk from (e.g. main straight)")
    parser.add_argument("--exclude-name", default="",
                        help="regex of way names to exclude (pit lanes, kart tracks...)")
    parser.add_argument("--include-ids", default="",
                        help="comma-separated way ids to force-include")
    parser.add_argument("--sf-lat", type=float, required=True)
    parser.add_argument("--sf-lng", type=float, required=True)
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--expected-length", type=float, required=True)
    parser.add_argument("--length-tolerance", type=float, default=0.05)
    parser.add_argument("--step", type=float, default=7.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    data = json.loads(args.osm_json.read_text(encoding="utf-8"))
    ways = [w for w in data["elements"] if w.get("type") == "way" and w.get("geometry")]
    if args.exclude_name:
        import re
        pattern = re.compile(args.exclude_name)
        force = {int(v) for v in args.include_ids.split(",") if v}
        ways = [w for w in ways
                if w["id"] in force or not pattern.search(w.get("tags", {}).get("name", ""))]
    ref_lat = args.sf_lat

    loop = assemble_loop(ways, args.start_way, ref_lat)
    length = way_length_m(loop + [loop[0]], ref_lat)
    if abs(length - args.expected_length) / args.expected_length > args.length_tolerance:
        raise SystemExit(f"assembled length {length:.0f} m is outside "
                         f"±{args.length_tolerance:.0%} of expected {args.expected_length:.0f} m")

    latlngs = resample(loop, args.step, ref_lat)
    latlngs = ensure_clockwise(latlngs)
    latlngs = rotate_to_start(latlngs, args.sf_lat, args.sf_lng)

    dem = DemSampler(args.track)
    alts_raw = []
    for lat, lng in latlngs:
        value = dem.elevation(lat, lng)
        alts_raw.append(value)
    known = [v for v in alts_raw if v is not None]
    if not known:
        raise SystemExit("no DEM coverage at all")
    fallback = sum(known) / len(known)
    alts = smooth([v if v is not None else fallback for v in alts_raw])

    origin_lat, origin_lng = latlngs[0]
    origin_alt = alts[0]
    lng_scale = METRES_PER_DEGREE * math.cos(math.radians(origin_lat))
    centerline = []
    dist = 0.0
    prev_xz: tuple[float, float] | None = None
    for (lat, lng), alt in zip(latlngs, alts):
        x = (lng - origin_lng) * lng_scale
        z = -(lat - origin_lat) * METRES_PER_DEGREE
        if prev_xz is not None:
            dist += math.hypot(x - prev_xz[0], z - prev_xz[1])
        prev_xz = (x, z)
        centerline.append({
            "lat": round(lat, 8), "lng": round(lng, 8), "alt": round(alt, 2),
            "dist": round(dist, 1),
            "x": round(x, 3), "y": round(alt - origin_alt, 2), "z": round(z, 3),
        })
    total = dist + math.hypot(centerline[0]["x"] - prev_xz[0], centerline[0]["z"] - prev_xz[1])

    lats = [p["lat"] for p in centerline]
    lngs = [p["lng"] for p in centerline]
    track_json = {
        "version": 1,
        "trackId": args.track,
        "sourceTrackId": f"{args.track}_osm_bootstrap",
        "trackName": args.track_name,
        "width": args.width,
        "totalLength": round(total, 1),
        "elevationRange": [round(min(alts), 2), round(max(alts), 2)],
        "origin": {"lat": origin_lat, "lng": origin_lng, "alt": round(origin_alt, 2)},
        "bounds": {
            "lat": [min(lats), max(lats)], "lng": [min(lngs), max(lngs)],
            "x": [min(p["x"] for p in centerline), max(p["x"] for p in centerline)],
            "z": [min(p["z"] for p in centerline), max(p["z"] for p in centerline)],
        },
        "sectors": [
            {"id": "S1", "startDist": 0.0, "endDist": round(total / 3, 1)},
            {"id": "S2", "startDist": round(total / 3, 1), "endDist": round(2 * total / 3, 1)},
            {"id": "S3", "startDist": round(2 * total / 3, 1), "endDist": round(total, 1)},
        ],
        "zConvention": "north-negative-z",
        # Run the car directly on the aerial imagery: bootstrapped tracks have
        # no tuned procedural road, so the ribbon would only hide the photo.
        "defaultLayers": {"road3d": False},
        "provenance": {
            "kind": "OSM bootstrap — visualization grade, NOT surveyed",
            "geometry": "OpenStreetMap contributors (ODbL)",
            "elevation": "国土地理院 標高タイル (dem5a/dem5b/dem)",
            "positionalAccuracy": "~1-5 m (OSM), no camber data",
        },
        "centerline": centerline,
    }
    out = args.out or REPO_ROOT / "public" / "data" / "tracks" / args.track / "track.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(track_json, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"track.json: {out}  ({len(centerline)} pts, {total:.0f} m, "
          f"alt {min(alts):.0f}..{max(alts):.0f} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
