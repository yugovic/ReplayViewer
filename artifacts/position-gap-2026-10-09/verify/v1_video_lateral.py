"""Adversarial verifier V1: independent re-implementation of the video-vs-CG lateral conversion.

Recomputes, without importing or copying I1/I3 code:
  (a) the held-out per-scene median pixel residual of the frozen Codex camera (published 9.42 px median of medians),
  (b) a per-scene lateral shift dy of the whole camera along the drawn car's right axis
      (+ = car actually further RIGHT than drawn) by brute-force 1-D grid search under two different objectives
      (soft-L1 4 px = I3's choice, and plain median residual = a different robust objective),
  (c) a per-point lateral equivalent: the dy that best fits each single pick on its own (camera-shift method, not
      I1's ground-plane-intersection method), compared point-by-point with I1's px_to_metres_points.json,
  (d) the "lateral / naive" ratio and sign-convention consistency between I1 (signedRightMetres: + = video
      boundary right of CG => real car LEFT) and I3 (dy: + = real car RIGHT).

Own quaternion->matrix code (no scipy Rotation) so a convention error shared with I1/I3 would show up as a
mismatch in (a).  Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
I1 = ROOT / "artifacts" / "position-gap-2026-10-09" / "I1-gap-budget"
I3 = ROOT / "artifacts" / "position-gap-2026-10-09" / "I3-video-lateral"


def qmat(q):
    """three.js quaternion [x, y, z, w] -> 3x3 rotation matrix (own implementation)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def mount(yaw, pitch, roll):
    y, p, r = map(math.radians, (yaw, pitch, roll))
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Ry = np.array([[math.cos(y), 0, -math.sin(y)], [0, 1, 0], [math.sin(y), 0, math.cos(y)]])
    return Rz @ Rx @ Ry


