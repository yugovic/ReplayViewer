"""I3: lateral vehicle offset implied by the in-car video (frozen 2026-10-03 camera).

Inputs (all read-only, repo-relative; no video pixels are read):
  artifacts/video-cg-alignment-2026-10-03/runtime-geometry.json        exact runtime paint lines + production poses
  artifacts/video-cg-alignment-2026-10-03/camera-fit.json              frozen camera (h=1.25) + Codex height refits
  artifacts/video-cg-alignment-2026-10-03/train-annotations.json       3 wet training frames (camera fit only)
  artifacts/video-cg-alignment-2026-10-03/runtime-capture.json         sample times of training frames
  artifacts/video-cg-alignment-2026-10-03/evidence/heldout-boundary-picks.json   10 frozen held-out scenes
  artifacts/video-cg-alignment-2026-10-03/evidence/sequence/{runtime-poses,sequence-paint-lines}.json  8 diagnostic frames
  public/data/races/<race>/osaki_hmr_demio_101_lap_00N.json            logger accy/speed at the scene time

Model: the camera of camera-profile.json rigidly mounted on the production pose.  Per scene the whole
car (camera) is moved by dy metres along the drawn car's right axis (+ = car actually further RIGHT
than drawn), optionally rotated by dyaw about the car up axis (+ = actual heading rotated to the
right / clockwise from above), and in diagnostics rolled by droll (+ = camera right side up = body
rolled to the left, the sense expected in a right-hand corner) or moved by dx along the car forward
axis.  Score = point-to-projected-polyline pixel distance exactly as the 2026-10-03 evaluation.
Robust loss soft_l1, f_scale 4 px (recorded pick uncertainty).

Outputs: lateral-offset-results.json next to this file (I3_QUICK=1 -> .quick.json, small bootstrap).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
EV = SRC / "evidence"
QUICK = os.environ.get("I3_QUICK") == "1"

RNG = np.random.default_rng(20261009)
F_SCALE = 4.0
DY_GRID = np.round(np.arange(-5.0, 5.0 + 1e-9, 0.01), 4)
DY_GRID_COARSE = np.round(np.arange(-5.0, 5.0 + 1e-9, 0.05), 4)
DY_GRID2 = np.round(np.arange(-4.0, 4.0 + 1e-9, 0.05), 4)
YAW_GRID = np.round(np.arange(-2.0, 2.0 + 1e-9, 0.1), 4)
B_POINTS = 400 if QUICK else 4000
B_FREE = 200 if QUICK else 1000
B_ROLL = 20 if QUICK else 200
B_CAMERA = 20 if QUICK else 300
IMU_ROLL = None
if (HERE / "imu-roll-gradient.json").exists():
    IMU_ROLL = json.loads((HERE / "imu-roll-gradient.json").read_text(encoding="utf-8")).get("adoptedRollGradientDegPerG")
ROLL_GRADIENTS = [0.0] + ([round(IMU_ROLL, 3)] if IMU_ROLL else []) + [2.0, 4.0]  # deg per g; IMU-measured + assumed
ROLL_BOUND = 5.0
DX_BOUND = 5.0


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT.resolve()).as_posix()


G = json.loads((SRC / "runtime-geometry.json").read_text(encoding="utf-8"))
FIT = json.loads((SRC / "camera-fit.json").read_text(encoding="utf-8"))
TRAIN = json.loads((SRC / "train-annotations.json").read_text(encoding="utf-8"))["frames"]
CAPTURE = json.loads((SRC / "runtime-capture.json").read_text(encoding="utf-8"))
HELD = json.loads((EV / "heldout-boundary-picks.json").read_text(encoding="utf-8"))
SEQP = json.loads((EV / "sequence" / "runtime-poses.json").read_text(encoding="utf-8"))
SEQL = json.loads((EV / "sequence" / "sequence-paint-lines.json").read_text(encoding="utf-8"))
L = G["trackLength"]
FROZEN = np.array([FIT["profile"][k] for k in ["f", "pitchDeg", "yawDeg", "rollDeg"]])

SHOTS = {s["id"]: dict(s) for s in G["shots"]}
for row in SEQP["shots"]:
    pose = next(p for p in row["poses"] if p["offset"] == 0)
    SHOTS[row["id"]] = dict(pose, id=row["id"], raceId=row["raceId"], time=row["time"])

SAMPLE_T = {s["id"]: s["views"]["onboard"]["sampleTime"] for s in CAPTURE["shots"]}
RUNTIME_TELEMETRY = {s["id"]: s["views"]["onboard"]["telemetry"] for s in CAPTURE["shots"]}
for s in HELD["shots"]:
    SAMPLE_T[s["id"]] = s["afterPlaybackSeconds"]
for s in SEQP["shots"]:
    SAMPLE_T[s["id"]] = s["afterPlaybackSeconds"]

LAPS = {}


def lap_json(race, lap):
    key = (race, lap)
    if key not in LAPS:
        p = ROOT / "public" / "data" / "races" / race / f"osaki_hmr_demio_101_lap_{lap:03d}.json"
        LAPS[key] = (p, json.loads(p.read_text(encoding="utf-8")))
    return LAPS[key]


def telemetry(ident):
    shot = SHOTS[ident]
    lap = int(ident.split("-")[3][1:])
    _, d = lap_json(shot["raceId"], lap)
    t = SAMPLE_T[ident]
    tt = np.asarray(d["t"])
    return {k: float(np.interp(t, tt, np.asarray(d[k], float))) for k in ["speed", "accy", "accx", "steer", "dist"]} | {
        "sampleTime": float(t)}


ACCY = {i: telemetry(i)["accy"] for i in SHOTS}


def rot(yaw, pitch, roll):
    y, p, r = np.radians([yaw, pitch, roll])
    cy, sy, cp, sp, cr, sr = np.cos(y), np.sin(y), np.cos(p), np.sin(p), np.cos(r), np.sin(r)
    return (np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
            @ np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
            @ np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]]))


def car_axes(shot):
    q = Rotation.from_quat(shot["quaternion"])
    return q.apply([0, 0, 1]), q.apply([0, 1, 0]), q.apply([-1, 0, 0])  # fw, up, right


LOCAL = {}
for ident, shot in SHOTS.items():
    LOCAL[ident] = {}
    for name, line in G["lines"].items():
        rows = [r for r in line if -5 < ((r["s"] - shot["station"] + L / 2) % L - L / 2) < 180]
        LOCAL[ident][name] = np.array([r["point"] for r in rows], dtype=float)


def scene_rows():
    out = []
    for r in TRAIN:
        out.append(dict(id=r["id"], group="train", lines={k: np.array(v, float) for k, v in r["lines"].items()}))
    for s in HELD["shots"]:
        out.append(dict(id=s["id"], group="heldout",
                        lines={("paintL" if a["side"] == "left" else "paintR"): np.array(a["points"], float)
                               for a in s["annotations"]}))
    for s in SEQL:
        out.append(dict(id=s["id"], group="sequence", lines={k: np.array(v, float) for k, v in s["lines"].items()}))
    return out


ROWS = scene_rows()


# --------------------------------------------------------------------------- projection
def _arr(x, n):
    x = np.atleast_1d(np.asarray(x, float))
    return np.repeat(x, n) if x.size == 1 and n > 1 else x


def line_distances(ident, name, obs, cam, h, dy, dyaw=0.0, droll=0.0, dx=0.0, chunk=256):
    """Distances (K, Np) from observed points to the projected polyline for each parameter set k."""
    dy = np.atleast_1d(np.asarray(dy, float))
    K = max(dy.size, np.size(dyaw), np.size(droll), np.size(dx))
    dy, dyaw, droll, dx = _arr(dy, K), _arr(dyaw, K), _arr(droll, K), _arr(dx, K)
    f, pitch, yaw0, roll0 = cam[:4]
    k1 = cam[4] if len(cam) > 4 else 0.0
    shot = SHOTS[ident]
    fw, up, right = car_axes(shot)
    base = np.array(shot["position"]) + up * h + fw * (-0.55)
    Bm = np.stack([right, -up, fw])
    P = LOCAL[ident][name]
    out = np.empty((K, len(obs)))
    for k0 in range(0, K, chunk):
        sl = slice(k0, k0 + chunk)
        C = base[None] + dy[sl, None] * right[None] + dx[sl, None] * fw[None]
        R = np.stack([rot(yaw0 + a, pitch, roll0 + r) @ Bm for a, r in zip(dyaw[sl], droll[sl])])
        loc = np.einsum("kij,knj->kni", R, P[None, :, :] - C[:, None, :])
        z = loc[:, :, 2]
        nrm = loc[:, :, :2] / np.maximum(z[:, :, None], 1e-5)
        if k1:
            nrm = nrm * (1 + k1 * np.sum(nrm * nrm, axis=2, keepdims=True))
        uv = nrm * f + np.array([640.0, 360.0])
        ok = (z[:, :-1] > 2) & (z[:, 1:] > 2) & (z[:, :-1] < 180) & (z[:, 1:] < 180)
        a, b = uv[:, :-1], uv[:, 1:]
        v = b - a
        den = np.maximum(np.sum(v * v, axis=2), 1e-9)
        w = obs[None, :, None, :] - a[:, None, :, :]
        t = np.clip(np.sum(w * v[:, None], axis=3) / den[:, None, :], 0, 1)
        d = np.linalg.norm(w - t[..., None] * v[:, None], axis=3)
        d = np.where(ok[:, None, :], d, np.inf)
        out[sl] = d.min(axis=2)
    return out


def scene_distances(row, cam, h, dy, dyaw=0.0, droll=0.0, dx=0.0, names=None):
    names = names or list(row["lines"].keys())
    parts = [line_distances(row["id"], n, row["lines"][n], cam, h, dy, dyaw, droll, dx) for n in names]
    labels = sum(([n] * len(row["lines"][n]) for n in names), [])
    return np.concatenate(parts, axis=1), labels


def rho(d, fs=F_SCALE):
    return 2 * fs * fs * (np.sqrt(1 + (d / fs) ** 2) - 1)


# --------------------------------------------------------------------------- camera fit (Codex parity)
def train_residuals(cam, h, rows, pose_shift=0.0, roll_gradient=0.0):
    res = []
    for row in rows:
        ds = []
        for name, pts in row["lines"].items():
            ds.extend(line_distances(row["id"], name, pts, cam, h, [pose_shift], 0.0,
                                     roll_gradient * ACCY[row["id"]])[0])
        ds = np.asarray(ds)
        res.extend(ds / math.sqrt(len(ds)))
    return np.asarray(res)


TRAIN_ROWS = [r for r in ROWS if r["group"] == "train"]


def fit_camera(h, rows=None, pose_shift=0.0, roll_gradient=0.0, x0=(600, 14, 0, 0), tol=1e-10, nfev=700):
    rows = rows or TRAIN_ROWS
    return least_squares(train_residuals, list(x0), bounds=([430, 5, -8, -6], [850, 25, 8, 6]),
                         args=(h, rows, pose_shift, roll_gradient), loss="soft_l1", f_scale=2,
                         xtol=tol, ftol=tol, gtol=tol, max_nfev=nfev)


def fit_camera_k1(h):
    """5-parameter camera with one radial distortion term k1 (normalised coords), training frames only."""
    r = least_squares(train_residuals, [*FROZEN, 0.0], bounds=([430, 5, -8, -6, -0.5], [850, 25, 8, 6, 0.5]),
                      args=(h, TRAIN_ROWS), loss="soft_l1", f_scale=2, xtol=1e-10, ftol=1e-10, gtol=1e-10,
                      max_nfev=3000)
    sv = np.linalg.svd(r.jac, compute_uv=False)
    return r, sv


def raw_train_stats(cam, h, pose_shift=0.0, roll_gradient=0.0):
    raw = []
    for row in TRAIN_ROWS:
        for name, pts in row["lines"].items():
            raw.extend(line_distances(row["id"], name, pts, cam, h, [pose_shift], 0.0,
                                      roll_gradient * ACCY[row["id"]])[0])
    return float(np.median(raw)), float(np.percentile(raw, 95))


def identifiability_common_dy(h):
    def fun(x):
        return train_residuals(x[:4], h, TRAIN_ROWS, pose_shift=x[4])

    r = least_squares(fun, [600, 14, 0, 0, 0], bounds=([430, 5, -8, -6, -3], [850, 25, 8, 6, 3]),
                      loss="soft_l1", f_scale=2, xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=3000)
    sv = np.linalg.svd(r.jac, compute_uv=False)
    prof = []
    for b in [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2]:
        best = None
        for x0 in [FROZEN, r.x[:4], (600, 14, 0, 0)]:
            rr = fit_camera(h, pose_shift=b, x0=x0)
            if best is None or rr.cost < best.cost:
                best = rr
        med, p95 = raw_train_stats(best.x, h, pose_shift=b)
        prof.append({"commonTrainingDyMeters": b, "camera": best.x.tolist(), "cost": float(best.cost),
                     "trainMedianPx": med, "trainP95Px": p95, "activeBounds": best.active_mask.tolist()})
    frozen_cost = float(0.5 * np.sum(rho(train_residuals(FROZEN, h, TRAIN_ROWS), 2.0)))
    return {"x": r.x.tolist(), "cost": float(r.cost), "frozenCameraCost": frozen_cost,
            "activeBounds": r.active_mask.tolist(), "jacobianSingularValues": sv.tolist(),
            "conditionNumber": float(sv[0] / sv[-1]),
            "parameterOrder": ["f", "pitchDeg", "yawDeg", "rollDeg", "commonTrainingDyMeters"],
            "profileOverCommonTrainingDy": prof}


def identifiability_roll_gradient(h):
    def fun(x):
        return train_residuals(x[:4], h, TRAIN_ROWS, roll_gradient=x[4])

    r = least_squares(fun, [*FROZEN, 0.0], bounds=([430, 5, -8, -6, -10], [850, 25, 8, 6, 10]),
                      loss="soft_l1", f_scale=2, xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=3000)
    sv = np.linalg.svd(r.jac, compute_uv=False)
    return {"x": r.x.tolist(), "cost": float(r.cost), "activeBounds": r.active_mask.tolist(),
            "jacobianSingularValues": sv.tolist(), "conditionNumber": float(sv[0] / sv[-1]),
            "trainingAccyG": {row["id"]: ACCY[row["id"]] for row in TRAIN_ROWS},
            "parameterOrder": ["f", "pitchDeg", "yawDeg", "rollDeg", "rollGradientDegPerG"]}


# --------------------------------------------------------------------------- fitting helpers
def refine_1d(cost, grid):
    i = int(np.argmin(cost))
    if 0 < i < len(grid) - 1:
        c0, c1, c2 = cost[i - 1], cost[i], cost[i + 1]
        den = c0 - 2 * c1 + c2
        if den > 0:
            return float(grid[i] + 0.5 * (c0 - c2) / den * (grid[1] - grid[0]))
    return float(grid[i])


def grid_fit(D, grid, weights=None):
    R = rho(D)
    if weights is None:
        return refine_1d(R.sum(axis=1), grid)
    C = weights @ R.T
    return np.array([refine_1d(c, grid) for c in C])


def fit_free_grid(D2, weights=None):
    R = rho(D2)
    C = R.sum(axis=1)[None, :] if weights is None else weights @ R.T
    out = []
    for c in C:
        c = c.reshape(len(YAW_GRID), len(DY_GRID2))
        j, i = np.unravel_index(np.argmin(c), c.shape)
        out.append((refine_1d(c[j], DY_GRID2), refine_1d(c[:, i], YAW_GRID), bool(j in (0, len(YAW_GRID) - 1))))
    return out if weights is not None else out[0]


def continuous(row, cam, h, keys, starts, bounds, names=None, droll0=0.0, lines=None):
    """Robust continuous fit of a subset of (dy, dyaw, droll, dx); returns best x and distances."""
    rr = row if lines is None else dict(row, lines=lines)

    def fun(x):
        v = dict(zip(keys, x))
        return scene_distances(rr, cam, h, [v.get("dy", 0.0)], v.get("dyaw", 0.0),
                               droll0 + v.get("droll", 0.0), v.get("dx", 0.0), names)[0][0]

    best = None
    lo = [bounds[k][0] for k in keys]
    hi = [bounds[k][1] for k in keys]
    for x0 in starts:
        x0 = np.clip(np.asarray(x0, float), np.asarray(lo) + 1e-6, np.asarray(hi) - 1e-6)
        r = least_squares(fun, x0, bounds=(lo, hi), loss="soft_l1", f_scale=F_SCALE,
                          xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=400)
        if best is None or r.cost < best.cost:
            best = r
    return best.x, fun(best.x), best


BOUNDS = {"dy": (-5, 5), "dyaw": (-2, 2), "droll": (-ROLL_BOUND, ROLL_BOUND), "dx": (-DX_BOUND, DX_BOUND)}


def stratified_weights(labels, B):
    labels = np.asarray(labels)
    W = np.zeros((B, len(labels)))
    for name in np.unique(labels):
        idx = np.flatnonzero(labels == name)
        draws = RNG.integers(0, len(idx), size=(B, len(idx)))
        rows = np.repeat(np.arange(B), len(idx))
        np.add.at(W, (rows, idx[draws].ravel()), 1.0)
    return W


def resample_lines(row):
    return {n: pts[RNG.integers(0, len(pts), len(pts))] for n, pts in row["lines"].items()}


def ci(x, lo=2.5, hi=97.5):
    x = np.asarray(x, float)
    return [float(np.percentile(x, lo)), float(np.percentile(x, hi))]


def stats(d):
    return {"medianPx": float(np.median(d)), "p95Px": float(np.percentile(d, 95))}


# --------------------------------------------------------------------------- scene geometry context
def road_context(ident):
    shot = SHOTS[ident]
    fw, up, right = car_axes(shot)
    s0 = shot["station"]
    pts = {}
    for name, line in G["lines"].items():
        ss = np.array([r["s"] for r in line])
        P = np.array([r["point"] for r in line])
        pts[name] = [np.array([np.interp(s, ss, P[:, k]) for k in range(3)]) for s in (s0 - 2.5, s0, s0 + 2.5)]
    cl = [(pts["paintL"][i] + pts["paintR"][i]) / 2 for i in range(3)]
    tang = cl[2] - cl[0]
    tang[1] = 0
    tang /= np.linalg.norm(tang)
    fwh = fw.copy()
    fwh[1] = 0
    fwh /= np.linalg.norm(fwh)
    # signed angle (+ = car heading rotated to the right of the road direction, seen from above, +y up)
    ang = math.degrees(math.atan2(np.dot(np.cross(fwh, tang), [0, 1, 0]), np.dot(fwh, tang)))
    nrm = np.cross(tang, [0, 1, 0])  # road right
    nrm /= np.linalg.norm(nrm)
    car = np.array(shot["position"])
    dl = float(np.dot(car - pts["paintL"][1], nrm))  # >0: car right of left paint line
    dr = float(np.dot(pts["paintR"][1] - car, nrm))  # >0: car left of right paint line
    return {"carHeadingMinusRoadDeg": ang, "drawnCentreToLeftPaintM": dl, "drawnCentreToRightPaintM": dr,
            "roadWidthPaintToPaintM": dl + dr}


def scene_meta(row):
    shot = SHOTS[row["id"]]
    fw, up, right = car_axes(shot)
    parts = row["id"].split("-")
    session = "2020-07-29 wet" if shot["raceId"] == "fuji_aim_01" else "2020-07-30 dry"
    zones = [{"id": z["id"], "side": z["side"], "from": z["from"], "to": z["to"]} for z in G["zones"]
             if z["to"] >= shot["station"] - 5 and z["from"] <= shot["station"] + 60]
    tel = telemetry(row["id"])
    meta = {"id": row["id"], "group": row["group"], "raceId": shot["raceId"], "session": session,
            "lap": int(parts[3][1:]), "lapTimeSeconds": float(parts[4]), "stationMeters": float(shot["station"]),
            "telemetry": tel, "rightAxisEastNorth": [float(right[0]), float(-right[2])],
            "linesScored": {k: int(len(v)) for k, v in row["lines"].items()},
            "kerbZonesInView": zones, **road_context(row["id"])}
    if row["id"] in RUNTIME_TELEMETRY:
        meta["runtimeTelemetryAccy"] = RUNTIME_TELEMETRY[row["id"]]["accy"]
    return meta


# --------------------------------------------------------------------------- per-scene analysis
def per_scene(row, cam, h, full=True, droll0=0.0):
    D, labels = scene_distances(row, cam, h, DY_GRID, 0.0, droll0)
    base = D[np.argmin(np.abs(DY_GRID))]
    dy_grid = grid_fit(D, DY_GRID)
    x, d_fix, _ = continuous(row, cam, h, ["dy"], [[dy_grid]], BOUNDS, droll0=droll0)
    dy_fix = float(x[0])
    res = {"baseline": stats(base),
           "fixedYaw": {"dyMeters": dy_fix, **stats(d_fix), "atBound": bool(abs(dy_fix) > 4.98),
                        "dyMetersL2Loss": refine_1d((D ** 2).sum(axis=1), DY_GRID)}}
    per = {}
    for name in row["lines"]:
        m = np.array([l == name for l in labels])
        g = grid_fit(D[:, m], DY_GRID)
        xx, dd, _ = continuous(row, cam, h, ["dy"], [[g]], BOUNDS, names=[name], droll0=droll0)
        per[name] = {"dyMeters": float(xx[0]), "atBound": bool(abs(xx[0]) > 4.98), "points": int(m.sum()),
                     "baselineMedianPx": float(np.median(base[m])), "fitMedianPx": float(np.median(dd))}
    res["perBoundaryFixedYaw"] = per
    starts = [(dy_fix, 0.0), (dy_fix, -1.5), (dy_fix, 1.5), (0.0, -1.0), (0.0, 1.0)]
    D2 = None
    if full:
        yy, ddg = np.meshgrid(YAW_GRID, DY_GRID2, indexing="ij")
        D2, _ = scene_distances(row, cam, h, ddg.ravel(), yy.ravel(), droll0)
        g_dy, g_yaw, _ = fit_free_grid(D2)
        starts.append((g_dy, g_yaw))
    x, d_free, _ = continuous(row, cam, h, ["dy", "dyaw"], starts, BOUNDS, droll0=droll0)
    res["freeYaw"] = {"dyMeters": float(x[0]), "dyawDeg": float(x[1]), "yawAtBound": bool(abs(x[1]) > 1.995),
                      **stats(d_free)}
    if not full:
        return res
    # yaw-only explanation
    Dy, _ = scene_distances(row, cam, h, np.zeros_like(YAW_GRID), YAW_GRID)
    dyaw_only = refine_1d(rho(Dy).sum(axis=1), YAW_GRID)
    res["yawOnly"] = {"dyawDeg": dyaw_only, "atBound": bool(abs(dyaw_only) > 1.995),
                      **stats(scene_distances(row, cam, h, [0.0], dyaw_only)[0][0])}
    # diagnostics: unmodelled body roll, longitudinal (time/along-track) offset
    x, dd, _ = continuous(row, cam, h, ["dy", "droll"], [(dy_fix, 0), (dy_fix, 2), (dy_fix, -2), (0, 0)], BOUNDS)
    res["diagRollFree"] = {"dyMeters": float(x[0]), "drollDeg": float(x[1]),
                           "rollAtBound": bool(abs(x[1]) > ROLL_BOUND - 0.01), **stats(dd)}
    x, dd, _ = continuous(row, cam, h, ["dy", "dyaw", "droll"],
                          [(dy_fix, 0, 0), (res["freeYaw"]["dyMeters"], res["freeYaw"]["dyawDeg"], 0),
                           (dy_fix, 0, 2), (dy_fix, 0, -2)], BOUNDS)
    res["diagYawRollFree"] = {"dyMeters": float(x[0]), "dyawDeg": float(x[1]), "drollDeg": float(x[2]), **stats(dd)}
    x, dd, _ = continuous(row, cam, h, ["dy", "dx"], [(dy_fix, 0), (dy_fix, 2), (dy_fix, -2)], BOUNDS)
    res["diagForwardFree"] = {"dyMeters": float(x[0]), "dxMeters": float(x[1]),
                              "dxAtBound": bool(abs(x[1]) > DX_BOUND - 0.01), **stats(dd)}
    # bootstraps over picked points (stratified by boundary line)
    W = stratified_weights(labels, B_POINTS)
    bf = grid_fit(D, DY_GRID, W)
    res["fixedYaw"]["pointBootstrap"] = {"B": int(B_POINTS), "ci95": ci(bf), "sd": float(np.std(bf)),
                                         "gridStepMeters": 0.01, "fractionAtBound": float(np.mean(np.abs(bf) > 4.98))}
    pb = {}
    for name in row["lines"]:
        m = np.array([l == name for l in labels])
        pb[name] = grid_fit(D[:, m], DY_GRID, W[:, m])
        res["perBoundaryFixedYaw"][name]["ci95"] = ci(pb[name])
    if len(pb) == 2:
        diff = pb["paintR"] - pb["paintL"]
        res["twoSidedConsistency"] = {
            "dyRightMinusLeftMeters": per["paintR"]["dyMeters"] - per["paintL"]["dyMeters"],
            "ci95": ci(diff),
            "meaning": "a pure lateral car offset gives 0; >0 = video road looks NARROWER on both sides than CG"
                       " (both boundaries closer), <0 = wider. Non-zero also arises from unmodelled roll/scale."}
    W2 = W[:B_FREE]
    bfree = fit_free_grid(D2, W2)
    res["freeYaw"]["pointBootstrap"] = {
        "B": int(len(W2)), "dyCi95": ci([b[0] for b in bfree]), "dyawCi95": ci([b[1] for b in bfree]),
        "dySd": float(np.std([b[0] for b in bfree])), "grid": "dy 0.05 m x yaw 0.1 deg",
        "fractionYawAtBound": float(np.mean([b[2] for b in bfree]))}
    br = []
    for _ in range(B_ROLL):
        lines = resample_lines(row)
        x, _, _ = continuous(row, cam, h, ["dy", "droll"],
                             [(res["diagRollFree"]["dyMeters"], res["diagRollFree"]["drollDeg"])], BOUNDS,
                             lines=lines)
        br.append(x)
    br = np.array(br)
    res["diagRollFree"]["pointBootstrap"] = {"B": int(B_ROLL), "dyCi95": ci(br[:, 0]), "drollCi95": ci(br[:, 1])}
    return res


# --------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    rows = ROWS
    inputs = [SRC / "runtime-geometry.json", SRC / "camera-fit.json", SRC / "camera-profile.json",
              SRC / "train-annotations.json", SRC / "runtime-capture.json", EV / "heldout-boundary-picks.json",
              EV / "sequence" / "runtime-poses.json", EV / "sequence" / "sequence-paint-lines.json"]
    for row in rows:
        lap = int(row["id"].split("-")[3][1:])
        inputs.append(lap_json(SHOTS[row["id"]]["raceId"], lap)[0])
    report = {"track": "I3-video-lateral", "createdBy": rel(Path(__file__)), "quick": QUICK,
              "inputs": {rel(p): sha(p) for p in dict.fromkeys(inputs)},
              "conventions": {
                  "dyMeters": "+ = car (and rigidly mounted camera) actually further RIGHT than drawn, along the drawn car right axis",
                  "dyawDeg": "+ = actual heading rotated to the RIGHT (clockwise seen from above) relative to drawn heading; bounded +-2 deg",
                  "drollDeg": "+ = camera right side up (body rolled to the left, expected in a right-hand corner); diagnostic only, bounded +-5 deg",
                  "dxMeters": "+ = car actually further FORWARD than drawn; diagnostic only, bounded +-5 m",
                  "accyG": "logger lateral acceleration, + in right-hand corners (sign checked against T1/k13 and k9)",
                  "loss": f"soft_l1 on point-to-projected-polyline pixel distance, f_scale {F_SCALE} px (pick uncertainty)",
                  "pointEstimates": "continuous least_squares (multi-start) seeded by grid search; dy bounded +-5 m",
                  "bootstrapGrids": "fixed yaw dy 0.01 m; free yaw dy 0.05 m x yaw 0.1 deg (dy in +-4 m)",
                  "scope": "relative to the GPS/heading at the 3 wet training frames (camera absorbs their common error); not absolute GPS truth"}}

    # 1) camera parity and height refits (training frames only)
    cams = {}
    parity = []
    for s in FIT["sensitivity"]:
        h = s["heightMeters"]
        r = fit_camera(h)
        cams[h] = r.x
        parity.append({"heightMeters": h, "refit": r.x.tolist(), "codex": s["parameters"],
                       "maxAbsDiff": float(np.max(np.abs(r.x - np.array(s["parameters"])))),
                       "activeBounds": r.active_mask.tolist()})
    cams[1.25] = FROZEN
    report["cameraRefitParity"] = parity
    print("camera parity", [round(p["maxAbsDiff"], 6) for p in parity], flush=True)

    held = [r for r in rows if r["group"] == "heldout"]
    med = [float(np.median(scene_distances(r, FROZEN, 1.25, [0.0])[0][0])) for r in held]
    report["heldoutReproduction"] = {"medianOfSceneMediansPx": float(np.median(med)), "expectedPx": 9.4199,
                                     "perScene": dict(zip([r["id"] for r in held], med))}
    print("held-out median of medians", round(float(np.median(med)), 4), flush=True)

    # 2) per-scene analysis
    scenes = []
    for row in rows:
        meta = scene_meta(row)
        main_res = per_scene(row, FROZEN, 1.25, full=True)
        hs = {}
        for h in (1.0, 1.5):
            rr = per_scene(row, cams[h], h, full=False)
            hs[str(h)] = {"fixedYawDy": rr["fixedYaw"]["dyMeters"], "freeYawDy": rr["freeYaw"]["dyMeters"],
                          "freeYawDyaw": rr["freeYaw"]["dyawDeg"], "baselineMedianPx": rr["baseline"]["medianPx"],
                          "fixedYawMedianPx": rr["fixedYaw"]["medianPx"],
                          "perBoundaryDy": {k: v["dyMeters"] for k, v in rr["perBoundaryFixedYaw"].items()}}
        scenes.append({**meta, "h1.25": main_res, "heightSensitivity": hs})
        fy = main_res["fixedYaw"]
        print(row["id"], f"accy {meta['telemetry']['accy']:+.2f}", f"fix {fy['dyMeters']:+.2f} {np.round(fy['pointBootstrap']['ci95'], 2)}",
              f"free {main_res['freeYaw']['dyMeters']:+.2f}/{main_res['freeYaw']['dyawDeg']:+.2f}",
              {k: round(v["dyMeters"], 2) for k, v in main_res["perBoundaryFixedYaw"].items()},
              f"roll {main_res['diagRollFree']['dyMeters']:+.2f}/{main_res['diagRollFree']['drollDeg']:+.2f}",
              f"fwd {main_res['diagForwardFree']['dyMeters']:+.2f}/{main_res['diagForwardFree']['dxMeters']:+.2f}",
              f"h1.0 {hs['1.0']['fixedYawDy']:+.2f} h1.5 {hs['1.5']['fixedYawDy']:+.2f}",
              f"{time.time()-t0:.0f}s", flush=True)
    report["scenes"] = scenes

    # 3) circularity: inject a common lateral error b into the 3 training poses, refit camera, re-estimate
    inj = []
    for b in [-1.0, -0.5, 0.5, 1.0]:
        rcam = fit_camera(1.25, pose_shift=b, x0=FROZEN)
        per = {}
        for row in rows:
            if row["group"] == "train":
                continue
            D, _ = scene_distances(row, rcam.x, 1.25, DY_GRID)
            x, _, _ = continuous(row, rcam.x, 1.25, ["dy"], [[grid_fit(D, DY_GRID)]], BOUNDS)
            per[row["id"]] = float(x[0])
        inj.append({"commonTrainingShiftMeters": b, "camera": rcam.x.tolist(),
                    "trainMedianPx": raw_train_stats(rcam.x, 1.25, b)[0], "fixedYawDy": per})
    slope = {}
    for row in rows:
        if row["group"] == "train":
            continue
        xs = np.array([e["commonTrainingShiftMeters"] for e in inj] + [0.0])
        ys = np.array([e["fixedYawDy"][row["id"]] for e in inj] +
                      [next(s for s in scenes if s["id"] == row["id"])["h1.25"]["fixedYaw"]["dyMeters"]])
        slope[row["id"]] = float(np.polyfit(xs, ys, 1)[0])
    report["circularityInjection"] = {
        "meaning": "Training poses shifted right by b, camera refit on the same 3 frames, scene dy re-estimated. slope ~ +1: the scene offset is measured relative to the training frames' lateral error (true_dy ~ measured_dy + slope * training_error).",
        "runs": inj, "slopeDyPerTrainingShift": slope}
    print("injection slopes", {k: round(v, 2) for k, v in slope.items()}, flush=True)

    report["trainingCommonOffsetIdentifiability"] = identifiability_common_dy(1.25)
    print("5-param common dy", np.round(report["trainingCommonOffsetIdentifiability"]["x"], 3), flush=True)
    report["trainingRollGradientIdentifiability"] = identifiability_roll_gradient(1.25)
    print("5-param roll gradient", np.round(report["trainingRollGradientIdentifiability"]["x"], 3), flush=True)

    # 4) roll-gradient sensitivity (assumed k deg/g; camera refit on training for each k)
    rg = []
    for k in ROLL_GRADIENTS:
        rcam = fit_camera(1.25, roll_gradient=k, x0=FROZEN)
        per = {}
        for row in rows:
            droll0 = k * ACCY[row["id"]]
            rr = per_scene(row, rcam.x, 1.25, full=False, droll0=droll0)
            per[row["id"]] = {"fixedYawDy": rr["fixedYaw"]["dyMeters"], "baselineMedianPx": rr["baseline"]["medianPx"],
                              "fixedYawMedianPx": rr["fixedYaw"]["medianPx"],
                              "perBoundaryDy": {n: v["dyMeters"] for n, v in rr["perBoundaryFixedYaw"].items()}}
        hm = [per[r["id"]]["baselineMedianPx"] for r in held]
        rg.append({"rollGradientDegPerG": k, "camera": rcam.x.tolist(),
                   "trainMedianPx": raw_train_stats(rcam.x, 1.25, 0.0, k)[0],
                   "heldoutMedianOfSceneMediansPx": float(np.median(hm)),
                   "heldoutMeanOfSceneMediansPx": float(np.mean(hm)), "scenes": per})
        print("roll gradient", k, np.round(rcam.x, 3), "held-out median", round(float(np.median(hm)), 2), flush=True)
    report["rollGradientSensitivity"] = {
        "imuMeasuredGradientDegPerG": IMU_ROLL,
        "meaning": "Body roll = k * accy added to the camera roll (k = 0, the IMU-measured value from imu-roll-gradient.json, and assumed 2/4 deg/g). Camera refit on the 3 training frames for each k; held-out values are diagnostics chosen after seeing held-out residuals, not an acceptance test.",
        "runs": rg}

    # 4b) lens-distortion sensitivity: camera with k1 fit on the 3 training frames
    rk, svk = fit_camera_k1(1.25)
    perk = {}
    for row in rows:
        rr = per_scene(row, rk.x, 1.25, full=False)
        perk[row["id"]] = {"fixedYawDy": rr["fixedYaw"]["dyMeters"], "freeYawDy": rr["freeYaw"]["dyMeters"],
                           "freeYawDyaw": rr["freeYaw"]["dyawDeg"], "baselineMedianPx": rr["baseline"]["medianPx"],
                           "fixedYawMedianPx": rr["fixedYaw"]["medianPx"],
                           "perBoundaryDy": {n: v["dyMeters"] for n, v in rr["perBoundaryFixedYaw"].items()}}
    hmk = [perk[r["id"]]["baselineMedianPx"] for r in held]
    report["lensDistortionSensitivity"] = {
        "meaning": "Pinhole + one radial term k1 (x_d = x(1 + k1 r^2), normalised coords), 5 globals fit on the 3 training frames only. Diagnostic of the camera-model assumption; not adopted.",
        "camera": rk.x.tolist(), "activeBounds": rk.active_mask.tolist(), "jacobianSingularValues": svk.tolist(),
        "trainMedianPx": float(np.median(np.concatenate([
            line_distances(r["id"], n, p, rk.x, 1.25, [0.0])[0] for r in TRAIN_ROWS for n, p in r["lines"].items()]))),
        "heldoutMedianOfSceneMediansPx": float(np.median(hmk)), "heldoutMeanOfSceneMediansPx": float(np.mean(hmk)),
        "scenes": perk}
    print("k1 camera", np.round(rk.x, 4), "held-out median", round(float(np.median(hmk)), 2), flush=True)

    # 5) hierarchical bootstrap: training picks -> camera refit -> scene picks -> fixed-yaw dy
    others = [r for r in rows if r["group"] != "train"]
    boot = {r["id"]: [] for r in others}
    cams_b = []
    for b in range(B_CAMERA):
        rs = [dict(r, lines=resample_lines(r)) for r in TRAIN_ROWS]
        rc = least_squares(train_residuals, FROZEN, bounds=([430, 5, -8, -6], [850, 25, 8, 6]),
                           args=(1.25, rs), loss="soft_l1", f_scale=2, xtol=1e-8, ftol=1e-8, gtol=1e-8,
                           max_nfev=300)
        cams_b.append(rc.x.tolist())
        for r in others:
            D, labels = scene_distances(r, rc.x, 1.25, DY_GRID_COARSE)
            W = stratified_weights(labels, 1)
            boot[r["id"]].append(refine_1d((W @ rho(D).T)[0], DY_GRID_COARSE))
        if b % 25 == 0:
            print("camera bootstrap", b, f"{time.time()-t0:.0f}s", flush=True)
    cams_b = np.array(cams_b)
    report["hierarchicalBootstrap"] = {
        "B": int(B_CAMERA),
        "meaning": "Resample training picks (per line) -> refit 4 camera globals -> resample scene picks -> fixed-yaw dy (0.05 m grid + parabolic refine). Captures pick noise in camera fit and scene; does NOT capture the common training-frame GPS/heading error or the camera lateral mount (see circularityInjection).",
        "cameraParameterSd": cams_b.std(axis=0).tolist(), "cameraParameterOrder": ["f", "pitchDeg", "yawDeg", "rollDeg"],
        "fixedYawDyCi95": {k2: ci(v) for k2, v in boot.items()},
        "fixedYawDySd": {k2: float(np.std(v)) for k2, v in boot.items()}}

    report["runtimeSeconds"] = round(time.time() - t0, 1)
    out = HERE / ("lateral-offset-results.quick.json" if QUICK else "lateral-offset-results.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", rel(out), report["runtimeSeconds"], "s")


if __name__ == "__main__":
    main()
