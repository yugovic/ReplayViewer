#!/usr/bin/env python3
"""Generate a SYNTHETIC course-preview race for a bootstrapped track.

One constant-speed lap along the track.json centerline so the viewer can be
opened (camera/imagery inspection) before real telemetry exists. Clearly
flagged as synthetic in every meta field — this is NOT driving data.

    python pipeline/make_preview_race.py --track suzuka --speed-kmh 120
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True)
    parser.add_argument("--speed-kmh", type=float, default=120.0)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    args = parser.parse_args()

    track = json.loads((REPO_ROOT / "public" / "data" / "tracks" / args.track / "track.json")
                       .read_text(encoding="utf-8"))
    centerline = track["centerline"]
    total = track["totalLength"]
    speed = args.speed_kmh / 3.6
    dt = 1.0 / args.rate_hz
    lap_time = total / speed
    samples = int(lap_time / dt) + 1

    # arc-length lookup over the closed centerline
    dists = [p["dist"] for p in centerline]

    def at(distance: float) -> tuple[float, float]:
        distance = distance % total
        lo, hi = 0, len(dists) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if dists[mid] <= distance:
                lo = mid + 1
            else:
                hi = mid
        i = max(0, lo - 1)
        a = centerline[i]
        b = centerline[(i + 1) % len(centerline)]
        span = (b["dist"] - a["dist"]) if b["dist"] > a["dist"] else (total - a["dist"])
        t = (distance - a["dist"]) / span if span > 0 else 0.0
        return (a["lat"] + (b["lat"] - a["lat"]) * t,
                a["lng"] + (b["lng"] - a["lng"]) * t)

    t_arr, lat_arr, lng_arr, dist_arr = [], [], [], []
    for i in range(samples):
        t = round(i * dt, 3)
        d = min(i * dt * speed, total)
        lat, lng = at(d)
        t_arr.append(t)
        lat_arr.append(round(lat, 8))
        lng_arr.append(round(lng, 8))
        dist_arr.append(round(d, 2))

    n = len(t_arr)
    zeros = [0] * n
    race_id = f"{args.track}_preview"
    vehicle = "course_preview"
    lap = {
        "meta": {
            "version": 1,
            "track_id": args.track,
            "race_id": race_id,
            "vehicle_id": vehicle,
            "vehicle_number": "0",
            "lap": 1,
            "lap_time_seconds": round(lap_time, 3),
            "lap_time": None,
            "point_count": n,
            "raw_gps_points": n,
            "gps_outliers_removed": 0,
            "total_distance_m": total,
            "source_file": "SYNTHETIC centerline preview (constant "
                           f"{args.speed_kmh:.0f} km/h) — not real driving data",
            "time_column": "t",
            "first_sample_time": "0",
            "last_sample_time": str(t_arr[-1]),
        },
        "t": t_arr,
        "lat": lat_arr,
        "lng": lng_arr,
        "speed": [args.speed_kmh] * n,
        "aps": zeros,
        "brake": zeros,
        "steer": zeros,
        "gear": [4] * n,
        "accx": zeros,
        "accy": zeros,
        "dist": dist_arr,
    }

    race_dir = REPO_ROOT / "public" / "data" / "races" / race_id
    race_dir.mkdir(parents=True, exist_ok=True)
    lap_file = f"{vehicle}_lap_001.json"
    (race_dir / lap_file).write_text(json.dumps(lap, ensure_ascii=False) + "\n", encoding="utf-8")
    minutes, seconds = divmod(lap_time, 60)
    lap_record = {
        "vehicle_id": vehicle,
        "vehicle_number": "0",
        "lap": 1,
        "lap_time_seconds": round(lap_time, 3),
        "lap_time": f"{int(minutes)}:{seconds:06.3f}",
        "start_time": "2026-01-01T00:00:00.000Z",
        "end_time": None,
        "outing": "0",
        "is_best_vehicle": True,
        "is_overall_best": True,
        "data_file": lap_file,
        "point_count": n,
        "total_distance_m": total,
    }
    laps_index = {
        "version": 1,
        "race_id": race_id,
        "track_id": args.track,
        "source_lap_times": "synthetic centerline preview",
        "laps": [lap_record],
        "selected": [{
            "vehicle_id": vehicle,
            "lap": 1,
            "data_file": lap_file,
            "point_count": n,
            "file_size": (race_dir / lap_file).stat().st_size,
            "total_distance_m": total,
        }],
    }
    (race_dir / "laps.json").write_text(json.dumps(laps_index, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(f"{race_id}: {n} samples, lap {lap_time:.1f}s -> {race_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
