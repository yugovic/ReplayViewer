#!/usr/bin/env python3
"""Track-limit registration: estimate the per-lap rigid GPS translation.

Why
---
A standalone 10 Hz GNSS logger reports positions with a slowly varying
absolute bias of a few metres.  Over one lap (~2 min) that bias is close to a
constant world-space translation, while the track map (traced from a surveyed
orthophoto) is fixed.  The visible symptom is a car that drives on the grass at
one corner and looks fine at another: the same translation projects onto the
road normal differently at every heading.

What
----
For every lap, find the translation d = (dx east, dz south) that makes the
four tyre contact points respect the traced track limits over the whole lap:

    J_lap(d) = sum_i sum_wheels huber(max(0, buffer - margin_iw(d)))
    d_session = argmin sum_laps J_lap(d) + eps |d|^2
    d_lap     = argmin J_lap(d) + shrink |d - d_session|^2

The raw lat/lng arrays are never modified.  The result is a display-only
registration written next to the race as gps_registration.json; the viewer adds
the offset after projecting each sample.  This is NOT a survey: it says where
the recorded line fits the traced road, with a jackknife spread as a guide.

Usage
-----
    python pipeline/register_gps_to_track.py --track fuji --race fuji_aim_01 --surface wet
    python pipeline/register_gps_to_track.py --track fuji --race fuji_aim_2020_07_30 --surface dry
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter
from scipy.optimize import minimize
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
METERS_PER_DEGREE = 111_320.0  # must match src/replay/projection.ts

# Dimensions used only to place four tyre contact points around the GPS point.
VEHICLE_WIDTH = 1.70       # outer tyre edge to outer tyre edge (display width)
VEHICLE_WHEELBASE = 2.60
HEADING_WINDOW_S = 0.6     # same +-0.6 s chord the viewer uses for heading
PIT_EXCLUDE_BEYOND_M = 4.0  # centre this far outside the road = pit lane / off
PIT_PAD_S = 8.0
EDGE_MEDIAN_WINDOW_M = 10.0  # removes tracing notches narrower than ~5 m
HUBER_DELTA_M = 0.75
SESSION_EPS = 1e-3
LAP_SHRINK = 1.5
ZONE_LENGTH_M = 250.0       # jackknife block along the lap


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Limits:
    centre: np.ndarray   # (n,2) XZ
    normal: np.ndarray   # (n,2) unit, pointing to the driver's right
    left: np.ndarray     # signed lateral offset of the left road edge (<0)
    right: np.ndarray    # signed lateral offset of the right road edge (>0)
    curb_left: np.ndarray   # kerb width outside the left edge (>=0)
    curb_right: np.ndarray
    dist: np.ndarray
    source: str
    source_sha256: str
    tree: cKDTree

    def margins(self, points: np.ndarray, curb_use: float) -> np.ndarray:
        """Signed distance inside the allowed band for (...,2) XZ points."""
        flat = points.reshape(-1, 2)
        _, idx = self.tree.query(flat)
        q = np.einsum("ij,ij->i", flat - self.centre[idx], self.normal[idx])
        left = q - (self.left[idx] - curb_use * self.curb_left[idx])
        right = (self.right[idx] + curb_use * self.curb_right[idx]) - q
        return np.minimum(left, right).reshape(points.shape[:-1])

    def lateral(self, points: np.ndarray):
        _, idx = self.tree.query(points)
        q = np.einsum("ij,ij->i", points - self.centre[idx], self.normal[idx])
        return idx, q


def load_limits(track_dir: Path) -> Limits:
    """Traced road edges + kerbs.  Fuji ships them in cg_study/geometry.json."""
    geometry = track_dir / "cg_study" / "geometry.json"
    if geometry.exists():
        g = json.loads(geometry.read_text(encoding="utf-8"))
        road = np.array([row[:11] for row in g["road"]], float)
        step = float(g.get("stationStep", 0.5))
        dist, centre, normal = road[:, 0], road[:, 1:3], road[:, 3:5]
        left, right = road[:, 5].copy(), road[:, 6].copy()
        curb_left = np.zeros(len(road))
        curb_right = np.zeros(len(road))
        for curb in g["curbs"]:
            target = curb_left if curb["side"] == "left" else curb_right
            for row in curb["rows"]:
                i = int(round(row[0] / step))
                if 0 <= i < len(target):
                    target[i] = max(target[i], abs(row[6] - row[5]))
        source = geometry
    else:
        track = json.loads((track_dir / "track.json").read_text(encoding="utf-8"))
        pts = track["centerline"]
        centre = np.array([[p["x"], p["z"]] for p in pts], float)
        dist = np.array([p["dist"] for p in pts], float)
        tangent = np.gradient(centre, axis=0)
        tangent /= np.maximum(1e-9, np.linalg.norm(tangent, axis=1))[:, None]
        normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
        half = float(track["width"]) / 2
        left, right = np.full(len(pts), -half), np.full(len(pts), half)
        curb_left = np.zeros(len(pts))
        curb_right = np.zeros(len(pts))
        step = float(np.median(np.diff(dist)))
        source = track_dir / "track.json"
    # A closed-loop median removes tracing notches (pit blend lines, start
    # gantry shadows) that would otherwise act as false walls.
    window = max(3, int(round(EDGE_MEDIAN_WINDOW_M / step)) | 1)
    left = median_filter(left, size=window, mode="wrap")
    right = median_filter(right, size=window, mode="wrap")
    return Limits(centre, normal, left, right, curb_left, curb_right, dist,
                  str(source.relative_to(ROOT)).replace("\\", "/"), sha256(source), cKDTree(centre))


def to_local(lat, lng, origin) -> np.ndarray:
    lat, lng = np.asarray(lat, float), np.asarray(lng, float)
    scale = METERS_PER_DEGREE * math.cos(math.radians(origin["lat"]))
    return np.stack([(lng - origin["lng"]) * scale, -(lat - origin["lat"]) * METERS_PER_DEGREE], axis=1)


def wheel_points(xy: np.ndarray, t: np.ndarray, antenna=(0.0, 0.0)) -> np.ndarray:
    """Four tyre contact points. `antenna` = (right, forward) metres of the GPS
    antenna from the vehicle centre; the centre is the GPS point minus that
    body-frame vector (it rotates with the heading, unlike the world shift)."""
    n = len(t)
    step = float(np.median(np.diff(t))) if n > 1 else 0.1
    k = max(1, int(round(HEADING_WINDOW_S / step)))
    a = np.clip(np.arange(n) - k, 0, n - 1)
    b = np.clip(np.arange(n) + k, 0, n - 1)
    heading = xy[b] - xy[a]
    heading /= np.maximum(1e-9, np.linalg.norm(heading, axis=1))[:, None]
    right = np.stack([-heading[:, 1], heading[:, 0]], axis=1)
    centre = xy - antenna[0] * right - antenna[1] * heading
    half_w, half_l = VEHICLE_WIDTH / 2, VEHICLE_WHEELBASE / 2
    return np.stack([centre + f * half_l * heading + s * half_w * right
                     for f in (1, -1) for s in (-1, 1)], axis=1)  # FL FR RL RR


def racing_mask(limits: Limits, xy: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Drop pit lane / big excursions plus padding; they are not track-limit evidence."""
    idx, q = limits.lateral(xy)
    beyond = np.maximum(limits.left[idx] - limits.curb_left[idx] - q,
                        q - limits.right[idx] - limits.curb_right[idx])
    keep = np.ones(len(t), bool)
    for i in np.flatnonzero(beyond > PIT_EXCLUDE_BEYOND_M):
        keep &= np.abs(t - t[i]) > PIT_PAD_S
    return keep


