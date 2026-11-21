"""R2-vision: where in the scene are the existing 2026-10-03 boundary picks (lateral offset X, depth Z)?

For every training / held-out / diagnostic-sequence pick, find the nearest projected CG paint segment
under the frozen Codex camera (same projection as evidence/independent-camera-evaluation.py) and
report that model point's camera-frame lateral offset X (horizontal, + right) and forward depth Z.
This tells how much lateral leverage the existing picks have (see sensitivity.json: lateral metres per
pixel grow ~linearly with Z and with |X|/h).
Inputs (repo-relative): artifacts/video-cg-alignment-2026-10-03/{runtime-geometry.json, camera-profile.json,
train-annotations.json, evidence/heldout-boundary-picks.json, evidence/sequence/sequence-annotations.json,
evidence/sequence/runtime-poses.json}. No frame image is opened. Output: pick_leverage.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
g = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
prof = json.loads((SRC / "camera-profile.json").read_text("utf-8"))
L = g["trackLength"]
LINES = {k: (np.array([v["point"] for v in p]), np.array([v["s"] for v in p])) for k, p in g["lines"].items()}
shots = {s["id"]: s for s in g["shots"]}
seq_poses = json.loads((SRC / "evidence" / "sequence" / "runtime-poses.json").read_text("utf-8"))
for s in seq_poses["shots"]:
    p0 = [p for p in s["poses"] if p["offset"] == 0][0]
    shots[s["id"]] = {"id": s["id"], "position": p0["position"], "quaternion": p0["quaternion"], "station": p0["station"]}


def cam(shot):
    q = Rotation.from_quat(shot["quaternion"])
    fw, up, right = q.apply([0, 0, 1]), q.apply([0, 1, 0]), q.apply([-1, 0, 0])
    pos = np.array(shot["position"]) + up * prof["upMeters"] + fw * prof["forwardMeters"]
    y, p, r = np.radians([prof["yawDeg"], prof["pitchDeg"], prof["rollDeg"]])
    Rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    Ry = np.array([[np.cos(y), 0, -np.sin(y)], [0, 1, 0], [np.sin(y), 0, np.cos(y)]])
    # horizontal car frame for X/Z reporting
    wup = np.array([0.0, 1.0, 0.0])
    fh = fw - fw.dot(wup) * wup
    fh /= np.linalg.norm(fh)
    rh = np.cross(fh, wup)  # three.js: x right? verify sign with right vector
    if rh.dot(right) < 0:
        rh = -rh
    return pos, Rz @ Rx @ Ry @ np.stack([right, -up, fw]), rh, fh


def analyse(sid, name, picks):
    shot = shots[sid]
    pos, b, rh, fh = cam(shot)
    pts, s = LINES[name]
    d = (s - shot["station"] + L / 2) % L - L / 2
    pts = pts[(d > -5) & (d < 180)]
    pc = (b @ (pts - pos).T).T
    valid = (pc[:-1, 2] > 2) & (pc[1:, 2] > 2) & (pc[:-1, 2] < 180) & (pc[1:, 2] < 180)
    xy = pc[:, :2] / np.maximum(pc[:, 2, None], 1e-5) * prof["f"] + [640, 360]
    idx = np.where(valid)[0]
    out = []
    for pt in picks:
        pt = np.asarray(pt, float)
        aa, bb = xy[idx], xy[idx + 1]
        v = bb - aa
        t = np.clip(np.sum((pt - aa) * v, 1) / np.maximum(np.sum(v * v, 1), 1e-12), 0, 1)
        dist = np.linalg.norm(aa + t[:, None] * v - pt, axis=1)
        k = int(np.argmin(dist))
        i = idx[k]
        # perspective-correct 3D point: interpolate in camera space using image-space t (approx; fine for leverage)
        P = pts[i] + (pts[i + 1] - pts[i]) * t[k]
        rel = P - pos
        out.append({"X": float(rel.dot(rh)), "Z": float(rel.dot(fh)), "residualPx": float(dist[k]), "row": float(pt[1])})
    return out


rows = []
tr = json.loads((SRC / "train-annotations.json").read_text("utf-8"))
for fr in tr["frames"]:
    for name, picks in fr["lines"].items():
        for r in analyse(fr["id"], name, picks):
            rows.append({"set": "train", "scene": fr["id"], "line": name, **r})
held = json.loads((SRC / "evidence" / "heldout-boundary-picks.json").read_text("utf-8"))
for s in held["shots"]:
    for a in s["annotations"]:
        name = "paintL" if a["side"] == "left" else "paintR"
        for r in analyse(s["id"], name, a["points"]):
            rows.append({"set": "heldout", "scene": s["id"], "line": name, **r})
seq = json.loads((SRC / "evidence" / "sequence" / "sequence-annotations.json").read_text("utf-8"))
for s in seq["shots"]:
    for a in s.get("annotations", []):
        name = a.get("name") or ("paintL" if a.get("side") == "left" else "paintR")
        for r in analyse(s["id"], name, a["points"]):
            rows.append({"set": "sequence", "scene": s["id"], "line": name, **r})

absX = np.array([abs(r["X"]) for r in rows])
Z = np.array([r["Z"] for r in rows])
summary = {
    "points": len(rows),
    "bySet": {k: sum(1 for r in rows if r["set"] == k) for k in ["train", "heldout", "sequence"]},
    "absX_percentiles_m": dict(zip(["p5", "p25", "p50", "p75", "p95"], np.percentile(absX, [5, 25, 50, 75, 95]).round(2).tolist())),
    "Z_percentiles_m": dict(zip(["p5", "p25", "p50", "p75", "p95"], np.percentile(Z, [5, 25, 50, 75, 95]).round(1).tolist())),
    "share_nearEdge_absX_le_3m_and_Z_le_20m": float(np.mean((absX <= 3) & (Z <= 20))),
    "share_absX_le_3m": float(np.mean(absX <= 3)),
    "share_Z_le_15m": float(np.mean(Z <= 15)),
    "note": "Depth/lateral of the matched CG point under the frozen Codex camera (f 472, h 1.25 assumed). Depth scales roughly with f, so with f 711 depths would be ~40% larger.",
}
(HERE / "pick_leverage.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n", "utf-8")
print(json.dumps(summary, indent=1))
