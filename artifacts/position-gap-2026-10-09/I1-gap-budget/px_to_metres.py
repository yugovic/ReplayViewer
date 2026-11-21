"""I1 gap budget, component 6: Codex held-out video-vs-CG boundary residuals converted to metres.

Reads (all repo-relative, nothing from the original videos is opened):
  artifacts/video-cg-alignment-2026-10-03/runtime-geometry.json   (3D road-facing paint lines + exact runtime poses)
  artifacts/video-cg-alignment-2026-10-03/camera-profile.json     (frozen estimated onboard camera)
  artifacts/video-cg-alignment-2026-10-03/heldout-evaluation.json (height-sensitivity camera variants)
  artifacts/video-cg-alignment-2026-10-03/evidence/heldout-boundary-picks.json      (10 preregistered held-out scenes)
  artifacts/video-cg-alignment-2026-10-03/evidence/sequence/sequence-annotations.json + runtime-poses.json
                                                                   (8 post-selection diagnostic scenes, not independent)

Method (per annotated boundary point):
  1. Re-project the CG paint line with the frozen camera exactly as evidence/independent-camera-evaluation.py does
     and find the nearest projected segment -> pixel residual (reproduces the published per-scene medians).
  2. Recover the 3D point Q on the CG line that corresponds to the nearest image point (perspective-correct).
  3. Back-project the video pick pixel as a camera ray and intersect it with the local road plane through Q
     (normal = car up axis from the runtime quaternion) -> ground point G.
  4. Lateral metres = in-plane distance from G to the 3D CG polyline (perpendicular to the boundary), signed along the
     boundary normal that points to the car's right (+ = video boundary lies right of the CG boundary).
     This accounts for the line orientation in the image (a receding road edge needs a much larger lateral shift than
     residual_px * depth / f to move by residual_px perpendicular to its own image line).
  5. Also reported: the naive conversion residual_px * depth / f, and the metre equivalent of the 4 px picking
     uncertainty (pick moved +-4 px along the image normal of the matched segment).
  6. Camera-height sensitivity: the same picks are converted with the 1.0 m and 1.5 m camera fits that Codex stored
     (heldout-evaluation.json heightSensitivity; those camera variants were fitted on the same 3 training scenes).

Outputs (numbers only, no imagery) next to this script:
  px_to_metres_points.json   per point
  px_to_metres_summary.json  per scene + pooled distributions
Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
SRC = ROOT / "artifacts" / "video-cg-alignment-2026-10-03"
EV = SRC / "evidence"
PICK_SIGMA_PX = 4.0


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mount_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    y, p, r = np.radians([yaw_deg, pitch_deg, roll_deg])
    Rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    Ry = np.array([[np.cos(y), 0, -np.sin(y)], [0, 1, 0], [np.sin(y), 0, np.cos(y)]])
    return Rz @ Rx @ Ry


def camera_from_pose(position, quaternion, cam):
    q = Rotation.from_quat(quaternion)
    fw = q.apply([0, 0, 1])
    up = q.apply([0, 1, 0])
    right = q.apply([-1, 0, 0])
    pos = np.array(position) + up * cam["up"] + fw * cam["forward"] + right * cam.get("right", 0.0)
    b = mount_matrix(cam["yaw"], cam["pitch"], cam["roll"]) @ np.stack([right, -up, fw])
    return pos, b, up, right, fw


def segment_window(line, station, track_len, lo, hi):
    pts = np.array([v["point"] for v in line if lo < (v["s"] - station + track_len / 2) % track_len - track_len / 2 < hi])
    return pts


def ground_point(pix, pos, b, f, plane_pt, plane_n):
    d_cam = np.array([(pix[0] - 640.0) / f, (pix[1] - 360.0) / f, 1.0])
    d_w = b.T @ d_cam
    den = float(d_w @ plane_n)
    if abs(den) < 1e-9:
        return None
    lam = float((plane_pt - pos) @ plane_n) / den
    if lam <= 0:
        return None
    return pos + lam * d_w


def nearest_on_polyline(G, P, n_plane):
    """In-plane nearest point of G on the 3D polyline P (consecutive rows)."""
    A = P[:-1]
    B = P[1:]
    # project everything into the plane through G
    def proj(X):
        return X - np.outer((X - G) @ n_plane, n_plane)
    A2, B2 = proj(A), proj(B)
    v = B2 - A2
    t = np.clip(np.sum((G - A2) * v, axis=1) / np.maximum(np.sum(v * v, axis=1), 1e-12), 0, 1)
    near = A2 + t[:, None] * v
    d = np.linalg.norm(near - G, axis=1)
    i = int(np.argmin(d))
    tangent = v[i] / max(np.linalg.norm(v[i]), 1e-12)
    return near[i], float(d[i]), tangent, i


def evaluate_scene(shot_pose, annotations, line_lookup, geometry, cam, window):
    pos, b, up, right, fw = camera_from_pose(shot_pose["position"], shot_pose["quaternion"], cam)
    f = cam["f"]
    out = []
    for a in annotations:
        name = line_lookup(a)
        P = segment_window(geometry["lines"][name], shot_pose["station"], geometry["trackLength"], *window)
        pc = (b @ (P - pos).T).T
        valid = (pc[:-1, 2] > 2) & (pc[1:, 2] > 2) & (pc[:-1, 2] < 180) & (pc[1:, 2] < 180)
        xy = pc[:, :2] / np.maximum(pc[:, 2, None], 1e-5) * f + [640, 360]
        idx = np.nonzero(valid)[0]
        aa = xy[:-1][valid]
        bb = xy[1:][valid]
        side = "left" if name == "paintL" else "right"
        for pt in a["points"]:
            pt = np.array(pt, dtype=float)
            v = bb - aa
            t = np.clip(np.sum((pt - aa) * v, axis=1) / np.maximum(np.sum(v * v, axis=1), 1e-12), 0, 1)
            near = aa + t[:, None] * v
            d = np.linalg.norm(near - pt, axis=1)
            k = int(np.argmin(d))
            px = float(d[k])
            seg = idx[k]
            zA, zB = pc[seg, 2], pc[seg + 1, 2]
            tk = float(t[k])
            tau = (tk / zB) / ((1 - tk) / zA + tk / zB)
            Q = P[seg] + tau * (P[seg + 1] - P[seg])
            depth = float((b @ (Q - pos))[2])
            naive_m = px * depth / f
            n_plane = up / np.linalg.norm(up)
            # image-space unit normal of the matched segment
            sv = v[k] / max(np.linalg.norm(v[k]), 1e-12)
            img_n = np.array([-sv[1], sv[0]])
            G = ground_point(pt, pos, b, f, Q, n_plane)
            rec = {
                "side": side,
                "residualPx": px,
                "cameraDepthM": depth,
                "naiveMetres": naive_m,
            }
            if G is None:
                rec.update({"lateralMetres": None, "note": "pick ray does not hit the local road plane"})
                out.append(rec)
                continue
            nearG, dG, tan3, _ = nearest_on_polyline(G, P, n_plane)
            nb = np.cross(n_plane, tan3)
            nb /= max(np.linalg.norm(nb), 1e-12)
            if nb @ right < 0:
                nb = -nb
            signed = float((G - nearG) @ nb)
            horiz = float(np.linalg.norm((G - pos) - ((G - pos) @ n_plane) * n_plane))
            # metre equivalent of +-4 px along the image normal of the matched segment
            unc = []
            for sgn in (+1, -1):
                Gs = ground_point(pt + sgn * PICK_SIGMA_PX * img_n, pos, b, f, Q, n_plane)
                if Gs is None:
                    unc.append(float("nan"))
                else:
                    ns, _, _, _ = nearest_on_polyline(Gs, P, n_plane)
                    unc.append(float((Gs - ns) @ nb))
            unc_m = float(np.nanmax(np.abs(np.array(unc) - signed))) if not all(math.isnan(u) for u in unc) else None
            outward = signed if side == "right" else -signed
            rec.update({
                "lateralMetres": abs(signed),
                "signedRightMetres": signed,
                "outwardMetres": outward,
                "horizontalRangeM": horiz,
                "pick4pxMetres": unc_m,
                "lateralOverNaive": abs(signed) / naive_m if naive_m > 1e-9 else None,
            })
            out.append(rec)
    return out


def pct(vals, q):
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    return float(np.percentile(vals, q)) if vals else None


def stats(vals):
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return {"n": len(vals), "median": float(np.median(vals)), "p25": pct(vals, 25), "p75": pct(vals, 75),
            "p95": pct(vals, 95), "max": float(np.max(vals))}


COND_MAX_PICK_M = 1.0  # a point is "well-conditioned" if 4 px of picking error moves its ground point <= 1 m


def conditioned(r):
    return r.get("lateralMetres") is not None and r.get("pick4pxMetres") is not None and r["pick4pxMetres"] <= COND_MAX_PICK_M


def decompose(rows):
    """Non-unique diagnostic: signedRight = a + psi*D + c*outwardSign (Huber IRLS) on conditioned points.
    a ~ common lateral shift at the car (+ = video boundaries appear right => real car left of replay),
    psi ~ yaw/sideslip-like rotation (rad), c ~ width/scale differential (+ = video road wider)."""
    sub = [r for r in rows if conditioned(r)]
    sides = {r["side"] for r in sub}
    # one-sided scenes cannot separate a from psi over the short near-field range: do not report them
    if len(sides) < 2 or min(sum(r["side"] == s for r in sub) for s in sides) < 3:
        return None
    D = np.array([r["horizontalRangeM"] for r in sub])
    y = np.array([r["signedRightMetres"] for r in sub])
    o = np.array([1.0 if r["side"] == "right" else -1.0 for r in sub])
    cols = [np.ones_like(D), D]
    names = ["a", "psi"]
    if len(sides) == 2 and len(sub) >= 6:
        cols.append(o)
        names.append("c")
    X = np.stack(cols, axis=1)
    w = np.ones_like(y)
    for _ in range(30):
        W = np.sqrt(w)
        beta, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
        res = y - X @ beta
        k = 0.5
        w = np.where(np.abs(res) <= k, 1.0, k / np.maximum(np.abs(res), 1e-9))
    res = y - X @ beta
    out = {n: float(v) for n, v in zip(names, beta)}
    out["psiDeg"] = math.degrees(out["psi"])
    out["points"] = len(sub)
    out["sides"] = sorted(sides)
    out["medianAbsResidualAfterFitM"] = float(np.median(np.abs(res)))
    out["note"] = ("Non-identifiable diagnostic only: camera attitude (unrendered body roll/pitch), lens distortion and "
                   "GPS lateral/heading all map onto a, psi and c. Not a GPS correction.")
    return out


def summarise(rows):
    cond = [r for r in rows if conditioned(r)]
    s = {
        "conditionedPoints": len(cond),
        "conditionedLateralMetres": stats([r["lateralMetres"] for r in cond]),
        "decomposition": decompose(rows),
        "points": len(rows),
        "residualPx": stats([r["residualPx"] for r in rows]),
        "lateralMetres": stats([r.get("lateralMetres") for r in rows]),
        "naiveMetres": stats([r["naiveMetres"] for r in rows]),
        "pick4pxMetres": stats([r.get("pick4pxMetres") for r in rows]),
        "cameraDepthM": stats([r["cameraDepthM"] for r in rows]),
    }
    for side in ("left", "right"):
        sub = [r for r in rows if r["side"] == side and r.get("signedRightMetres") is not None]
        if sub:
            s[f"{side}SignedRightMedianM"] = float(np.median([r["signedRightMetres"] for r in sub]))
            s[f"{side}OutwardMedianM"] = float(np.median([r["outwardMetres"] for r in sub]))
            s[f"{side}LateralMedianM"] = float(np.median([r["lateralMetres"] for r in sub]))
            s[f"{side}Points"] = len(sub)
    if "leftSignedRightMedianM" in s and "rightSignedRightMedianM" in s:
        s["commonModeRightM"] = (s["leftSignedRightMedianM"] + s["rightSignedRightMedianM"]) / 2
        s["differentialOutwardM"] = (s["leftOutwardMedianM"] + s["rightOutwardMedianM"]) / 2
    return s


def main():
    geometry = json.loads((SRC / "runtime-geometry.json").read_text("utf-8"))
    profile = json.loads((SRC / "camera-profile.json").read_text("utf-8"))
    held = json.loads((EV / "heldout-boundary-picks.json").read_text("utf-8"))
    heldout_eval = json.loads((SRC / "heldout-evaluation.json").read_text("utf-8"))
    published = json.loads((EV / "independent-camera-evaluation.json").read_text("utf-8"))
    seq_ann = json.loads((EV / "sequence" / "sequence-annotations.json").read_text("utf-8"))
    seq_pose = json.loads((EV / "sequence" / "runtime-poses.json").read_text("utf-8"))
    shots = {s["id"]: s for s in geometry["shots"]}

    cams = {}
    for h in heldout_eval["heightSensitivity"]:
        f, pitch, yaw, roll = h["parameters"]
        cams[f"h{h['heightMeters']:.2f}"] = {"f": f, "pitch": pitch, "yaw": yaw, "roll": roll,
                                             "up": h["heightMeters"], "forward": profile["forwardMeters"],
                                             "right": profile["rightMeters"]}
    frozen = {"f": profile["f"], "pitch": profile["pitchDeg"], "yaw": profile["yawDeg"], "roll": profile["rollDeg"],
              "up": profile["upMeters"], "forward": profile["forwardMeters"], "right": profile["rightMeters"]}
    assert abs(cams["h1.25"]["f"] - frozen["f"]) < 1e-9

    pub = {r["id"]: r for r in published["rows"]}
    points_out = []
    scene_out = []
    reproduction = []
    for cam_id, cam in [("frozen_h1.25", frozen), ("h1.00", cams["h1.00"]), ("h1.50", cams["h1.50"])]:
        for row in held["shots"]:
            pts = evaluate_scene(shots[row["id"]], row["annotations"],
                                 lambda a: "paintL" if a["side"] == "left" else "paintR", geometry, cam, (-5, 180))
            for p in pts:
                p.update({"scene": row["id"], "set": "heldout10", "camera": cam_id})
            points_out.extend(pts)
            sm = summarise(pts)
            sm.update({"scene": row["id"], "set": "heldout10", "camera": cam_id})
            scene_out.append(sm)
            if cam_id == "frozen_h1.25":
                reproduction.append({"scene": row["id"], "publishedAfterMedianPx": pub[row["id"]]["afterMedianPx"],
                                     "reproducedMedianPx": sm["residualPx"]["median"],
                                     "absDiffPx": abs(pub[row["id"]]["afterMedianPx"] - sm["residualPx"]["median"])})
        # post-selection diagnostic sequence (offset 0 poses only)
        poses = {s["id"]: s for s in seq_pose["shots"]}
        for row in seq_ann["shots"]:
            p0 = next(p for p in poses[row["id"]]["poses"] if p["offset"] == 0)
            pts = evaluate_scene(p0, row["annotations"], lambda a: a["name"], geometry, cam, (-20, 195))
            for p in pts:
                p.update({"scene": row["id"], "set": "sequence8_postselection", "camera": cam_id})
            points_out.extend(pts)
            sm = summarise(pts)
            sm.update({"scene": row["id"], "set": "sequence8_postselection", "camera": cam_id})
            scene_out.append(sm)

    def pooled(set_name, cam_id):
        sc = [s for s in scene_out if s["set"] == set_name and s["camera"] == cam_id]
        pt = [p for p in points_out if p["set"] == set_name and p["camera"] == cam_id]
        med = [s["lateralMetres"]["median"] for s in sc]
        near = [p for p in pt if p.get("horizontalRangeM") is not None and p["horizontalRangeM"] <= 25]
        far = [p for p in pt if p.get("horizontalRangeM") is not None and p["horizontalRangeM"] > 25]
        cond_sc = [s for s in sc if s["conditionedLateralMetres"] is not None and s["conditionedPoints"] >= 3]
        cond_pts = [p for p in pt if conditioned(p)]
        return {
            "conditionedRule": f"pick4pxMetres <= {COND_MAX_PICK_M} m and ground intersection exists",
            "conditionedPoints": len(cond_pts),
            "unconditionedPoints": len(pt) - len(cond_pts),
            "conditionedScenesWith3plus": len(cond_sc),
            "conditionedSceneMedianLateralM": {s["scene"]: round(s["conditionedLateralMetres"]["median"], 3) for s in sc if s["conditionedLateralMetres"] is not None},
            "conditionedScenePoints": {s["scene"]: s["conditionedPoints"] for s in sc},
            "conditionedMedianOfSceneMediansM": float(np.median([s["conditionedLateralMetres"]["median"] for s in cond_sc])) if cond_sc else None,
            "conditionedMaxSceneMedianM": float(np.max([s["conditionedLateralMetres"]["median"] for s in cond_sc])) if cond_sc else None,
            "conditionedPointsLateral": stats([p["lateralMetres"] for p in cond_pts]),
            "conditionedPointsPick4px": stats([p["pick4pxMetres"] for p in cond_pts]),
            "decompositionByScene": {s["scene"]: s["decomposition"] for s in sc},
            "minimum3dDisplacementPointsAll": stats([p["naiveMetres"] for p in pt]),
            "scenes": len(sc),
            "medianOfSceneMedianLateralM": float(np.median(med)),
            "meanOfSceneMedianLateralM": float(np.mean(med)),
            "maxSceneMedianLateralM": float(np.max(med)),
            "sceneMedianLateralM": {s["scene"]: round(s["lateralMetres"]["median"], 3) for s in sc},
            "medianOfSceneMedianPx": float(np.median([s["residualPx"]["median"] for s in sc])),
            "medianOfSceneMedianNaiveM": float(np.median([s["naiveMetres"]["median"] for s in sc])),
            "medianOfSceneMedianPick4pxM": float(np.median([s["pick4pxMetres"]["median"] for s in sc])),
            "pointsAll": stats([p.get("lateralMetres") for p in pt]),
            "pointsWithin25m": stats([p.get("lateralMetres") for p in near]),
            "pointsBeyond25m": stats([p.get("lateralMetres") for p in far]),
            "pick4pxWithin25m": stats([p.get("pick4pxMetres") for p in near]),
            "lateralOverNaive": stats([p.get("lateralOverNaive") for p in pt]),
            "sceneCommonModeRightM": {s["scene"]: round(s["commonModeRightM"], 3) for s in sc if "commonModeRightM" in s},
            "sceneDifferentialOutwardM": {s["scene"]: round(s["differentialOutwardM"], 3) for s in sc if "differentialOutwardM" in s},
            "sceneSideLateralMedianM": {s["scene"]: {k: round(s[k], 3) for k in ("leftLateralMedianM", "rightLateralMedianM") if k in s} for s in sc},
        }

    summary = {
        "scope": "Video-vs-CG road-facing boundary residuals of the Codex 2026-10-03 estimated onboard camera, converted to an "
                 "equivalent lateral ground offset at each annotated point. This is an image-agreement quantity under one "
                 "assumed camera (height 1.25 m not measured); it lumps GPS lateral error, heading/sideslip, camera "
                 "intrinsics/lens distortion, course geometry and picking error. It is not a GPS truth measurement.",
        "codeDataState": "Uncommitted working tree on top of origin/master 7ebf89b: Codex onboard camera profile + pre-repair "
                         "runtime geometry (road repair at k16/k19/k21 did not change these residuals per "
                         "evidence/final-road-camera-effect.json). GPS registration = shipped default gps_registration.json "
                         "(not ?gps=kerb). 7/30 data = re-timed channels (committed d9a1ea9).",
        "inputsSha256": {
            "runtime-geometry.json": sha(SRC / "runtime-geometry.json"),
            "camera-profile.json": sha(SRC / "camera-profile.json"),
            "heldout-boundary-picks.json": sha(EV / "heldout-boundary-picks.json"),
            "sequence-annotations.json": sha(EV / "sequence" / "sequence-annotations.json"),
            "sequence-runtime-poses.json": sha(EV / "sequence" / "runtime-poses.json"),
        },
        "reproductionOfPublishedPx": {
            "maxAbsDiffPx": max(r["absDiffPx"] for r in reproduction),
            "rows": reproduction,
        },
        "pooled": {
            f"{s}|{c}": pooled(s, c)
            for s in ("heldout10", "sequence8_postselection")
            for c in ("frozen_h1.25", "h1.00", "h1.50")
        },
        "scenes": [s for s in scene_out],
        "method": __doc__,
        "grade": "derived (from measured pixel picks + frozen estimated camera + exact runtime poses; camera height assumed)",
    }
    (HERE / "px_to_metres_points.json").write_text(json.dumps(points_out, indent=1) + "\n", "utf-8")
    (HERE / "px_to_metres_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    p = summary["pooled"]["heldout10|frozen_h1.25"]
    print("reproduction max diff px", summary["reproductionOfPublishedPx"]["maxAbsDiffPx"])
    print(json.dumps({k: p[k] for k in ("conditionedPoints", "unconditionedPoints", "conditionedSceneMedianLateralM",
                                         "conditionedScenePoints", "conditionedMedianOfSceneMediansM",
                                         "conditionedMaxSceneMedianM", "conditionedPointsLateral", "conditionedPointsPick4px",
                                         "minimum3dDisplacementPointsAll", "medianOfSceneMedianNaiveM",
                                         "medianOfSceneMedianLateralM", "maxSceneMedianLateralM",
                                         "decompositionByScene")}, indent=1))
    for key in ("heldout10|h1.00", "heldout10|h1.50", "sequence8_postselection|frozen_h1.25"):
        q = summary["pooled"][key]
        print(key, "cond med-of-med", q["conditionedMedianOfSceneMediansM"], "cond max", q["conditionedMaxSceneMedianM"],
              q["conditionedSceneMedianLateralM"], "naive", round(q["medianOfSceneMedianNaiveM"], 3))


if __name__ == "__main__":
    main()
