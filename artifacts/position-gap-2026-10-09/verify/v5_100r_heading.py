"""Verifier V5: is the 100R-exit 'dy swing of 5.3 m in 73 m' (I3 sequence 44.0-46.0 s, 7/30 L2) physically plausible
as a GPS error, or does the drawn pose / CG line show a discontinuity there?

For the drawn poses (offset 0) at 44.0/44.5/45.0/45.5/46.0/47.522 s: car heading (from the runtime quaternion),
paint-line tangent heading at the car station, heading-minus-road, and the yaw change implied by the logged lateral
acceleration (a_y / v integrated over the 0.5 s step) versus the drawn heading change.
Also lateral distance of the drawn car from each paint line (CG) at each pose.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

from v1_video_lateral import qmat

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"


def yaw_of(v):
    return math.degrees(math.atan2(v[0], v[2]))  # heading in the x-z plane (three.js y-up)


def main():
    g = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
    L = g["trackLength"]
    seq = json.loads((SRC / "evidence" / "sequence" / "runtime-poses.json").read_text("utf-8"))
    poses = {}
    for s in seq["shots"]:
        p0 = next(p for p in s["poses"] if p["offset"] == 0)
        poses[s["id"]] = (p0, s["afterPlaybackSeconds"])
    for s in g["shots"]:
        if s["id"].startswith("2020-07-30-L2"):
            m = json.loads((SRC / "evidence" / "heldout-boundary-picks.json").read_text("utf-8"))
            t = next((r["afterPlaybackSeconds"] for r in m["shots"] if r["id"] == s["id"]), s["time"])
            poses.setdefault(s["id"], (s, t))
    lapd = json.loads((ROOT / "public" / "data" / "races" / "fuji_aim_2020_07_30" / "osaki_hmr_demio_101_lap_002.json").read_text("utf-8"))
    tt = np.asarray(lapd["t"], float)
    ay = np.asarray(lapd["accy"], float) * 9.80665
    v = np.asarray(lapd["speed"], float) / 3.6
    keys = sorted([k for k in poses if k.startswith("2020-07-30-L2-4")], key=lambda k: float(k.split("-")[-1]))
    rows = []
    for k in keys:
        p, t = poses[k]
        R = qmat(p["quaternion"])
        fw = R[:, 2]
        st = p["station"]
        rec = {"scene": k, "t": t, "station": round(st, 1), "carYawDeg": yaw_of(fw)}
        for name in ("paintL", "paintR"):
            pts = [(r["s"], r["point"]) for r in g["lines"][name] if abs((r["s"] - st + L / 2) % L - L / 2) < 6]
            pts.sort()
            P = np.array([q for _, q in pts])
            tan = P[-1] - P[0]
            rec[f"{name}YawDeg"] = yaw_of(tan)
            # lateral distance of car position to the polyline (horizontal)
            pos = np.array(p["position"])
            A, B = P[:-1], P[1:]
            vv = B - A
            tpar = np.clip(np.sum((pos - A) * vv, 1) / np.maximum(np.sum(vv * vv, 1), 1e-12), 0, 1)
            near = A + tpar[:, None] * vv
            d = near - pos
            d[:, 1] = 0
            rec[f"{name}DistM"] = float(np.min(np.linalg.norm(d, axis=1)))
        rec["carMinusRoadDeg"] = (rec["carYawDeg"] - rec["paintRYawDeg"] + 180) % 360 - 180
        rows.append(rec)
    for a, b in zip(rows, rows[1:]):
        dt = b["t"] - a["t"]
        m = (tt >= a["t"]) & (tt <= b["t"])
        imu_yaw = float(np.degrees(np.trapz(ay[m] / np.maximum(v[m], 1), tt[m]))) if m.sum() > 1 else None
        b["drawnYawChangeDeg"] = (b["carYawDeg"] - a["carYawDeg"] + 180) % 360 - 180
        b["roadYawChangeDeg"] = (b["paintRYawDeg"] - a["paintRYawDeg"] + 180) % 360 - 180
        b["accyOverSpeedYawChangeDeg"] = imu_yaw
        b["dtS"] = dt
    (HERE / "v5_100r_heading.json").write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    for r in rows:
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
