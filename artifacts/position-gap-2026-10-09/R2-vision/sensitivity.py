"""R2-vision: theoretical lateral-measurement error budget for a road-edge seen by the SmartyCam.

Model: flat ground, camera height h above it, pitched down by p, roll 0, yaw 0; a straight road edge
parallel to the car at lateral offset X (camera frame, metres, + right). For points of that edge at
forward distance Z we compute how much the flat-ground lateral reading X_hat moves when
  * the pixel moves 1 px along the image normal of the edge line (random picking / detection noise),
  * camera height is wrong by +5 cm, pitch (horizon) by +0.2 deg, roll by +0.5 deg, yaw by +0.2 deg,
  * an unmodelled radial lens displacement of 5 px exists at that pixel.
Two cameras that both fit the 2026-10-03 training picks are used: f 472 / pitch 18.3 (Codex) and
f 711 / pitch 12.6 (AiM 84 deg HFOV refit, see fov_hypotheses.json).
Also: per-frame joint least squares of (lateral d, yaw psi) from one edge sampled every metre over
the visible range, and of (d, psi, pitch) from two edges, giving the 1-sigma lateral precision for
1/2/4 px point noise. Visibility: 0<=u<=1280 and v<=300 (all 223 existing manual picks lie in rows
196-298; the dash/overlay hides lower rows - an observation, not a measured mask).
Output: sensitivity.json next to this script. Pure geometry, no data files read.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CX, CY, VMAX = 640.0, 360.0, 300.0


def R(p_deg: float, r_deg: float = 0.0, y_deg: float = 0.0) -> np.ndarray:
    """level (right, down, forward) -> camera (right, down, forward)."""
    p, r, y = map(math.radians, (p_deg, r_deg, y_deg))
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Ry = np.array([[math.cos(y), 0, -math.sin(y)], [0, 1, 0], [math.sin(y), 0, math.cos(y)]])
    return Rz @ Rx @ Ry


def proj(X: float, Z: float, cam: dict) -> np.ndarray:
    pc = R(cam["p"], cam.get("r", 0), cam.get("y", 0)) @ np.array([X, cam["h"], Z])
    return np.array([pc[0] / pc[2] * cam["f"] + CX, pc[1] / pc[2] * cam["f"] + CY])


def back(u: float, v: float, cam: dict) -> tuple[float, float]:
    d = R(cam["p"], cam.get("r", 0), cam.get("y", 0)).T @ np.array([(u - CX) / cam["f"], (v - CY) / cam["f"], 1.0])
    t = cam["h"] / d[1]
    return d[0] * t, d[2] * t


def visible(uv: np.ndarray, cam: dict) -> bool:
    vh = CY - cam["f"] * math.tan(math.radians(cam["p"]))
    return 0 <= uv[0] <= 1280 and vh + 1 < uv[1] <= VMAX


def point_budget(X: float, Z: float, cam: dict) -> dict | None:
    uv = proj(X, Z, cam)
    if not visible(uv, cam):
        return {"visible": False, "u": round(float(uv[0]), 1), "v": round(float(uv[1]), 1)}
    a, b = proj(X, Z - 0.5, cam), proj(X, Z + 0.5, cam)
    t = (b - a) / np.linalg.norm(b - a)
    n = np.array([-t[1], t[0]])
    X0, _ = back(*uv, cam)
    dXn = back(*(uv + n), cam)[0] - X0
    radial = uv - [CX, CY]
    rr = np.linalg.norm(radial)
    out = {
        "visible": True, "u": round(float(uv[0]), 1), "v": round(float(uv[1]), 1),
        "m_per_px_normal": abs(dXn),
        "naive_m_per_px_Z_over_f": Z / cam["f"],
        "dh+5cm": back(*uv, dict(cam, h=cam["h"] + 0.05))[0] - X0,
        "dpitch+0.2deg": back(*uv, dict(cam, p=cam["p"] + 0.2))[0] - X0,
        "droll+0.5deg": back(*uv, dict(cam, r=0.5))[0] - X0,
        "dyaw+0.2deg": back(*uv, dict(cam, y=0.2))[0] - X0,
        "dist_radial5px": (back(*(uv + 5 * radial / rr), cam)[0] - X0) if rr > 1 else 0.0,
    }
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def joint_precision(edges: list[float], cam: dict, sigma: float, params: tuple[str, ...]) -> dict:
    """1-sigma of lateral d from all visible edge points (every 1 m, Z 3..60) with unknowns params."""
    rows, used = [], 0
    for X in edges:
        for Z in np.arange(3.0, 60.01, 1.0):
            uv = proj(X, Z, cam)
            if not visible(uv, cam):
                continue
            a, b = proj(X, Z - 0.5, cam), proj(X, Z + 0.5, cam)
            t = (b - a) / np.linalg.norm(b - a)
            n = np.array([-t[1], t[0]])
            J = []
            for prm in params:
                eps = {"d": 0.01, "psi": 0.01, "pitch": 0.01, "roll": 0.01}[prm]
                if prm == "d":
                    uv2 = proj(X - eps, Z, cam)  # camera moves right by eps => edge moves left in camera frame
                elif prm == "psi":
                    uv2 = proj(X, Z, dict(cam, y=eps))
                elif prm == "pitch":
                    uv2 = proj(X, Z, dict(cam, p=cam["p"] + eps))
                else:
                    uv2 = proj(X, Z, dict(cam, r=eps))
                J.append(float(np.dot(uv2 - uv, n)) / eps)
            rows.append(J)
            used += 1
    if used < len(params) + 1:
        return {"points": used, "sigma_d_m": None}
    A = np.array(rows)
    cov = np.linalg.inv(A.T @ A) * sigma ** 2
    return {"points": used, "sigma_d_m": round(float(math.sqrt(cov[0, 0])), 4),
            "sigma_psi_deg": round(float(math.sqrt(cov[1, 1])), 4) if "psi" in params else None}


cams = {"codex_f472_p18.3": {"f": 473.2, "p": 18.33, "h": 1.25},
        "spec84_f711_p12.6": {"f": 710.8, "p": 12.60, "h": 1.25}}
out = {"purpose": __doc__.strip().splitlines()[0], "assumptions": {
    "flatGround": True, "visibleRowsMax": VMAX, "h": 1.25,
    "note": "Errors are first-order flat-ground lateral reading changes; signs follow +X = right."}, "cameras": {}}
for cid, cam in cams.items():
    tab = {}
    for X in [-1.5, 1.5, 3.0, 5.0, 8.0, 12.0]:
        for Z in [5, 7.5, 10, 15, 20, 30, 40]:
            tab[f"X{X:+.1f}_Z{Z}"] = point_budget(X, Z, cam)
    joint = {}
    for X in [1.0, 1.5, 3.0, 5.0, 8.0, 12.0]:
        for s in [1.0, 2.0, 4.0]:
            joint[f"oneEdge_X{X}_sigma{s}px_d+psi"] = joint_precision([X], cam, s, ("d", "psi"))
    for (XL, XR) in [(-1.5, 12.0), (-4.0, 9.5), (-7.0, 7.0)]:
        for s in [1.0, 2.0, 4.0]:
            joint[f"twoEdges_{XL}_{XR}_sigma{s}px_d+psi+pitch+roll"] = joint_precision([XL, XR], cam, s, ("d", "psi", "pitch", "roll"))
    out["cameras"][cid] = {"camera": cam, "pointBudget": tab, "jointPerFrame": joint}
(HERE / "sensitivity.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", "utf-8")

# compact console table
for cid, c in out["cameras"].items():
    print("==", cid)
    print(f"{'X':>5} {'Z':>5} {'v':>6} {'m/px':>6} {'h+5cm':>7} {'p+.2':>7} {'r+.5':>7} {'y+.2':>7} {'dist5':>7}")
    for k, v in c["pointBudget"].items():
        X, Z = k.split("_")
        if v and v.get("visible"):
            print(f"{X:>5} {Z:>5} {v['v']:6.1f} {v['m_per_px_normal']:6.3f} {v['dh+5cm']:7.3f} {v['dpitch+0.2deg']:7.3f} {v['droll+0.5deg']:7.3f} {v['dyaw+0.2deg']:7.3f} {v['dist_radial5px']:7.3f}")
    for k, v in c["jointPerFrame"].items():
        print(k, v)
