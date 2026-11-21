"""R2-vision: is the Codex 2026-10-03 focal length identifiable, and does the manufacturer FOV fit too?

Question
  Codex fitted f = 472.3 px (HFOV 107 deg at 1280x720) with camera height fixed at 1.25 m on 3 wet
  training frames (30 road-facing boundary picks, all 3-85 px below the horizon). The AiM technical
  sheets for SmartyCam HD / GP HD Rev.2.1 list "Field of view 67 deg or 84 deg" and the GP HD sheet
  names the lenses "Bullet cam HFOV 67 deg / 84 deg lens". A rectilinear 84 deg HFOV is f = 711 px,
  67 deg is f = 967 px. This script refits the SAME training picks with f fixed to those values (and a
  few lens-model variants) and scores the SAME 10 preregistered held-out scenes with the SAME metric as
  evidence/independent-camera-evaluation.py. It also shows how much a flat-ground lateral reading
  changes between camera hypotheses that fit equally well.

Inputs (repo-relative, read-only; no video file or frame image is opened):
  artifacts/video-cg-alignment-2026-10-03/runtime-geometry.json
  artifacts/video-cg-alignment-2026-10-03/camera-profile.json
  artifacts/video-cg-alignment-2026-10-03/train-annotations.json
  artifacts/video-cg-alignment-2026-10-03/evidence/heldout-boundary-picks.json
Outputs (numbers only):
  fov_hypotheses.json next to this script.
Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
Diagnostic only: nothing here is adopted as a camera, GPS, or course value.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
W, H, CX, CY = 1280, 720, 640.0, 360.0


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


g = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
profile = json.loads((SRC / "camera-profile.json").read_text("utf-8"))
train = json.loads((SRC / "train-annotations.json").read_text("utf-8"))
held = json.loads((SRC / "evidence" / "heldout-boundary-picks.json").read_text("utf-8"))
shots = {s["id"]: s for s in g["shots"]}
L = g["trackLength"]
LINES = {k: (np.array([v["point"] for v in pts]), np.array([v["s"] for v in pts])) for k, pts in g["lines"].items()}


def window_pts(name: str, station: float) -> np.ndarray:
    pts, s = LINES[name]
    d = (s - station + L / 2) % L - L / 2
    return pts[(d > -5) & (d < 180)]


def camera_basis(shot: dict, pitch: float, yaw: float, roll: float, up_m: float, fwd_m: float):
    q = Rotation.from_quat(shot["quaternion"])
    fw, up, right = q.apply([0, 0, 1]), q.apply([0, 1, 0]), q.apply([-1, 0, 0])
    pos = np.array(shot["position"]) + up * up_m + fw * fwd_m
    y, p, r = np.radians([yaw, pitch, roll])
    Rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    Ry = np.array([[np.cos(y), 0, -np.sin(y)], [0, 1, 0], [np.sin(y), 0, np.cos(y)]])
    return pos, Rz @ Rx @ Ry @ np.stack([right, -up, fw])


def project(pc: np.ndarray, cam: dict) -> np.ndarray:
    """camera right/down/forward coords -> pixels for the lens model in cam."""
    z = np.maximum(pc[:, 2], 1e-5)
    x, y = pc[:, 0] / z, pc[:, 1] / z
    model = cam.get("model", "pinhole")
    if model == "pinhole":
        k1 = cam.get("k1", 0.0)
        r2 = x * x + y * y
        s = 1 + k1 * r2
        return np.stack([x * s, y * s], 1) * cam["f"] + [CX, CY]
    if model == "equidistant":  # r_d = f * theta (fisheye)
        r = np.sqrt(x * x + y * y)
        th = np.arctan(r)
        s = np.where(r > 1e-9, th / np.maximum(r, 1e-9), 1.0)
        return np.stack([x * s, y * s], 1) * cam["f"] + [CX, CY]
    raise ValueError(model)


def residuals_for(shot_id: str, lines: dict, cam: dict) -> list[float]:
    shot = shots[shot_id]
    pos, b = camera_basis(shot, cam["pitch"], cam["yaw"], cam["roll"], cam["up"], cam["fwd"])
    out = []
    for name, picks in lines.items():
        pts = window_pts(name, shot["station"])
        pc = (b @ (pts - pos).T).T
        valid = (pc[:-1, 2] > 2) & (pc[1:, 2] > 2) & (pc[:-1, 2] < 180) & (pc[1:, 2] < 180)
        xy = project(pc, cam)
        aa, bb = xy[:-1][valid], xy[1:][valid]
        v = bb - aa
        for pt in picks:
            pt = np.asarray(pt, float)
            t = np.clip(np.sum((pt - aa) * v, 1) / np.maximum(np.sum(v * v, 1), 1e-12), 0, 1)
            out.append(float(np.min(np.linalg.norm(aa + t[:, None] * v - pt, axis=1))))
    return out


TRAIN = {fr["id"]: fr["lines"] for fr in train["frames"]}
HELD = {
    s["id"]: {("paintL" if a["side"] == "left" else "paintR"): a["points"] for a in s["annotations"]}
    for s in held["shots"]
}
# merge if a scene has two annotations on the same side
for s in held["shots"]:
    acc: dict = {}
    for a in s["annotations"]:
        acc.setdefault("paintL" if a["side"] == "left" else "paintR", []).extend(a["points"])
    HELD[s["id"]] = acc


def score(cam: dict, scenes: dict) -> dict:
    rows = {}
    for sid, lines in scenes.items():
        r = residuals_for(sid, lines, cam)
        rows[sid] = {"medianPx": float(np.median(r)), "p95Px": float(np.percentile(r, 95)), "points": len(r)}
    med = [v["medianPx"] for v in rows.values()]
    return {
        "medianOfSceneMediansPx": float(np.median(med)),
        "meanOfSceneMediansPx": float(np.mean(med)),
        "maxSceneMedianPx": float(np.max(med)),
        "scenes": rows,
    }


def fit(base: dict, free: list[str], x0: list[float], bounds) -> dict:
    def fun(x):
        cam = dict(base)
        cam.update(dict(zip(free, x)))
        res = []
        for sid, lines in TRAIN.items():
            res += residuals_for(sid, lines, cam)
        return np.array(res)

    sol = least_squares(fun, x0, bounds=bounds, loss="soft_l1", f_scale=5.0, x_scale="jac")
    cam = dict(base)
    cam.update(dict(zip(free, [float(v) for v in sol.x])))
    r = fun(sol.x)
    J = sol.jac
    try:
        cov = np.linalg.pinv(J.T @ J) * (np.median(np.abs(r)) / 0.6745) ** 2
        sd = {k: float(math.sqrt(max(cov[i, i], 0))) for i, k in enumerate(free)}
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)) + 1e-30)
        corr_d = {f"{free[i]}~{free[j]}": float(corr[i, j]) for i in range(len(free)) for j in range(i + 1, len(free))}
    except Exception:  # pragma: no cover
        sd, corr_d = {}, {}
    return {"camera": cam, "trainMedianPx": float(np.median(r)), "trainP95Px": float(np.percentile(r, 95)),
            "trainPoints": int(len(r)), "linearisedSd": sd, "paramCorrelation": corr_d, "cost": float(sol.cost)}


BASE = {"model": "pinhole", "f": profile["f"], "pitch": profile["pitchDeg"], "yaw": profile["yawDeg"],
        "roll": profile["rollDeg"], "up": profile["upMeters"], "fwd": profile["forwardMeters"], "k1": 0.0}
f84 = CX / math.tan(math.radians(42))
f67 = CX / math.tan(math.radians(33.5))
f84_eq = CX / math.radians(42)  # equidistant fisheye with 84 deg HFOV
ANG = ([-45, -20, -20], [45, 20, 20])

hyps = []
# 0 frozen Codex profile (reproduction of the published numbers)
hyps.append(("codex-frozen", "Codex profile as published (f 472.3, pitch 18.21, h 1.25)", None))
# 1 refit f free (reproduce)
hyps.append(("refit-f-free", "f, pitch, yaw, roll free; h 1.25 (reproduces Codex fit)",
             (dict(BASE), ["f", "pitch", "yaw", "roll"], [500, 15, 0, 0], ([250] + ANG[0][:1] + ANG[0][1:], [1500] + ANG[1][:1] + ANG[1][1:]))))
hyps.append(("pinhole-f711-hfov84", "f fixed 711 px (rectilinear HFOV 84 deg, AiM 84 deg lens); pitch/yaw/roll free; h 1.25",
             (dict(BASE, f=f84), ["pitch", "yaw", "roll"], [12, 0, 0], ANG)))
hyps.append(("pinhole-f967-hfov67", "f fixed 967 px (rectilinear HFOV 67 deg, AiM 67 deg lens); pitch/yaw/roll free; h 1.25",
             (dict(BASE, f=f67), ["pitch", "yaw", "roll"], [9, 0, 0], ANG)))
hyps.append(("pinhole-f711-k1", "f fixed 711 px + radial k1 free; pitch/yaw/roll free; h 1.25",
             (dict(BASE, f=f84), ["pitch", "yaw", "roll", "k1"], [12, 0, 0, 0], (ANG[0] + [-0.6], ANG[1] + [0.6]))))
hyps.append(("equidistant-f873-hfov84", "equidistant fisheye, f 873 px (HFOV 84 deg); pitch/yaw/roll free; h 1.25",
             (dict(BASE, model="equidistant", f=f84_eq), ["pitch", "yaw", "roll"], [12, 0, 0], ANG)))
hyps.append(("pinhole-f711-h1.0", "f fixed 711, h 1.0 m; pitch/yaw/roll free",
             (dict(BASE, f=f84, up=1.0), ["pitch", "yaw", "roll"], [12, 0, 0], ANG)))
hyps.append(("pinhole-f711-h1.5", "f fixed 711, h 1.5 m; pitch/yaw/roll free",
             (dict(BASE, f=f84, up=1.5), ["pitch", "yaw", "roll"], [12, 0, 0], ANG)))

results = []
cams = {}
for hid, label, spec in hyps:
    if spec is None:
        cam = dict(BASE)
        tr = score(cam, TRAIN)
        entry = {"id": hid, "label": label, "camera": cam,
                 "trainMedianPx": float(np.median([v for sid, l in TRAIN.items() for v in residuals_for(sid, l, cam)]))}
    else:
        base, free, x0, bnds = spec
        entry = {"id": hid, "label": label, **fit(base, free, x0, bnds)}
        cam = entry["camera"]
    ho = score(cam, HELD)
    entry["heldout"] = ho
    hfov = None
    if cam.get("model", "pinhole") == "pinhole" and abs(cam.get("k1", 0)) < 1e-12:
        hfov = 2 * math.degrees(math.atan(CX / cam["f"]))
    entry["hfovDegIfRectilinear"] = hfov
    # horizon row (level ground, camera axes only): v_h = cy - f*tan(pitch) for pinhole
    entry["horizonRowPinholeApprox"] = CY - cam["f"] * math.tan(math.radians(cam["pitch"])) if cam.get("model", "pinhole") == "pinhole" else None
    results.append(entry)
    cams[hid] = cam
    print(f"{hid:28s} train med {entry['trainMedianPx']:6.2f}px | held-out med-of-med {ho['medianOfSceneMediansPx']:6.2f} "
          f"mean {ho['meanOfSceneMediansPx']:6.2f} max {ho['maxSceneMedianPx']:6.2f} | f {cam['f']:.1f} pitch {cam['pitch']:.2f} "
          f"yaw {cam['yaw']:.2f} roll {cam['roll']:.2f} k1 {cam.get('k1', 0):.3f}")


# Flat-ground lateral reading for the same pixel under two equally-fitting hypotheses.
def ground_from_pixel(u: float, v: float, cam: dict) -> tuple[float, float] | None:
    """Level camera frame; ground plane y = +h (down). Returns (lateral X right, forward Z) metres."""
    xn, yn = (u - CX) / cam["f"], (v - CY) / cam["f"]
    if cam.get("model", "pinhole") == "equidistant":
        rd = math.hypot(xn, yn)
        if rd > 1e-9:
            s = math.tan(rd) / rd
            xn, yn = xn * s, yn * s
    elif abs(cam.get("k1", 0)) > 0:
        # invert x_d = x (1 + k1 r^2) by fixed-point iteration
        xd, yd = xn, yn
        for _ in range(30):
            r2 = xn * xn + yn * yn
            xn, yn = xd / (1 + cam["k1"] * r2), yd / (1 + cam["k1"] * r2)
    p, r = math.radians(cam["pitch"]), math.radians(cam["roll"])
    # undo roll then pitch (camera -> level): d_level = Rx(p)^T Rz(r)^T d_cam  (yaw ignored: lateral relative to view axis)
    d = np.array([xn, yn, 1.0])
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    dl = Rx.T @ Rz.T @ d
    if dl[1] <= 1e-6:
        return None
    t = cam["up"] / dl[1]
    return float(dl[0] * t), float(dl[2] * t)


grid = []
A, B = cams["refit-f-free"], cams["pinhole-f711-hfov84"]
for v in [230, 250, 270, 300, 330]:
    for u in [200, 400, 640, 880, 1080]:
        ga, gb = ground_from_pixel(u, v, A), ground_from_pixel(u, v, B)
        if ga and gb:
            grid.append({"u": u, "v": v, "f472": {"X": round(ga[0], 3), "Z": round(ga[1], 2)},
                         "f711": {"X": round(gb[0], 3), "Z": round(gb[1], 2)},
                         "dX": round(gb[0] - ga[0], 3), "dZ": round(gb[1] - ga[1], 2)})

out = {
    "purpose": "Diagnostic only. Tests whether the 2026-10-03 focal length is identified by the 30 training picks and whether the AiM-specified 84/67 deg HFOV lenses fit the same picks; scores the same 10 preregistered held-out scenes with the same metric. Nothing adopted.",
    "inputsSha256": {p: sha(SRC / p) for p in ["runtime-geometry.json", "camera-profile.json", "train-annotations.json", "evidence/heldout-boundary-picks.json"]},
    "manufacturerSpec": {
        "source": ["https://aim-sportline.com/en/out-of-production/smartycam-hd-rev2.1/technical-specifications.htm",
                   "https://store.aimsports.com/download/technical-sheets/oldies/aim_smartycam_gp_hd_rev21_100.pdf"],
        "text": "Video H.264 1280x720 @ 30 fps; Field of view 67 deg or 84 deg; GP HD accessory list names 'Bullet cam HFOV 67 deg / 84 deg lens'",
        "rectilinearEquivalentF": {"hfov84": f84, "hfov67": f67}, "equidistantF_hfov84": f84_eq,
        "caveat": "Which camera/lens was fitted to car #102 in July 2020 is not recorded in the repo; the spec HFOV may be nominal and its definition (with or without distortion) is not stated for the HD body.",
    },
    "hypotheses": results,
    "flatGroundReadingGrid": {
        "meaning": "Same pixel back-projected to level ground (h fixed) under the f-free refit (f~472) and the f=711 refit. dX = lateral reading difference, dZ = depth difference. Shows which quantity the focal-length ambiguity contaminates.",
        "rows": grid,
    },
}
(HERE / "fov_hypotheses.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", "utf-8")
print("wrote", HERE / "fov_hypotheses.json")
