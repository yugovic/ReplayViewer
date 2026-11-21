"""Verifier V5b: curvature profile of the CG paint lines (runtime-geometry.json) through 100R (1840-2020 m)
versus the drawn 7/30 L2 car path, to test whether the 100R-exit dy swing is a CG inside-line artifact.
Heading of each line is taken over 10 m chords; output: per 5 m station, line heading and implied radius."""
from __future__ import annotations
import json, math, os
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
g = json.loads((ROOT / "artifacts/video-cg-alignment-2026-10-03/runtime-geometry.json").read_text("utf-8"))
out = {}
for name in ("paintL", "paintR"):
    rows = sorted((r["s"], r["point"]) for r in g["lines"][name] if 1800 < r["s"] < 2060)
    s = np.array([a for a, _ in rows]); P = np.array([b for _, b in rows])
    prof = []
    for st in np.arange(1850, 2016, 5.0):
        i0, i1 = np.searchsorted(s, st - 5), np.searchsorted(s, st + 5)
        i1 = min(i1, len(s) - 1)
        d = P[i1] - P[i0]
        yaw = math.degrees(math.atan2(d[0], d[2]))
        prof.append([float(st), yaw])
    prof = np.array(prof)
    dyaw = (np.diff(prof[:, 1]) + 180) % 360 - 180
    rad = [float(5.0 / max(abs(math.radians(x)), 1e-6)) for x in dyaw]
    out[name] = {"spacingMedianM": float(np.median(np.diff(s))),
                 "rows": [{"station": float(a), "yawDeg": round(float(b), 2), "dYawPer5m": (round(float(c), 2) if c is not None else None), "radiusM": (round(r, 1) if r is not None else None)}
                          for a, b, c, r in zip(prof[:, 0], prof[:, 1], [None] + list(dyaw), [None] + rad)]}
lap = json.loads((ROOT / "public/data/races/fuji_aim_2020_07_30/osaki_hmr_demio_101_lap_002.json").read_text("utf-8"))
v = np.asarray(lap["speed"]) / 3.6; ay = np.asarray(lap["accy"]) * 9.80665
m = (np.asarray(lap["t"]) > 43.5) & (np.asarray(lap["t"]) < 47.5)
out["carPathRadiusFromAccyM"] = {"median": float(np.median(v[m] ** 2 / np.maximum(np.abs(ay[m]), 0.1))), "p10": float(np.percentile(v[m] ** 2 / np.maximum(np.abs(ay[m]), 0.1), 10)), "p90": float(np.percentile(v[m] ** 2 / np.maximum(np.abs(ay[m]), 0.1), 90))}
(HERE / "v5b_line_curvature.json").write_text(json.dumps(out, indent=1) + "\n", "utf-8")
print("spacing", out["paintL"]["spacingMedianM"], out["paintR"]["spacingMedianM"], "car radius", out["carPathRadiusFromAccyM"])
for a, b in zip(out["paintL"]["rows"], out["paintR"]["rows"]):
    print(a["station"], "L", a["yawDeg"], a["radiusM"], "| R", b["yawDeg"], b["radiusM"])
