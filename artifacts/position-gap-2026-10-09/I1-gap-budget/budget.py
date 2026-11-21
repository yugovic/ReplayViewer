"""I1 gap budget: assemble the current video-vs-replay position gap budget from existing evidence (numbers only).

Repo root: env REPLAYVIEWER_ROOT, else three levels above this file. Reads only repo files (public lap JSON,
registration JSON, evaluation JSON) and px_to_metres_summary.json produced by px_to_metres.py in this folder.
Writes gap_budget.json next to this file. No video or XRK is opened.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
RACES = ROOT / "public" / "data" / "races"
ART = ROOT / "artifacts"
SESSIONS = {"fuji_aim_01": "2020-07-29 wet", "fuji_aim_2020_07_30": "2020-07-30 dry"}
R_EARTH = 6378137.0


def load(p: Path):
    return json.loads(p.read_text("utf-8"))


def q(vals, p):
    return float(np.percentile(np.asarray(vals, float), p))


def laps(race):
    idx = load(RACES / race / "laps.json")
    for sel in idx["selected"]:
        yield sel["lap"], load(RACES / race / sel["data_file"])


def tangents(lap):
    lat = np.radians(np.asarray(lap["lat"]))
    lng = np.radians(np.asarray(lap["lng"]))
    x = (lng - lng[0]) * math.cos(lat[0]) * R_EARTH  # east
    z = -(lat - lat[0]) * R_EARTH                    # south
    dx = np.gradient(x)
    dz = np.gradient(z)
    n = np.hypot(dx, dz)
    ok = n > 0.05
    t = np.stack([dx / np.where(ok, n, 1), dz / np.where(ok, n, 1)], axis=1)
    return t[ok]


def project(vec, t):
    """|along| and |lateral| components of a fixed (east, south) vector along a lap path."""
    v = np.asarray(vec, float)
    along = t @ v
    lat = t[:, 0] * v[1] - t[:, 1] * v[0]
    return np.abs(along), np.abs(lat)


def speed_stats():
    out = {}
    for race, label in SESSIONS.items():
        allv, minima, maxima = [], [], []
        for lap_no, lap in laps(race):
            v = np.asarray(lap["speed"], float)
            allv.extend(v.tolist())
            pk, _ = find_peaks(-v, prominence=15, distance=30)
            minima.extend(v[pk].tolist())
            maxima.append(float(v.max()))
        allv = np.asarray(allv)
        out[race] = {
            "session": label,
            "samples10Hz": int(allv.size),
            "timeWeightedKmh": {"p10": q(allv, 10), "p50": q(allv, 50), "p90": q(allv, 90), "max": float(allv.max())},
            "perLapMaxKmh": {"median": float(np.median(maxima)), "min": float(np.min(maxima)), "max": float(np.max(maxima))},
            "cornerMinimaKmh": {"n": len(minima), "min": float(np.min(minima)), "median": float(np.median(minima)),
                                "max": float(np.max(minima))},
            "shareOfTime60to100Kmh": float(np.mean((allv >= 60) & (allv <= 100))),
        }
    return out


def to_m(seconds, kmh):
    return seconds * kmh / 3.6


def along_table(seconds_by_name, speeds):
    ref = {"60kmh": 60.0, "80kmh": 80.0, "100kmh": 100.0}
    for race, s in speeds.items():
        ref[f"{race}_medianSpeed"] = s["timeWeightedKmh"]["p50"]
        ref[f"{race}_topSpeed"] = s["timeWeightedKmh"]["max"]
    return {name: {k: round(to_m(sec, v), 3) for k, v in ref.items()} | {"seconds": sec}
            for name, sec in seconds_by_name.items()}


def timer_read_scatter():
    d = load(ART / "video-position-gap-2026-10-02" / "video-timer-reads.json")
    out = {}
    for race, s in d["sessions"].items():
        dev, offsets = [], {}
        for r in s["reads"]:
            diffs = np.asarray(r["read"]) - np.asarray(r["predicted"])
            offsets[r["lap"]] = float(diffs.mean())
            dev.extend((diffs - diffs.mean()).tolist())
        out[race] = {"withinLapReadDeviationS": {"maxAbs": float(np.max(np.abs(dev))), "rms": float(np.sqrt(np.mean(np.square(dev))))},
                     "perLapMeanReadMinusPredictedS": offsets,
                     "perLapCorrectionSeconds": s["perLapCorrectionSeconds"]}
    return out


def manifest_timing():
    m = load(ART / "onboard-comparison-2026-10-03" / "manifest.json")
    rows = []
    for s in m["shots"]:
        rows.append({"id": s["id"], "decodedMinusRequestedS": round(s["decodedFramePtsSeconds"] - s["videoPtsSeconds"], 4),
                     "overlayMinusRequestedS": s["overlayMinusRequestedLapSeconds"],
                     "speedKmh": round(s["telemetry"]["after"]["speed"], 1),
                     "decodedOffsetAlongM": round(to_m(s["decodedFramePtsSeconds"] - s["videoPtsSeconds"], s["telemetry"]["after"]["speed"]), 3)})
    return {"rows": rows, "maxOverlayTimerDifferenceSeconds": m["qa"]["maxOverlayTimerDifferenceSeconds"]}


def registration_evidence():
    out = {}
    summ = load(ART / "gps-accuracy-2026-09-22" / "registration" / "analysis-summary.json")
    sens = load(ART / "gps-accuracy-2026-09-22" / "registration" / "sensitivity-audit.json")
    for race in SESSIONS:
        reg = load(RACES / race / "gps_registration.json")
        lapdata = dict(laps(race))
        rows = []
        for L in summ["sessions"][race]["laps"]:
            t = tangents(lapdata[L["lap"]])
            o = np.asarray(L["offsetMeters"])
            half = []
            for k, cv in L["crossValidation"].items():
                d = np.asarray(cv["offset"]) - o
                a, l = project(d, t)
                half.append({"split": k, "vectorM": d.round(3).tolist(), "normM": float(np.hypot(*d)),
                             "lateralMedianM": float(np.median(l)), "lateralP95M": q(l, 95),
                             "alongMedianM": float(np.median(a)), "alongP95M": q(a, 95)})
            a0, l0 = project(o, t)
            regl = next(x for x in reg["laps"] if x["lap"] == L["lap"])
            rows.append({"lap": L["lap"], "offsetM": L["offsetMeters"], "offsetNormM": float(np.hypot(*o)),
                         "offsetLateralMedianM": float(np.median(l0)), "offsetAlongMedianM": float(np.median(a0)),
                         "jackknifeSigmaM": regl["jackknifeSigmaMeters"], "registeredMaxBeyondM": regl["registered"]["maxBeyondLimitMeters"],
                         "registeredSecondsBeyond": regl["registered"]["secondsBeyondLimit"], "halfSplit": half})
        hs_norm = [h["normM"] for r in rows for h in r["halfSplit"]]
        hs_lat = [h["lateralMedianM"] for r in rows for h in r["halfSplit"]]
        hs_lat95 = [h["lateralP95M"] for r in rows for h in r["halfSplit"]]
        hs_al = [h["alongMedianM"] for r in rows for h in r["halfSplit"]]
        ant = sens["races"][race]["antennaSensitivity"]
        base = np.asarray(ant["right=0,forward=0"])
        out[race] = {
            "sessionOffsetM": reg["sessionOffsetMeters"],
            "laps": rows,
            "halfSplitDeviationM": {"norm": {"median": float(np.median(hs_norm)), "max": float(np.max(hs_norm))},
                                    "lateralMedianAlongLap": {"median": float(np.median(hs_lat)), "max": float(np.max(hs_lat))},
                                    "lateralP95AlongLap": {"median": float(np.median(hs_lat95)), "max": float(np.max(hs_lat95))},
                                    "alongMedianAlongLap": {"median": float(np.median(hs_al)), "max": float(np.max(hs_al))}},
            "antennaSensitivityShiftM": {k: float(np.hypot(*(np.asarray(v) - base))) for k, v in ant.items()},
            "surfaceSensitivityShiftM": float(np.hypot(*(np.asarray(sens["races"][race]["surfaceSensitivity"]["1"]) -
                                                       np.asarray(sens["races"][race]["surfaceSensitivity"]["0"])))),
        }
    # shipped vs kerb-contact candidate (7/30 only)
    race = "fuji_aim_2020_07_30"
    cand = load(RACES / race / "gps_registration_kerb.json")
    ship = load(RACES / race / "gps_registration.json")
    lapdata = dict(laps(race))
    rows = []
    for c in cand["laps"]:
        s = next(x for x in ship["laps"] if x["lap"] == c["lap"])
        d = np.asarray(c["offsetMeters"]) - np.asarray(s["offsetMeters"])
        a, l = project(d, tangents(lapdata[c["lap"]]))
        rows.append({"lap": c["lap"], "vectorM": d.round(3).tolist(), "normM": float(np.hypot(*d)),
                     "lateralMedianM": float(np.median(l)), "lateralP95M": q(l, 95),
                     "alongMedianM": float(np.median(a)), "alongP95M": q(a, 95)})
    out["shippedVsKerbCandidate_0730"] = {
        "laps": rows,
        "normM": {"median": float(np.median([r["normM"] for r in rows])), "max": float(np.max([r["normM"] for r in rows]))},
        "lateralMedianM": {"median": float(np.median([r["lateralMedianM"] for r in rows])), "max": float(np.max([r["lateralMedianM"] for r in rows]))},
        "lateralP95M": {"median": float(np.median([r["lateralP95M"] for r in rows])), "max": float(np.max([r["lateralP95M"] for r in rows]))},
        "feasibleRegion": cand["feasibleRegion"]["session"],
        "uncertaintyStatement": cand["uncertainty"],
        "gpsAnomalies": cand["gpsAnomalies"],
    }
    ev = load(ART / "improvement-eval-2026-10-02" / "after" / "channel-timing-iter2" / "registration-candidate" / "evaluation.json")
    out["kerbLabelConsistency_0730"] = {
        "definition": ev["definitions"]["contradiction"],
        "shipped": {k: ev["heldOut"]["shipped"]["allLabels"][k] for k in ("n", "contradictionCount", "contradictions", "auc", "strongMedianG", "silentMedianG")},
        "raw": {k: ev["heldOut"]["raw"]["allLabels"][k] for k in ("n", "contradictionCount", "auc")},
        "candidateLOKO": {k: ev["anomalyTreatment"]["exclude"]["LOKO"]["allLabels"][k] for k in ("n", "contradictionCount", "contradictions", "auc")},
        "toleranceSensitivityShipped": {k: v["shipped"] for k, v in ev["toleranceSensitivity"].items()},
        "wet0729": ev["wet0729"]["reason"],
    }
    return out


def channel_timing():
    d = load(ART / "improvement-eval-2026-10-02" / "eval" / "iter2" / "lags_vs_0729.json")["results"]["sg"]
    raw29 = d["rawLags"]["0729"]
    iter2 = d["summary"]["iter2"]
    per = iter2["perLapMinus0729"]
    e = load(ART / "improvement-eval-2026-10-02" / "eval" / "iter2" / "evaluation-iter2.json")
    def rng(key):
        vals = [per[L][key] for L in per]
        return {"min": min(vals), "max": max(vals), "sessionMedian": iter2["sessionMedianMinus0729"][key]}
    return {
        "0729_clockPairs_perLapLagS": {L: {"accx_along": raw29[L]["accx_along"][0], "accy_lateral": raw29[L]["accy_lateral"][0]} for L in raw29},
        "0729_physicalPairsMedianLagS": {"brake_decel": d["summary"]["ref0729MedianLagS"]["brake_decel"],
                                         "steer_courseRate": d["summary"]["ref0729MedianLagS"]["steer_courseRate"]},
        "0730_minus_0729_iter2": {k: rng(k) for k in ("accx_along", "accy_lateral", "brake_decel", "steer_courseRate")},
        "videoOverlayPedalAfterRecordS": {"0729": e["video_iter2"]["0729"], "0730": e["video_iter2"]["0730"],
                                          "0730_minus_0729": e["video_iter2"]["0730_minus_0729"]},
        "videoYawVsLoggerPerLapS": {race: [{"lap": L["lap"], "gpsCourseRateAfterVideoS": L["gpsCourseRateAfterVideoS"],
                                            "imuAccyOverSpeedAfterVideoS": L["imuAccyOverSpeedAfterVideoS"]}
                                           for L in e["video_raw"][race]["laps"]] for race in e["video_raw"]},
        "videoYawVsLoggerMedianS": {race: e["video_raw"][race]["medians"] for race in e["video_raw"]},
    }


def viewer_pose():
    s = load(ART / "improvement-eval-2026-10-02" / "eval" / "c1c2-timebase-pose-kerbui-r2" / "summary.json")
    m = s["measuredByThisEvaluator"]
    after = m["poseLag_60fps_1x_10laps_83631frames_yawOnly_independentPython"]["after_2x70ms_lookahead140ms"]
    def lat_at(deg, dist):
        return dist * math.tan(math.radians(deg))
    return {
        "headingErrDeg": after["headingErrDeg"],
        "headingVsDrawnPathTangentDeg": after["headingVsDrawnPathTangentDeg"],
        "lateralShiftAt30mFromHeadingErrM": {k: round(lat_at(v, 30), 3) for k, v in after["headingErrDeg"].items()},
        "lateralShiftAt30mFromHeadingVsTangentM": {k: round(lat_at(v, 30), 3) for k, v in after["headingVsDrawnPathTangentDeg"].items()},
        "lapClock": m["lapClock_10laps"],
        "ghostPositionAbsErrM": m["ghostDelta"]["selfPairTruthTest_120pairs_truth0"]["ghostPositionAbsErrM"]["after"],
    }


def kerb_ortho():
    d = load(ART / "improvement-eval-2026-10-02" / "eval" / "kerb-geometry-ortho" / "results.json")
    return {"pooledAfter": {"paint": d["pooled"]["after_paint_excl_DunlopR_before"], "block": d["pooled"]["after_block_excl_DunlopR_before"]},
            "pooledBefore": {"paint": d["pooled"]["before_paint_excl_DunlopR_before"], "block": d["pooled"]["before_block_excl_DunlopR_before"]},
            "notSurveyTruth": d["not_survey_truth"]}


def road_repair():
    p = ART / "video-cg-alignment-2026-10-03" / "sol-road-mesh-audit.json"
    q2 = ART / "video-cg-alignment-2026-10-03" / "evidence" / "final-road-camera-effect.json"
    out = {"beforeRepairMaxOvershootM": {"k16": 0.901, "k19": 1.065, "k21": 1.539},
           "source": "docs/fuji-video-cg-alignment-review-2026-10-03.md table; WORKLOG 2026-10-02 20:42 (0.90/1.07/1.54 m, 8.9 m2)"}
    if p.exists():
        d = load(p)
        out["meshAuditKeys"] = list(d.keys())[:20]
    if q2.exists():
        out["finalRoadCameraEffectKeys"] = list(load(q2).keys())[:20]
    return out


def main():
    speeds = speed_stats()
    timing_s = {
        "frameQuantisationHalf_1over60": 1 / 60,
        "frameQuantisationFull_1over30": 1 / 30,
        "videoLapMapping_typical": 0.03,
        "videoLapMapping_worst_0730L5": 0.09,
        "decodedFrameOffset_0730scenes": 0.0287,
        "channelResidual_0729_maxClockPair": 0.044,
        "channelResidual_0730_accel": 0.024,
        "channelResidual_0730_turn": 0.031,
        "channelResidual_0730_brake": 0.004,
        "videoYawLag_0729_median": 0.185,
        "videoYawLag_0730_median": 0.235,
        "videoYawLag_max": 0.375,
        "timeProbe_0.2s": 0.2,
    }
    px = load(HERE / "px_to_metres_summary.json")
    budget = {
        "generatedBy": "artifacts/position-gap-2026-10-09/I1-gap-budget/budget.py",
        "codeDataStates": {
            "committed": "origin/master 7ebf89b (= local HEAD): shipped GPS registration, 7/30 channel re-timing, t+delta lap clock, "
                         "look-ahead pose smoothing, kerb zones; default cockpit camera only; road mesh overshoot at k16/k19/k21 present.",
            "workingTree": "uncommitted on top of 7ebf89b: Codex estimated onboard camera (src/engine/cameras/OnboardCamera.ts, "
                           "onboardCameraMath.ts, ReplayScene/Hud/replayStore edits) and road-mesh repair at kerb ends "
                           "(src/engine/track/FujiCgStudy.ts). Public data (laps, registration) unchanged vs 7ebf89b.",
        },
        "speeds": speeds,
        "alongTrackMetresPerTimingTerm": along_table(timing_s, speeds),
        "timerReads": timer_read_scatter(),
        "comparisonManifestTiming": manifest_timing(),
        "channelTiming": channel_timing(),
        "registration": registration_evidence(),
        "viewerPose": viewer_pose(),
        "kerbVsOrtho": kerb_ortho(),
        "roadRepair": road_repair(),
        "videoCgResidualMetres": {
            "heldout10_frozen": {k: px["pooled"]["heldout10|frozen_h1.25"][k] for k in (
                "medianOfSceneMedianPx", "medianOfSceneMedianNaiveM", "minimum3dDisplacementPointsAll",
                "conditionedRule", "conditionedPoints", "unconditionedPoints", "conditionedSceneMedianLateralM",
                "conditionedMedianOfSceneMediansM", "conditionedMaxSceneMedianM", "conditionedPointsLateral",
                "conditionedPointsPick4px", "medianOfSceneMedianLateralM", "sceneMedianLateralM", "lateralOverNaive")},
            "heldout10_h1.00_condMedianOfSceneMedians": px["pooled"]["heldout10|h1.00"]["conditionedMedianOfSceneMediansM"],
            "heldout10_h1.50_condMedianOfSceneMedians": px["pooled"]["heldout10|h1.50"]["conditionedMedianOfSceneMediansM"],
            "sequence8_postselection": {k: px["pooled"]["sequence8_postselection|frozen_h1.25"][k] for k in (
                "conditionedSceneMedianLateralM", "conditionedMedianOfSceneMediansM", "conditionedMaxSceneMedianM",
                "medianOfSceneMedianNaiveM")},
            "reproductionMaxAbsDiffPx": px["reproductionOfPublishedPx"]["maxAbsDiffPx"],
        },
    }
    (HERE / "gap_budget.json").write_text(json.dumps(budget, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({k: budget[k] for k in ("speeds", "alongTrackMetresPerTimingTerm", "timerReads")}, indent=1))
    r = budget["registration"]
    for race in SESSIONS:
        print(race, json.dumps(r[race]["halfSplitDeviationM"]), json.dumps(r[race]["antennaSensitivityShiftM"]),
              "surface", round(r[race]["surfaceSensitivityShiftM"], 3))
        for L in r[race]["laps"]:
            print("  L", L["lap"], "offset", L["offsetM"], "lat/along med", round(L["offsetLateralMedianM"], 2), round(L["offsetAlongMedianM"], 2))
    print(json.dumps({k: v for k, v in r["shippedVsKerbCandidate_0730"].items() if k in ("laps", "normM", "lateralMedianM", "lateralP95M")}, indent=1))
    print(json.dumps(budget["viewerPose"], indent=1)[:1500])
    print(json.dumps(budget["comparisonManifestTiming"], indent=0)[:1500])


if __name__ == "__main__":
    main()
