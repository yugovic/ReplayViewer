"""Build the I3 table (CSV + JSON summary + SVG chart, no video pixels) from lateral-offset-results.json,
and cross-check each scene against the IMU kerb-contact labels / apex KPI and the drawn track limits.
"""
import csv
import json
import math
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
RES = json.loads((HERE / os.environ.get("I3_RESULTS", "lateral-offset-results.json")).read_text(encoding="utf-8"))
HALF_WIDTH = 1.695 / 2  # body half width (mazda2-dj spec assumption used by the registration)
ON_TOL, SILENT_DEEP = 0.15, -0.40  # build_apex_kpi.py thresholds (strong-but-off > 0.15; silent-but-deep < -0.55)
APEX_WINDOW = 50.0


def load_race(race):
    base = ROOT / "public" / "data" / "races" / race
    return (json.loads((base / "apex_kpi.json").read_text(encoding="utf-8")),
            json.loads((base / "kerb_contacts.json").read_text(encoding="utf-8")))


RACES = {r: load_race(r) for r in ["fuji_aim_01", "fuji_aim_2020_07_30"]}
ZONES = {}
for z in json.loads((ROOT / "artifacts/video-cg-alignment-2026-10-03/runtime-geometry.json").read_text(encoding="utf-8"))["zones"]:
    ZONES[z["id"]] = z


def kerb_cross(scene, dy):
    apex, _ = RACES[scene["raceId"]]
    rows = apex["laps"].get(str(scene["lap"]), [])
    out = []
    for r in rows:
        dist = r["apexStation"] - scene["stationMeters"]
        if abs(dist) > APEX_WINDOW:
            continue
        side = ZONES[r["kerb"]]["side"]
        g = min(r["gBlockFront"], r["gBlockRear"])
        g_new = g + dy if side == "left" else g - dy
        cls = (r.get("contact") or {}).get("class")

        def verdict(gg):
            if cls == "strong":
                return "contradiction(strong-but-off)" if gg > ON_TOL else "consistent"
            if cls == "silent":
                return "contradiction(silent-but-deep)" if gg < SILENT_DEEP - ON_TOL else "consistent"
            return "uninformative(mild/none)"

        out.append({"kerb": r["kerb"], "side": side, "apexStation": r["apexStation"], "apexMinusSceneM": round(dist, 1),
                    "label": cls, "peakRollRms": (r.get("contact") or {}).get("peakRollRms"),
                    "drawnGBlockM": g, "gBlockIfDyAppliedM": round(g_new, 2),
                    "verdictDrawn": verdict(g), "verdictWithDy": verdict(g_new)})
    return out


def grade(scene):
    m = scene["h1.25"]
    fy = m["fixedYaw"]
    per = m["perBoundaryFixedYaw"]
    notes = []
    w = fy["pointBootstrap"]["ci95"][1] - fy["pointBootstrap"]["ci95"][0]
    bound = fy["atBound"] or fy["pointBootstrap"]["fractionAtBound"] > 0.05
    side_bound = [k for k, v in per.items() if v["atBound"]]
    two = "twoSidedConsistency" in m
    yaw_sens = abs(m["freeYaw"]["dyMeters"] - fy["dyMeters"]) > 0.5
    if bound:
        notes.append("combined fit runs to the +-5 m bound (no lateral optimum)")
    if side_bound and not bound:
        notes.append(f"{'/'.join(side_bound)} alone has no lateral optimum (runs to the bound): far/low-leverage points "
                     "misfit vertically, not laterally")
    if two:
        tc = m["twoSidedConsistency"]
        lo, hi = tc["ci95"]
        if (lo > 0 or hi < 0) and abs(tc["dyRightMinusLeftMeters"]) > 1.0:
            notes.append(f"left/right disagree by {tc['dyRightMinusLeftMeters']:+.2f} m (CI {lo:+.2f}..{hi:+.2f})")
            inconsistent = True
        else:
            inconsistent = False
    else:
        inconsistent = False
        notes.append("single boundary: dy and heading not separable")
    if yaw_sens:
        notes.append(f"yaw-sensitive: free-yaw dy {m['freeYaw']['dyMeters']:+.2f} m")
    if w > 1.0:
        notes.append(f"wide point-bootstrap CI ({w:.2f} m)")
    if bound:
        g = "D"
    elif inconsistent or side_bound:
        g = "C"
    elif two and w <= 1.0 and not yaw_sens:
        g = "A"
    elif w <= 1.0 and not yaw_sens:
        g = "B"
    else:
        g = "C"
    return g, notes