def huber(v: np.ndarray) -> np.ndarray:
    a = np.abs(v)
    return np.where(a <= HUBER_DELTA_M, 0.5 * a * a, HUBER_DELTA_M * (a - 0.5 * HUBER_DELTA_M))


@dataclass
class LapInput:
    record: dict
    t: np.ndarray
    xy: np.ndarray
    wheels: np.ndarray
    keep: np.ndarray
    station: np.ndarray
    sha: str


def lap_cost(d, lap: LapInput, limits: Limits, curb_use: float, buffer: float, mask=None) -> float:
    keep = lap.keep if mask is None else lap.keep & mask
    m = limits.margins(lap.wheels[keep] + np.asarray(d), curb_use)
    return float(huber(np.maximum(0.0, buffer - m)).sum())


def solve(cost, prior=(0.0, 0.0), coarse=True):
    """Coarse grid (robust to the flat far field) then Nelder-Mead polish."""
    grid = np.arange(-5.0, 5.01, 0.5) if coarse else ()
    best, best_value = np.asarray(prior, float), cost(prior)
    for dx in grid:
        for dz in grid:
            value = cost((dx, dz))
            if value < best_value - 1e-12:
                best, best_value = np.array([dx, dz]), value
    result = minimize(cost, best, method="Nelder-Mead",
                      options={"xatol": 1e-4, "fatol": 1e-8, "maxiter": 600})
    return (result.x, float(result.fun)) if result.fun <= best_value else (best, best_value)


