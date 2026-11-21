"""Verifier V1b: lateral shift dy of the 3 IN-SAMPLE training frames under the frozen camera (own code).

If frames the camera was fitted on still need |dy| ~1.5 m, the per-scene method cannot resolve GPS lateral errors
of that size (or 7/29 L3 GPS itself varies by that much within the lap) - either way, held-out |dy| ~1.6 m is not
by itself evidence of a GPS error.  Also tests the cross-session 'same place' pairs: is one member in-sample?
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from v1_video_lateral import mount, qmat

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"


def main():
    g = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
    cam = json.loads((SRC / "camera-profile.json").read_text("utf-8"))
    train = json.loads((SRC / "train-annotations.json").read_text("utf-8"))["frames"]
    L = g["trackLength"]
    shots = {s["id"]: s for s in g["shots"]}
    M = mount(cam["yawDeg"], cam["pitchDeg"], cam["rollDeg"])
    f = cam["f"]
    grid = np.round(np.arange(-5.0, 5.0 + 1e-9, 0.01), 3)
    out = []
    for fr in train:
        s = shots[fr["id"]]
        R = qmat(s["quaternion"])
        fw, up, right = R[:, 2], R[:, 1], -R[:, 0]
        base = np.array(s["position"]) + up * cam["upMeters"] + fw * cam["forwardMeters"]
        B = M @ np.stack([right, -up, fw])
        cols = {}
        for name, obs in fr["lines"].items():
            obs = np.array(obs, float)
            P = np.array([v["point"] for v in g["lines"][name] if -5 < (v["s"] - s["station"] + L / 2) % L - L / 2 < 180])
            prof = []
            for dy in grid:
                pc = (B @ (P - (base + dy * right)).T).T
                z = pc[:, 2]
                uv = pc[:, :2] / np.maximum(z[:, None], 1e-5) * f + [640.0, 360.0]
                ok = (z[:-1] > 2) & (z[1:] > 2) & (z[:-1] < 180) & (z[1:] < 180)
                a, b = uv[:-1][ok], uv[1:][ok]
                v = b - a
                r = []
                for p in obs:
                    t = np.clip(np.sum((p - a) * v, 1) / np.maximum(np.sum(v * v, 1), 1e-12), 0, 1)
                    r.append(np.min(np.linalg.norm(a + t[:, None] * v - p, axis=1)))
                prof.append(r)
            cols[name] = np.array(prof)
        D = np.concatenate(list(cols.values()), axis=1)
        soft = np.sum(32 * (np.sqrt(1 + (D / 4) ** 2) - 1), 1)
        rec = {"scene": fr["id"], "station": s["station"], "baselineMedianPx": float(np.median(D[np.argmin(np.abs(grid))])),
               "dySoftL1": float(grid[np.argmin(soft)]),
               "perSide": {k: float(grid[np.argmin(np.sum(32 * (np.sqrt(1 + (v / 4) ** 2) - 1), 1))]) for k, v in cols.items()},
               "points": {k: v.shape[1] for k, v in cols.items()}}
        out.append(rec)
        print(rec)
    (HERE / "v1b_training_floor.json").write_text(json.dumps({"trainingFrames": out}, indent=1) + "\n", "utf-8")


if __name__ == "__main__":
    main()
