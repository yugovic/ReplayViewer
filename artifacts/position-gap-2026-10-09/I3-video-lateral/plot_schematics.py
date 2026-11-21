"""Schematic grid (NO video pixels): projected CG paint lines at dy = 0 and at the fixed-yaw dy, with the
frozen pick coordinates, for every scene.  Run with a python that has matplotlib (heavy venv)."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lo", HERE / "lateral_offset.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)
res = {s["id"]: s for s in json.loads((HERE / "lateral-offset-results.json").read_text(encoding="utf-8"))["scenes"]}
summ = {r["scene"]: r for r in json.loads((HERE / "lateral-offset-summary.json").read_text(encoding="utf-8"))["rows"]}
cam = lo.FROZEN


def proj(ident, name, dy):
    shot = lo.SHOTS[ident]
    fw, up, right = lo.car_axes(shot)
    C = np.array(shot["position"]) + up * 1.25 + fw * -0.55 + right * dy
    R = lo.rot(cam[2], cam[1], cam[3]) @ np.stack([right, -up, fw])
    loc = (lo.LOCAL[ident][name] - C) @ R.T
    z = loc[:, 2]
    uv = loc[:, :2] / np.maximum(z[:, None], 1e-5) * cam[0] + [640, 360]
    uv[(z < 2) | (z > 180)] = np.nan
    return uv


order = [r for r in lo.ROWS if r["group"] == "heldout"] + [r for r in lo.ROWS if r["group"] == "sequence"] + \
        [r for r in lo.ROWS if r["group"] == "train"]
n = len(order)
cols = 4
rows_n = int(np.ceil(n / cols))
fig, axes = plt.subplots(rows_n, cols, figsize=(cols * 4.2, rows_n * 2.75))
for ax, row in zip(axes.ravel(), order):
    s = res[row["id"]]
    dy = s["h1.25"]["fixedYaw"]["dyMeters"]
    for name, pts in row["lines"].items():
        c = "tab:blue" if name == "paintL" else "tab:orange"
        uv0 = proj(row["id"], name, 0.0)
        uv1 = proj(row["id"], name, dy)
        ax.plot(uv0[:, 0], uv0[:, 1], color="0.6", lw=1)
        ax.plot(uv1[:, 0], uv1[:, 1], color=c, lw=1.2)
        ax.scatter(pts[:, 0], pts[:, 1], s=8, color="k", zorder=5)
    ax.set_xlim(0, 1280)
    ax.set_ylim(500, 100)
    ax.set_xticks([])
    ax.set_yticks([])
    g = summ[row["id"]]["grade"]
    ax.set_title(f"{row['id'][5:]} [{row['group']}] s={s['stationMeters']:.0f}m\n"
                 f"dy={dy:+.2f} m  grade {g}  base {s['h1.25']['baseline']['medianPx']:.0f}px -> {s['h1.25']['fixedYaw']['medianPx']:.0f}px",
                 fontsize=7.5)
for ax in axes.ravel()[n:]:
    ax.axis("off")
fig.suptitle("I3 schematic (no video pixels): grey = CG paint lines as drawn, colour = after fixed-yaw dy, dots = frozen video picks\n"
             "blue = left boundary, orange = right boundary; image rows 100-500 of 1280x720", fontsize=9)
fig.tight_layout(rect=(0, 0, 1, 0.96))
fig.savefig(HERE / "lateral-offset-schematics.png", dpi=80)
print("saved")