def violation_summary(lap: LapInput, limits: Limits, d, curb_use: float) -> dict:
    m = limits.margins(lap.wheels[lap.keep] + np.asarray(d), curb_use).min(axis=1)
    out = m < -0.05
    dt = float(np.median(np.diff(lap.t)))
    return {
        "secondsBeyondLimit": round(float(out.sum() * dt), 2),
        "maxBeyondLimitMeters": round(float(max(0.0, -m.min())), 3),
        "samples": int(lap.keep.sum()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True)
    ap.add_argument("--race", required=True)
    ap.add_argument("--surface", choices=("dry", "wet"), required=True,
                    help="wet: painted kerbs are not part of the usable road; dry: kerbs are usable")
    ap.add_argument("--buffer", type=float, default=0.0,
                    help="soft clearance (m) kept between tyres and the limit")
    ap.add_argument("--antenna-right", type=float, default=0.0,
                    help="MEASURED GPS antenna position right of the vehicle centreline (m); do not guess")
    ap.add_argument("--antenna-forward", type=float, default=0.0,
                    help="MEASURED GPS antenna position ahead of the wheelbase centre (m)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--diagnostics", type=Path, default=None,
                    help="optional JSON with per-lap cost grids and jackknife detail")
    args = ap.parse_args()

    track_dir = ROOT / "public" / "data" / "tracks" / args.track
    race_dir = ROOT / "public" / "data" / "races" / args.race
    track = json.loads((track_dir / "track.json").read_text(encoding="utf-8"))
    limits = load_limits(track_dir)
    curb_use = 0.0 if args.surface == "wet" else 1.0
    antenna = (args.antenna_right, args.antenna_forward)
    index = json.loads((race_dir / "laps.json").read_text(encoding="utf-8"))

    laps: list[LapInput] = []
    for record in index["selected"]:
        path = race_dir / record["data_file"]
        data = json.loads(path.read_text(encoding="utf-8"))
        t = np.asarray(data["t"], float)
        xy = to_local(data["lat"], data["lng"], track["origin"])
        idx, _ = limits.lateral(xy)
        laps.append(LapInput(record, t, xy, wheel_points(xy, t, antenna), racing_mask(limits, xy, t),
                             limits.dist[idx], sha256(path)))

    def session_cost(d):
        return sum(lap_cost(d, lap, limits, curb_use, args.buffer) for lap in laps) + SESSION_EPS * float(np.dot(d, d))

    session, _ = solve(session_cost)
    total = float(track["totalLength"])
    zones = int(math.ceil(total / ZONE_LENGTH_M))
    out_laps, diag = [], {}
    for lap in laps:
        def cost(d, lap=lap, mask=None):
            delta = np.asarray(d) - session
            return lap_cost(d, lap, limits, curb_use, args.buffer, mask) + LAP_SHRINK * float(np.dot(delta, delta))
        d, _ = solve(cost, prior=session)
        # Delete-one-zone jackknife: how much does the answer depend on any
        # single 250 m stretch (one kerb, one tracing error, one excursion)?
        estimates = []
        for z in range(zones):
            mask = ~((lap.station >= z * ZONE_LENGTH_M) & (lap.station < (z + 1) * ZONE_LENGTH_M))
            if mask.all():
                continue
            # Local polish only: the full-lap optimum is already in the basin.
            estimates.append(solve(lambda v, m=mask: cost(v, mask=m), prior=d, coarse=False)[0])
        estimates = np.array(estimates)
        n = len(estimates)
        spread = np.sqrt((n - 1) / n * ((estimates - estimates.mean(axis=0)) ** 2).sum(axis=0)) if n > 1 else np.zeros(2)
        raw = violation_summary(lap, limits, (0.0, 0.0), curb_use)
        fit = violation_summary(lap, limits, d, curb_use)
        out_laps.append({
            "vehicleId": lap.record["vehicle_id"],
            "lap": lap.record["lap"],
            "dataFile": lap.record["data_file"],
            "dataSha256": lap.sha,
            "offsetMeters": [round(float(d[0]), 3), round(float(d[1]), 3)],
            "jackknifeSigmaMeters": [round(float(spread[0]), 3), round(float(spread[1]), 3)],
            "raw": raw,
            "registered": fit,
        })
        diag[f'{lap.record["vehicle_id"]}:{lap.record["lap"]}'] = {
            "jackknifeEstimates": np.round(estimates, 3).tolist(),
            "excludedSamples": int((~lap.keep).sum()),
        }
        print(f'{args.race} lap {lap.record["lap"]}: d=({d[0]:+.2f},{d[1]:+.2f}) m  '
              f'jk=({spread[0]:.2f},{spread[1]:.2f})  beyond-limit {raw["secondsBeyondLimit"]}s/'
              f'{raw["maxBeyondLimitMeters"]}m -> {fit["secondsBeyondLimit"]}s/{fit["maxBeyondLimitMeters"]}m')

    result = {
        "version": 1,
        "kind": "lap-translation",
        "status": "track-limit-fit",
        "raceId": args.race,
        "trackId": args.track,
        "frame": ("viewer local XZ metres: +x east, +z south; added after latLngToLocal. "
                  "A non-zero antenna offset is subtracted in the body frame first."),
        "definition": ("Display-only rigid translation per lap. Estimated so the four tyre contact points respect "
                       "the traced road limits over the whole lap. Raw lat/lng are unchanged. Not a survey."),
        "method": {
            "tool": "pipeline/register_gps_to_track.py",
            "surface": args.surface,
            "curbUse": curb_use,
            "bufferMeters": args.buffer,
            "vehicleWidthMeters": VEHICLE_WIDTH,
            "wheelbaseMeters": VEHICLE_WHEELBASE,
            "antennaOffsetMeters": {"right": antenna[0], "forward": antenna[1]},
            "huberDeltaMeters": HUBER_DELTA_M,
            "lapShrink": LAP_SHRINK,
            "edgeMedianWindowMeters": EDGE_MEDIAN_WINDOW_M,
            "pitExcludeBeyondMeters": PIT_EXCLUDE_BEYOND_M,
        },
        "limits": {"source": limits.source, "sha256": limits.source_sha256},
        "sessionOffsetMeters": [round(float(session[0]), 3), round(float(session[1]), 3)],
        "laps": out_laps,
    }
    out = args.out or race_dir / "gps_registration.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"session d=({session[0]:+.2f},{session[1]:+.2f}) m -> {out}")
    if args.diagnostics:
        args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics.write_text(json.dumps(diag, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
