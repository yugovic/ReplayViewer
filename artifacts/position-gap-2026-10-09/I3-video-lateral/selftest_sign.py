"""Synthetic recovery test of the dy convention: picks generated from the CG lines seen by a camera
moved dy_true along the car right axis must be recovered as +dy_true.  Writes selftest-sign.json."""
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lo", HERE / "lateral_offset.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)
cam = lo.FROZEN
out = []
for ident in ["2020-07-30-L2-47.522", "2020-07-30-L2-84.000", "2020-07-29-L3-52.387"]:
    row = next(r for r in lo.ROWS if r["id"] == ident)
    shot = lo.SHOTS[ident]
    fw, up, right = lo.car_axes(shot)
    for true_dy in [1.0, -1.5, 2.5]:
        C = np.array(shot["position"]) + up * 1.25 + fw * -0.55 + right * true_dy
        R = lo.rot(cam[2], cam[1], cam[3]) @ np.stack([right, -up, fw])
        lines = {}
        for n in row["lines"]:
            loc = (lo.LOCAL[ident][n] - C) @ R.T
            z = loc[:, 2]
            ok = (z > 6) & (z < 40)
            lines[n] = (loc[ok, :2] / z[ok, None] * cam[0] + [640, 360])[::8][:6]
        D, _ = lo.scene_distances(dict(row, lines=lines), cam, 1.25, lo.DY_GRID)
        out.append({"scene": ident, "trueDy": true_dy, "recoveredDy": lo.grid_fit(D, lo.DY_GRID)})
(HERE / "selftest-sign.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print(json.dumps(out, indent=1))
