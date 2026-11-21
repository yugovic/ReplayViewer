"""Synthetic self-test of px_to_metres.evaluate_scene: shift the CG paint line by a known lateral amount (in the road
plane, perpendicular to the line), project the shifted line with the frozen camera, use those projections as picks,
and check that the method recovers the shift. No video data is used. Writes selftest_px_to_metres.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import px_to_metres as M  # noqa: E402

g = json.loads((M.SRC / "runtime-geometry.json").read_text("utf-8"))
prof = json.loads((M.SRC / "camera-profile.json").read_text("utf-8"))
cam = {"f": prof["f"], "pitch": prof["pitchDeg"], "yaw": prof["yawDeg"], "roll": prof["rollDeg"],
       "up": prof["upMeters"], "forward": prof["forwardMeters"], "right": prof["rightMeters"]}
results = []
for shot in g["shots"]:
    pos, b, up, right, fw = M.camera_from_pose(shot["position"], shot["quaternion"], cam)
    for name in ("paintL", "paintR"):
        P = M.segment_window(g["lines"][name], shot["station"], g["trackLength"], -5, 180)
        n = up / np.linalg.norm(up)
        T = np.gradient(P, axis=0)
        T /= np.linalg.norm(T, axis=1)[:, None]
        nb = np.cross(n, T)
        nb[(nb @ right) < 0] *= -1
        for shift in (0.5, 1.0, -1.0):
            Ps = P + shift * nb
            pc = (b @ (Ps - pos).T).T
            ok = (pc[:, 2] > 6) & (pc[:, 2] < 25)
            if ok.sum() < 3:
                continue
            xy = pc[ok, :2] / pc[ok, 2, None] * cam["f"] + [640, 360]
            inside = (xy[:, 0] > 0) & (xy[:, 0] < 1280) & (xy[:, 1] > 0) & (xy[:, 1] < 720)
            picks = xy[inside][:: max(1, inside.sum() // 6)].tolist()
            if len(picks) < 3:
                continue
            ann = [{"side": "left" if name == "paintL" else "right", "points": picks}]
            rows = M.evaluate_scene(shot, ann, lambda a: "paintL" if a["side"] == "left" else "paintR", g, cam, (-5, 180))
            rec = [x["signedRightMetres"] for x in rows if x.get("signedRightMetres") is not None]
            if rec:
                results.append({"scene": shot["id"], "line": name, "trueShiftRightM": shift,
                                "recoveredMedianM": float(np.median(rec)), "n": len(rec),
                                "naiveMedianM": float(np.median([x["naiveMetres"] for x in rows]))})
err = [abs(r["recoveredMedianM"] - r["trueShiftRightM"]) for r in results]
out = {"cases": len(results), "maxAbsErrorM": float(max(err)), "medianAbsErrorM": float(np.median(err)),
       "naiveOverTrueMedian": float(np.median([abs(r["naiveMedianM"] / r["trueShiftRightM"]) for r in results])),
       "rows": results}
(HERE / "selftest_px_to_metres.json").write_text(json.dumps(out, indent=1) + "\n", "utf-8")
print({k: out[k] for k in ("cases", "maxAbsErrorM", "medianAbsErrorM", "naiveOverTrueMedian")})
