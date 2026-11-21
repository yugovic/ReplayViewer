"""Diagnostic: per-boundary cost profiles over dy and schematic projections (no video pixels).

Writes schematic PNGs (projected CG paint lines + pick coordinates on a blank canvas) and
profile JSON to the directory given by I3_WORK (default: ./_diag next to this file).
Run with the heavy venv python (matplotlib).
"""
import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lo", HERE / "lateral_offset.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)
WORK = Path(os.environ.get("I3_WORK", HERE / "_diag"))
WORK.mkdir(parents=True, exist_ok=True)
cam = np.array([lo.FIT["profile"][k] for k in ["f", "pitchDeg", "yawDeg", "rollDeg"]])
H = 1.25
only = os.environ.get("I3_ONLY")


def projected(ident, name, dy, dyaw=0.0):
    C, R = lo.camera_batch(lo.SHOTS[ident], cam, H, np.array([dy]), np.array([dyaw]))
    P = lo.LOCAL[ident][name]
    loc = (P - C[0]) @ R[0].T
    z = loc[:, 2]
    uv = loc[:, :2] / np.maximum(z[:, None], 1e-5) * cam[0] + [640, 360]
    ok = (z > 2) & (z < 180)
    return uv, ok, z


prof = {}
for row in lo.ROWS:
    if only and only not in row["id"]:
        continue
    ident = row["id"]
    prof[ident] = {}
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.6))
    ax = axes[0]
    for name, pts in row["lines"].items():
        D = lo.line_distances(ident, name, pts, cam, H, lo.DY_GRID, 0.0)
        c = lo.rho(D).sum(axis=1)
        prof[ident][name] = {"dy": lo.DY_GRID[::10].tolist(), "cost": c[::10].round(2).tolist(),
                             "argmin": float(lo.DY_GRID[np.argmin(c)])}
        axes[1].plot(lo.DY_GRID, c, label=name)
        col = "tab:blue" if name == "paintL" else "tab:orange"
        for dy, ls in [(0.0, "-"), (prof[ident][name]["argmin"], ":")]:
            uv, ok, z = projected(ident, name, dy)
            seg = np.where(ok, 1, np.nan)
            ax.plot(uv[:, 0] * seg, uv[:, 1] * seg, ls, color=col, lw=1)
        ax.scatter(pts[:, 0], pts[:, 1], s=14, color=col, edgecolor="k", zorder=5)
        # depth labels along the dy=0 projection
        uv, ok, z = projected(ident, name, 0.0)
        for zz in (5, 10, 20, 40, 80):
            i = np.argmin(np.abs(np.where(ok, z, 1e9) - zz))
            if ok[i] and 0 <= uv[i, 0] <= 1280 and 0 <= uv[i, 1] <= 720:
                ax.annotate(f"{zz}m", uv[i], fontsize=6, color=col)
    ax.set_xlim(0, 1280)
    ax.set_ylim(720, 0)
    ax.set_aspect("equal")
    ax.set_title(f"{ident}: solid = CG at dy=0, dotted = per-boundary argmin; dots = picks")
    axes[1].set_xlabel("dy [m] (+ = car actually further right)")
    axes[1].set_ylabel("robust cost")
    axes[1].legend()
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(WORK / f"schematic-{ident}.png", dpi=90)
    plt.close(fig)
(WORK / "profiles.json").write_text(json.dumps(prof, indent=1), encoding="utf-8")
print("wrote", WORK)