def fmt(x, n=2):
    return "" if x is None else f"{x:+.{n}f}"


def main():
    hier = RES["hierarchicalBootstrap"]["fixedYawDyCi95"]
    slopes = RES["circularityInjection"]["slopeDyPerTrainingShift"]
    k1 = RES.get("lensDistortionSensitivity", {}).get("scenes", {})
    rg = {run["rollGradientDegPerG"]: run["scenes"] for run in RES["rollGradientSensitivity"]["runs"]}
    k_imu = RES["rollGradientSensitivity"].get("imuMeasuredGradientDegPerG")
    k_imu_key = round(k_imu, 3) if k_imu else None
    altp = HERE / "alt-reference.json"
    ALT = json.loads(altp.read_text(encoding="utf-8")) if altp.exists() else None
    table = []
    for s in RES["scenes"]:
        m = s["h1.25"]
        fy, fr = m["fixedYaw"], m["freeYaw"]
        per = m["perBoundaryFixedYaw"]
        g, notes = grade(s)
        dy = fy["dyMeters"]
        kc = kerb_cross(s, dy) if s["group"] != "train" else kerb_cross(s, dy)
        dl, dr = s["drawnCentreToLeftPaintM"], s["drawnCentreToRightPaintM"]
        # track-limit plausibility of the video-implied position (car right axis ~ road normal)
        cosang = math.cos(math.radians(s["carHeadingMinusRoadDeg"]))
        tyre_l = dl + dy * cosang - HALF_WIDTH
        tyre_r = dr - dy * cosang - HALF_WIDTH
        limit_note = None
        if min(tyre_l, tyre_r) < -1.2:
            limit_note = f"implied body edge {min(tyre_l, tyre_r):+.2f} m beyond the paint line (off the kerb) -> implausible"
        row = {
            "scene": s["id"], "group": s["group"], "session": s["session"], "lap": s["lap"],
            "lapTimeS": s["lapTimeSeconds"], "stationM": round(s["stationMeters"], 1),
            "lapDistM": round(s["telemetry"]["dist"], 1), "speedKmh": round(s["telemetry"]["speed"], 1),
            "accyG": round(s["telemetry"]["accy"], 2), "carHeadingMinusRoadDeg": round(s["carHeadingMinusRoadDeg"], 1),
            "linesLR": f"{s['linesScored'].get('paintL', 0)}/{s['linesScored'].get('paintR', 0)}",
            "baselineMedianPx": round(m["baseline"]["medianPx"], 1),
            "dyFixedYawM": round(dy, 2), "dyFixedCi95Point": [round(v, 2) for v in fy["pointBootstrap"]["ci95"]],
            "dyFixedCi95Hier": [round(v, 2) for v in hier[s["id"]]] if s["id"] in hier else None,
            "medianPxAfterFixed": round(fy["medianPx"], 1),
            "dyFreeYawM": round(fr["dyMeters"], 2), "dyFreeCi95": [round(v, 2) for v in fr["pointBootstrap"]["dyCi95"]],
            "impliedHeadingErrorDeg": round(fr["dyawDeg"], 2),
            "headingCi95": [round(v, 2) for v in fr["pointBootstrap"]["dyawCi95"]], "yawAtBound": fr["yawAtBound"],
            "medianPxAfterFree": round(fr["medianPx"], 1),
            "yawOnlyDeg": round(m["yawOnly"]["dyawDeg"], 2),
            "dyLeftOnlyM": round(per["paintL"]["dyMeters"], 2) if "paintL" in per else None,
            "dyRightOnlyM": round(per["paintR"]["dyMeters"], 2) if "paintR" in per else None,
            "rightMinusLeftM": round(m["twoSidedConsistency"]["dyRightMinusLeftMeters"], 2) if "twoSidedConsistency" in m else None,
            "rightMinusLeftCi95": [round(v, 2) for v in m["twoSidedConsistency"]["ci95"]] if "twoSidedConsistency" in m else None,
            "dyH1_0": round(s["heightSensitivity"]["1.0"]["fixedYawDy"], 2),
            "dyH1_5": round(s["heightSensitivity"]["1.5"]["fixedYawDy"], 2),
            "dyK1Camera": round(k1[s["id"]]["fixedYawDy"], 2) if s["id"] in k1 else None,
            "dyImuRoll": round(rg[k_imu_key][s["id"]]["fixedYawDy"], 2) if k_imu_key in rg else None,
            "dyRollFree": round(m["diagRollFree"]["dyMeters"], 2), "rollFreeDeg": round(m["diagRollFree"]["drollDeg"], 2),
            "dyForwardFree": round(m["diagForwardFree"]["dyMeters"], 2), "forwardFreeM": round(m["diagForwardFree"]["dxMeters"], 2),
            "injectionSlope": round(slopes[s["id"]], 2) if s["id"] in slopes else None,
            "dyAltReference": round(ALT["scenes"][s["id"]]["fixedYawDy"], 2) if ALT else None,
            "drawnCentreToLeftPaintM": round(dl, 2), "drawnCentreToRightPaintM": round(dr, 2),
            "grade": g, "notes": notes, "trackLimitNote": limit_note, "kerbCrossCheck": kc,
        }
        table.append(row)
    # CSV
    cols = ["scene", "group", "session", "lap", "lapTimeS", "stationM", "accyG", "linesLR", "baselineMedianPx",
            "dyFixedYawM", "dyFixedCi95Point", "dyFixedCi95Hier", "medianPxAfterFixed", "dyFreeYawM", "dyFreeCi95",
            "impliedHeadingErrorDeg", "headingCi95", "dyLeftOnlyM", "dyRightOnlyM", "rightMinusLeftM",
            "rightMinusLeftCi95", "dyH1_0", "dyH1_5", "dyK1Camera", "dyImuRoll", "dyRollFree", "rollFreeDeg",
            "dyForwardFree", "forwardFreeM", "injectionSlope", "dyAltReference", "carHeadingMinusRoadDeg",
            "drawnCentreToLeftPaintM", "drawnCentreToRightPaintM", "grade", "notes", "trackLimitNote"]
    with open(HERE / "lateral-offset-table.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in table:
            w.writerow([("; ".join(r[c]) if isinstance(r[c], list) and r[c] and isinstance(r[c][0], str)
                         else (json.dumps(r[c]) if isinstance(r[c], list) else r[c])) for c in cols])
    # group stats
    def grp(sel):
        v = np.array([r["dyFixedYawM"] for r in table if sel(r)])
        if len(v) == 0:
            return None
        return {"n": int(len(v)), "median": float(np.median(v)), "medianAbs": float(np.median(np.abs(v))),
                "min": float(v.min()), "max": float(v.max()), "sd": float(v.std())}
    summary = {
        "heldoutAll": grp(lambda r: r["group"] == "heldout"),
        "heldoutGradeAB": grp(lambda r: r["group"] == "heldout" and r["grade"] in "AB"),
        "heldout0730": grp(lambda r: r["group"] == "heldout" and r["session"].startswith("2020-07-30")),
        "heldout0729": grp(lambda r: r["group"] == "heldout" and r["session"].startswith("2020-07-29")),
        "sequenceAll": grp(lambda r: r["group"] == "sequence"),
        "sequenceGradeAB": grp(lambda r: r["group"] == "sequence" and r["grade"] in "AB"),
        "trainInSample": grp(lambda r: r["group"] == "train"),
        "allNonTrainGradeAB": grp(lambda r: r["group"] != "train" and r["grade"] in "AB"),
        "allNonTrainNotD": grp(lambda r: r["group"] != "train" and r["grade"] != "D"),
    }
    pairs = []
    for a in table:
        for b in table:
            if a["session"].startswith("2020-07-30") and b["session"].startswith("2020-07-29") and                     abs(a["stationM"] - b["stationM"]) <= 10:
                pairs.append({"dry": a["scene"], "wet": b["scene"], "stationDiffM": round(a["stationM"] - b["stationM"], 1),
                              "dyDry": a["dyFixedYawM"], "dyWet": b["dyFixedYawM"],
                              "dryMinusWetM": round(a["dyFixedYawM"] - b["dyFixedYawM"], 2),
                              "grades": a["grade"] + b["grade"],
                              "meaning": "same place, same CG course: course-boundary and common camera errors cancel; "
                                         "the difference is the lateral GPS/registration error difference between the two laps"})
    out = {"meaning": "dy + = car actually further right than drawn; relative to the 3 wet training frames' GPS (see circularity)",
           "sameplaceCrossSessionPairs": pairs,
           "grades": {"A": "both boundaries, consistent, point CI <= 1 m, not yaw-sensitive",
                      "B": "one boundary (or weakly constrained second), CI <= 1 m, not yaw-sensitive",
                      "C": "left/right disagree > 1 m (or one side has no lateral optimum), yaw-sensitive or wide CI: residual not a clean lateral offset",
                      "D": "combined fit runs to the +-5 m bound: no lateral optimum, value meaningless"},
           "groupStats": summary, "rows": table,
           "cameraParityMaxAbsDiff": [p["maxAbsDiff"] for p in RES["cameraRefitParity"]],
           "heldoutReproductionPx": RES["heldoutReproduction"]["medianOfSceneMediansPx"],
           "trainingCommonOffsetIdentifiability": {k: RES["trainingCommonOffsetIdentifiability"][k] for k in
                                                   ["x", "cost", "frozenCameraCost", "conditionNumber"]},
           "trainingCommonOffsetProfile": [{k: p[k] for k in ["commonTrainingDyMeters", "cost", "trainMedianPx", "camera"]}
                                           for p in RES["trainingCommonOffsetIdentifiability"]["profileOverCommonTrainingDy"]],
           "rollGradient": {"imuMeasured": k_imu,
                            "heldoutMedianPxByK": {str(r["rollGradientDegPerG"]): r["heldoutMedianOfSceneMediansPx"]
                                                   for r in RES["rollGradientSensitivity"]["runs"]},
                            "trainOnly5ParamFit": RES["trainingRollGradientIdentifiability"]["x"]},
           "alternativeReference": None if not ALT else {
               "meaning": "5-parameter training fit (camera + common training offset) prefers the training car "
                          f"{ALT['trainingCommonDyMeters']:+.2f} m right of drawn with camera yaw {ALT['alternativeCamera'][2]:+.2f} deg; "
                          "dyAltReference gives each scene under that hypothesis",
               "costAlt": ALT["costAlt"], "costFrozen": ALT["costFrozen"],
               "heldoutMedianOfSceneMediansPxAlt": ALT["heldoutMedianOfSceneMediansPxAlt"],
               "heldoutMeanOfSceneMediansPxAlt": ALT["heldoutMeanOfSceneMediansPxAlt"]},
           "lensDistortion": {k: RES["lensDistortionSensitivity"][k] for k in
                              ["camera", "activeBounds", "trainMedianPx", "heldoutMedianOfSceneMediansPx"]}
           if "lensDistortionSensitivity" in RES else None}
    (HERE / "lateral-offset-summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    svg(table)
    for r in table:
        print(f"{r['scene']:24s} {r['group']:8s} s={r['stationM']:7.1f} accy={r['accyG']:+.2f} L/R={r['linesLR']:5s} "
              f"base={r['baselineMedianPx']:5.1f}px  dy={r['dyFixedYawM']:+.2f} {r['dyFixedCi95Point']} hier={r['dyFixedCi95Hier']} "
              f"free={r['dyFreeYawM']:+.2f}/{r['impliedHeadingErrorDeg']:+.2f}deg  L={r['dyLeftOnlyM']} R={r['dyRightOnlyM']} "
              f"h1.0={r['dyH1_0']:+.2f} h1.5={r['dyH1_5']:+.2f} k1={r['dyK1Camera']} imuRoll={r['dyImuRoll']} "
              f"grade={r['grade']} {r['trackLimitNote'] or ''}")
        for k in r["kerbCrossCheck"]:
            print("     kerb", k)
    print(json.dumps(summary, indent=1))


def svg(table):
    W, H, ml, mr, mt, mb = 980, 420, 60, 20, 30, 50
    xs = [r["stationM"] for r in table]
    x0, x1 = 800, 4000
    y0, y1 = -5.5, 5.5

    def X(v):
        return ml + (v - x0) / (x1 - x0) * (W - ml - mr)

    def Y(v):
        return mt + (y1 - v) / (y1 - y0) * (H - mt - mb)

    col = {"A": "#1b7f5b", "B": "#3a6fb0", "C": "#c98a1a", "D": "#b03a3a"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for v in range(-5, 6):
        parts.append(f'<line x1="{ml}" x2="{W-mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{"#888" if v == 0 else "#e6e6e6"}"/>')
        parts.append(f'<text x="{ml-6}" y="{Y(v)+4:.1f}" text-anchor="end" fill="#444">{v:+d}</text>')
    for s in range(1000, 4001, 500):
        parts.append(f'<line x1="{X(s):.1f}" x2="{X(s):.1f}" y1="{mt}" y2="{H-mb}" stroke="#f0f0f0"/>')
        parts.append(f'<text x="{X(s):.1f}" y="{H-mb+16}" text-anchor="middle" fill="#444">{s}</text>')
    parts.append(f'<text x="{(W)/2}" y="{H-12}" text-anchor="middle" fill="#222">CG station (m)</text>')
    parts.append(f'<text x="16" y="{H/2}" transform="rotate(-90 16 {H/2})" text-anchor="middle" fill="#222">dy fixed-yaw (m, + = car actually further right)</text>')
    parts.append(f'<text x="{ml}" y="18" fill="#222">I3: video-implied lateral offset per scene (relative to the 3 wet training frames; bars = point-bootstrap 95%)</text>')
    for r in table:
        x = X(r["stationM"])
        lo, hi = r["dyFixedCi95Point"]
        c = col[r["grade"]]
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{Y(max(min(hi,5.4),-5.4)):.1f}" y2="{Y(max(min(lo,5.4),-5.4)):.1f}" stroke="{c}" stroke-width="2" opacity="0.7"/>')
        yv = Y(max(min(r["dyFixedYawM"], 5.4), -5.4))
        if r["group"] == "train":
            parts.append(f'<rect x="{x-4:.1f}" y="{yv-4:.1f}" width="8" height="8" fill="none" stroke="#555" stroke-width="1.5"/>')
        elif r["group"] == "sequence":
            parts.append(f'<path d="M{x:.1f},{yv-5:.1f} L{x+5:.1f},{yv+4:.1f} L{x-5:.1f},{yv+4:.1f} Z" fill="{c}"/>')
        else:
            shape = "circle" if r["session"].startswith("2020-07-30") else "diamond"
            if shape == "circle":
                parts.append(f'<circle cx="{x:.1f}" cy="{yv:.1f}" r="4.5" fill="{c}"/>')
            else:
                parts.append(f'<path d="M{x:.1f},{yv-5.5:.1f} L{x+5.5:.1f},{yv:.1f} L{x:.1f},{yv+5.5:.1f} L{x-5.5:.1f},{yv:.1f} Z" fill="{c}"/>')
    lx = W - mr - 330
    items = [("circle", "held-out 7/30 L2"), ("diamond", "held-out 7/29 L3"), ("tri", "diagnostic sequence 7/30 L2"),
             ("square", "training 7/29 L3 (in-sample)")]
    for i, (sh, label) in enumerate(items):
        yy = mt + 14 + i * 15
        if sh == "circle":
            parts.append(f'<circle cx="{lx}" cy="{yy}" r="4" fill="#666"/>')
        elif sh == "diamond":
            parts.append(f'<path d="M{lx},{yy-5} L{lx+5},{yy} L{lx},{yy+5} L{lx-5},{yy} Z" fill="#666"/>')
        elif sh == "tri":
            parts.append(f'<path d="M{lx},{yy-5} L{lx+5},{yy+4} L{lx-5},{yy+4} Z" fill="#666"/>')
        else:
            parts.append(f'<rect x="{lx-4}" y="{yy-4}" width="8" height="8" fill="none" stroke="#555"/>')
        parts.append(f'<text x="{lx+10}" y="{yy+4}" fill="#333">{label}</text>')
    for i, (gname, c) in enumerate(col.items()):
        parts.append(f'<rect x="{lx+180}" y="{mt+8+i*15}" width="10" height="10" fill="{c}"/><text x="{lx+195}" y="{mt+17+i*15}" fill="#333">grade {gname}</text>')
    parts.append("</svg>")
    (HERE / "lateral-offset-chart.svg").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