def main():
    g = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
    cam = json.loads((SRC / "camera-profile.json").read_text("utf-8"))
    held = json.loads((SRC / "evidence" / "heldout-boundary-picks.json").read_text("utf-8"))
    L = g["trackLength"]
    shots = {s["id"]: s for s in g["shots"]}
    f = cam["f"]
    M = mount(cam["yawDeg"], cam["pitchDeg"], cam["rollDeg"])

    i1pts = json.loads((I1 / "px_to_metres_points.json").read_text("utf-8"))
    i1pts = [p for p in i1pts if p["set"] == "heldout10" and p["camera"] == "frozen_h1.25"]
    i3 = {r["scene"]: r for r in json.loads((I3 / "lateral-offset-summary.json").read_text("utf-8"))["rows"]}

    grid = np.round(np.arange(-5.0, 5.0 + 1e-9, 0.01), 3)
    out_scenes = []
    point_pairs = []
    for row in held["shots"]:
        s = shots[row["id"]]
        R = qmat(s["quaternion"])
        fw, up, left = R[:, 2], R[:, 1], R[:, 0]
        right = -left
        base = np.array(s["position"]) + up * cam["upMeters"] + fw * cam["forwardMeters"]
        B = M @ np.stack([right, -up, fw])

        lines = {}
        for name in ("paintL", "paintR"):
            pts = [v["point"] for v in g["lines"][name]
                   if -5 < (v["s"] - s["station"] + L / 2) % L - L / 2 < 180]
            lines[name] = np.array(pts, float)

        def dists(dy, name, obs):
            C = base + dy * right
            pc = (B @ (lines[name] - C).T).T
            z = pc[:, 2]
            uv = pc[:, :2] / np.maximum(z[:, None], 1e-5) * f + np.array([640.0, 360.0])
            ok = (z[:-1] > 2) & (z[1:] > 2) & (z[:-1] < 180) & (z[1:] < 180)
            a, b = uv[:-1][ok], uv[1:][ok]
            v = b - a
            res = []
            for p in obs:
                t = np.clip(np.sum((p - a) * v, axis=1) / np.maximum(np.sum(v * v, axis=1), 1e-12), 0, 1)
                res.append(float(np.min(np.linalg.norm(a + t[:, None] * v - p, axis=1))))
            return np.array(res)

        obs = [("paintL" if a["side"] == "left" else "paintR", np.array(a["points"], float)) for a in row["annotations"]]
        # (a) baseline
        base_d = np.concatenate([dists(0.0, n, o) for n, o in obs])
        # (b) grid search
        D = np.array([np.concatenate([dists(dy, n, o) for n, o in obs]) for dy in grid])  # (G, N)
        softl1 = np.sum(2 * 16 * (np.sqrt(1 + (D / 4) ** 2) - 1), axis=1)
        med = np.median(D, axis=1)
        dy_soft = float(grid[np.argmin(softl1)])
        dy_med = float(grid[np.argmin(med)])
        # per side
        side_dy = {}
        col = 0
        for n, o in obs:
            k = len(o)
            sub = D[:, col:col + k]
            side_dy[n] = side_dy.get(n, []) + [float(grid[np.argmin(np.sum(2 * 16 * (np.sqrt(1 + (sub / 4) ** 2) - 1), axis=1))])]
            col += k
        # (c) per-point best dy (only points whose own profile reaches < 1 px within the grid = "fittable")
        per_point = []
        col = 0
        for n, o in obs:
            for j in range(len(o)):
                prof = D[:, col]
                k = int(np.argmin(prof))
                per_point.append({"line": n, "bestDy": float(grid[k]), "minPx": float(prof[k]),
                                  "baselinePx": float(prof[np.argmin(np.abs(grid))]),
                                  "atBound": bool(k in (0, len(grid) - 1))})
                col += 1
        # pair with I1 points (same order: annotations then points, same scene)
        mine_i1 = [p for p in i1pts if p["scene"] == row["id"]]
        assert len(mine_i1) == len(per_point), (row["id"], len(mine_i1), len(per_point))
        for a, b in zip(per_point, mine_i1):
            assert abs(a["baselinePx"] - b["residualPx"]) < 1e-6, (row["id"], a["baselinePx"], b["residualPx"])
            point_pairs.append({"scene": row["id"], "line": a["line"], "myBestDy": a["bestDy"], "myMinPx": a["minPx"],
                                "atBound": a["atBound"], "i1SignedRight": b.get("signedRightMetres"),
                                "i1Lateral": b.get("lateralMetres"), "i1Pick4px": b.get("pick4pxMetres"),
                                "i1Naive": b["naiveMetres"], "residualPx": b["residualPx"]})
        fittable = [p for p in per_point if p["minPx"] < 1.0 and not p["atBound"]]
        out_scenes.append({
            "scene": row["id"],
            "baselineMedianPx": float(np.median(base_d)),
            "dySoftL1_4px": dy_soft,
            "dyMedianObjective": dy_med,
            "medianPxAtDySoft": float(np.median(D[np.argmin(softl1)])),
            "dyPerSideSoftL1": side_dy,
            "perPointFittable": len(fittable),
            "perPointMedianAbsDy": float(np.median([abs(p["bestDy"]) for p in fittable])) if fittable else None,
            "perPointMedianDy": float(np.median([p["bestDy"] for p in fittable])) if fittable else None,
            "i3DyFixedYaw": i3[row["id"]]["dyFixedYawM"],
            "i3Grade": i3[row["id"]].get("grade"),
        })

    # sign convention check: I1 signedRight (+ = video boundary to the right => real car left) vs my per-point dy
    pairs = [p for p in point_pairs if p["i1SignedRight"] is not None and not p["atBound"] and p["myMinPx"] < 1.0]
    x = np.array([p["i1SignedRight"] for p in pairs])
    y = np.array([p["myBestDy"] for p in pairs])
    cond = [p for p in pairs if p["i1Pick4px"] is not None and p["i1Pick4px"] <= 1.0]
    xc = np.array([p["i1SignedRight"] for p in cond])
    yc = np.array([p["myBestDy"] for p in cond])
    ratio = [p["i1Lateral"] / p["i1Naive"] for p in point_pairs if p["i1Lateral"] is not None and p["i1Naive"] > 1e-9]

    summary = {
        "what": "Independent recomputation of video-vs-CG pixel->metre conversion (verifier V1)",
        "baselineMedianOfSceneMediansPx": float(np.median([s["baselineMedianPx"] for s in out_scenes])),
        "scenes": out_scenes,
        "signConvention": {
            "pairsFittable": len(pairs),
            "corr_I1signedRight_vs_myDy": float(np.corrcoef(x, y)[0, 1]),
            "fractionOppositeSign": float(np.mean(np.sign(x) != np.sign(y))),
            "conditionedPairs": len(cond),
            "conditioned_corr": float(np.corrcoef(xc, yc)[0, 1]) if len(cond) > 2 else None,
            "conditioned_median_abs_diff_of_magnitude": float(np.median(np.abs(np.abs(xc) - np.abs(yc)))) if len(cond) else None,
            "conditioned_median_I1_abs": float(np.median(np.abs(xc))) if len(cond) else None,
            "conditioned_median_my_abs": float(np.median(np.abs(yc))) if len(cond) else None,
            "expected": "negative correlation: I1 signedRight + means real car LEFT; my dy + means real car RIGHT",
        },
        "lateralOverNaiveMedian_recomputed_from_I1_points": float(np.median(ratio)),
        "pointPairs": point_pairs,
    }
    (HERE / "v1_video_lateral.json").write_text(json.dumps(summary, indent=1) + "\n", "utf-8")
    print("baseline med-of-med px", round(summary["baselineMedianOfSceneMediansPx"], 4))
    for s in out_scenes:
        print(f"{s['scene']:24s} base {s['baselineMedianPx']:6.2f}px  dy_soft {s['dySoftL1_4px']:+.2f}  dy_med {s['dyMedianObjective']:+.2f}"
              f"  sides {s['dyPerSideSoftL1']}  ptMed|dy| {s['perPointMedianAbsDy']} (n={s['perPointFittable']})  I3 {s['i3DyFixedYaw']:+.2f} {s['i3Grade']}")
    print(json.dumps(summary["signConvention"], indent=1))
    print("lateral/naive median", summary["lateralOverNaiveMedian_recomputed_from_I1_points"])


if __name__ == "__main__":
    main()
