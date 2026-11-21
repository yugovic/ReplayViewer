"""Build the I1 component table (gap_budget_table.json) from gap_budget.json + px_to_metres_summary.json.
Every numeric value is read from those files or from the cited evidence; text fields state scope/grade/state.
Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
B = json.loads((HERE / "gap_budget.json").read_text("utf-8"))
P = json.loads((HERE / "px_to_metres_summary.json").read_text("utf-8"))
A = B["alongTrackMetresPerTimingTerm"]
S = B["speeds"]
REG = B["registration"]
CT = B["channelTiming"]
VP = B["viewerPose"]
HO = P["pooled"]["heldout10|frozen_h1.25"]
SQ = P["pooled"]["sequence8_postselection|frozen_h1.25"]

COMMITTED = "committed 7ebf89b"
WT = "uncommitted working tree (Codex onboard camera + road repair) on 7ebf89b"
BOTH = "same in committed 7ebf89b and working tree (public data unchanged)"


def r(x, n=2):
    return None if x is None else round(float(x), n)


def m_at(term):
    a = A[term]
    return {"s": r(a["seconds"], 4), "m@60": a["60kmh"], "m@80": a["80kmh"], "m@100": a["100kmh"],
            "m@median_0729": a["fuji_aim_01_medianSpeed"], "m@median_0730": a["fuji_aim_2020_07_30_medianSpeed"],
            "m@top_0729": a["fuji_aim_01_topSpeed"], "m@top_0730": a["fuji_aim_2020_07_30_topSpeed"]}


rows = []


def add(group, name, direction, value, unit, scope, grade, evidence, state, note=""):
    rows.append({"group": group, "component": name, "direction": direction, "value": value, "unit": unit,
                 "scope": scope, "grade": grade, "evidence": evidence, "state": state, "note": note})


# ---------- 1. timing ----------
add("1 timing", "7/29 logger-clock channels vs GPS (clock pairs accx/accy)", "along (HUD/pedal/brake-lamp vs car, not car position)",
    {"perLapS": CT["0729_clockPairs_perLapLagS"], "maxAbsS": 0.044, "medianS": "~0 (accx -0.004, accy +0.002)",
     "metres": m_at("channelResidual_0729_maxClockPair")},
    "s / m", "fuji_aim_01 L1-L4", "measured",
    "artifacts/improvement-eval-2026-10-02/eval/iter2/lags_vs_0729.json (sg rawLags 0729)", BOTH,
    "Physical pairs brake->decel -0.13 s and steer->course -0.05 s are vehicle dynamics, not clock error.")
add("1 timing", "7/30 channels after native re-timing, residual vs 7/29 relation", "along (HUD/pedal vs car)",
    {"sessionMedianS": {k: v["sessionMedian"] for k, v in CT["0730_minus_0729_iter2"].items()},
     "perLapRangeS": {k: [v["min"], v["max"]] for k, v in CT["0730_minus_0729_iter2"].items()},
     "metres_accel0.024": m_at("channelResidual_0730_accel"), "metres_turn0.031": m_at("channelResidual_0730_turn"),
     "metres_brake0.004": m_at("channelResidual_0730_brake"),
     "videoOverlayPedalCheck": {"brake_0730_minus_0729_s": CT["videoOverlayPedalAfterRecordS"]["0730_minus_0729"]["brakeD"],
                                "throttle_0730_minus_0729_s": CT["videoOverlayPedalAfterRecordS"]["0730_minus_0729"]["throttleD"],
                                "resolution_s": "+-0.03..0.05"}},
    "s / m", "fuji_aim_2020_07_30 L1-L6", "measured",
    "artifacts/improvement-eval-2026-10-02/eval/iter2/lags_vs_0729.json, evaluation-iter2.json", "committed d9a1ea9 (in 7ebf89b)",
    "Residuals are below the video-overlay check resolution; 7/30 L5 pedal overlay 0.21 s vs ~0.08 s others = mapping outlier.")
add("1 timing", "Video PTS <-> lap time mapping (overlay LAP timer reads)", "along (car position)",
    {"withinLapReadDeviation": {k: v["withinLapReadDeviationS"] for k, v in B["timerReads"].items()},
     "comparison13Scenes_overlayMinusRequested_maxAbsS": B["comparisonManifestTiming"]["maxOverlayTimerDifferenceSeconds"],
     "overlayUpdateGranularityS": "~0.05 (reads trail 0-50 ms)",
     "outlier_0730L5_correctionS": 0.09,
     "metres_typical0.03": m_at("videoLapMapping_typical"), "metres_L5_0.09": m_at("videoLapMapping_worst_0730L5")},
    "s / m", "both sessions, all laps (timer reads at 10/50/90 % of each lap); 13 comparison scenes", "measured",
    "artifacts/video-position-gap-2026-10-02/video-timer-reads.json; artifacts/onboard-comparison-2026-10-03/manifest.json", BOTH)
add("1 timing", "Frame quantisation and decoded-frame offset", "along",
    {"halfFrame_1over60": m_at("frameQuantisationHalf_1over60"), "fullFrame_1over30": m_at("frameQuantisationFull_1over30"),
     "decodedMinusRequested_13scenes_s": [x["decodedMinusRequestedS"] for x in B["comparisonManifestTiming"]["rows"]],
     "decodedOffsetAlong_13scenes_m": [x["decodedOffsetAlongM"] for x in B["comparisonManifestTiming"]["rows"]]},
    "s / m", "13 comparison scenes (7/30 scenes: video frame 0.0287 s later than the CG time)", "measured",
    "artifacts/onboard-comparison-2026-10-03/manifest.json", "comparison artefact (fixable by sampling CG at decoded PTS)")
add("1 timing", "Video image vs logger data lag (window-flow yaw proxy vs GPS course rate / IMU ay/v)", "along (if it is a timing offset: replay behind video)",
    {"gpsCourseRateAfterVideoYaw_medianS": {"0729": 0.185, "0730": 0.235}, "perLapRangeS": [0.04, 0.375],
     "imuAyOverV_AfterVideoYaw_medianS": {"0729": 0.215, "0730": 0.285},
     "metres_0.185": m_at("videoYaw_0729_median") if "videoYaw_0729_median" in A else m_at("videoYawLag_0729_median"),
     "metres_0.235": m_at("videoYawLag_0730_median"),
     "probe_pm0.2s": "held-out 10 scenes: +0.2 s improved 5/worsened 5 (mean 15.20->15.07 px); diagnostic sequence: +0.2 s worse in 6/8"},
    "s / m", "both sessions, per lap", "unknown (measured lag; its split into overlay latency vs yaw/sideslip physics is not identified)",
    "artifacts/improvement-eval-2026-10-02/eval/iter2/evaluation-iter2.json; artifacts/video-cg-alignment-2026-10-03/evidence/time-diagnostic-evaluation.json, sequence/sequence-evaluation.json",
    BOTH, "GPS and IMU agree within +-0.04 s, so the ~0.2 s is between the video image and all logger data. Plausible timing share 0..0.24 s; "
          "a 0.08-0.09 s overlay latency is suggested by the pedal overlay but not proven for the timer itself. Not applied.")
add("1 timing", "Lap clock delta (first 10 Hz sample vs true lap start)", "along",
    {"before_m": VP["lapClock"]["carAheadOverLap_before_m"], "after_m": VP["lapClock"]["carAhead_after_m"], "deltaRangeS": VP["lapClock"]["deltaRange_s"]},
    "m", "all 10 laps", "measured", "artifacts/improvement-eval-2026-10-02/eval/c1c2-timebase-pose-kerbui-r2/summary.json", "committed d9a1ea9 (fixed)")

# ---------- 2. GPS lateral ----------
for race in ("fuji_aim_01", "fuji_aim_2020_07_30"):
    g = REG[race]
    add("2 GPS lateral", f"Registration internal variability (half-lap refit minus full-lap fit), {race}", "lateral (and along)",
        {"halfSplitNormM": g["halfSplitDeviationM"]["norm"], "lateralMedianAlongLapM": g["halfSplitDeviationM"]["lateralMedianAlongLap"],
         "lateralP95AlongLapM": g["halfSplitDeviationM"]["lateralP95AlongLap"],
         "jackknifeSigmaM": {L["lap"]: L["jackknifeSigmaM"] for L in g["laps"]},
         "registeredMaxBeyondLimitM": {L["lap"]: L["registeredMaxBeyondM"] for L in g["laps"]},
         "rawBiasRemoved_lateralMedianM": {L["lap"]: r(L["offsetLateralMedianM"]) for L in g["laps"]}},
        "m", race, "derived (conditional sensitivity, not absolute accuracy)",
        "artifacts/gps-accuracy-2026-09-22/registration/analysis-summary.json; public/data/races/*/gps_registration.json", BOTH)
k = REG["shippedVsKerbCandidate_0730"]
add("2 GPS lateral", "Shipped registration vs kerb-contact candidate (?gps=kerb), per lap", "lateral",
    {"lateralMedianM": k["lateralMedianM"], "lateralP95M": k["lateralP95M"], "normM": k["normM"],
     "perLap": {x["lap"]: {"lat_med": r(x["lateralMedianM"]), "along_med": r(x["alongMedianM"])} for x in k["laps"]}},
    "m", "fuji_aim_2020_07_30 L1-L6", "derived (disagreement of two plausible fits)",
    "public/data/races/fuji_aim_2020_07_30/gps_registration.json vs gps_registration_kerb.json", BOTH)
kc = REG["kerbLabelConsistency_0730"]
add("2 GPS lateral", "IMU kerb-contact label consistency with shipped registration", "lateral",
    {"labels": kc["shipped"]["n"], "contradictions": kc["shipped"]["contradictionCount"], "contradictionsDetail_lap_kerb_class_gMinM": kc["shipped"]["contradictions"],
     "auc": kc["shipped"]["auc"], "toleranceSensitivityShipped": kc["toleranceSensitivityShipped"],
     "rawGps": kc["raw"], "labelFeasibleWidthM": "0.4-0.7 (feasibleRegion)", "statedAbsoluteKpiIntervalM": 0.7,
     "basis": k["uncertaintyStatement"]["absoluteGapIntervalBasis"]},
    "m / count", "fuji_aim_2020_07_30 (58 labels); fuji_aim_01 has 1 strong label -> not bounded", "measured labels; interval = estimated lower bound",
    "artifacts/improvement-eval-2026-10-02/after/channel-timing-iter2/registration-candidate/evaluation.json; gps_registration_kerb.json", BOTH)
add("2 GPS lateral", "7/30 L2 place-dependent GPS error (anomaly sector)", "lateral",
    {"stationRangeM": [3250, 3900], "extentM": 650, "shareOfLap": round(650 / 4556.4, 3), "magnitudeM": "1-2 (k21 strong label at gMin +1.664 m with shipped registration; k24 silent -0.99 m in v3 report)",
     "comparisonScenesInside": ["2020-07-30-L2-94.000 (station 3432.9)", "sequence 93.0-95.0 s (3398-3469)"]},
    "m", "fuji_aim_2020_07_30 L2 only (the lap used for the 7/30 video comparison)", "derived from measured contradictions",
    "public/data/races/fuji_aim_2020_07_30/gps_registration_kerb.json gpsAnomalies; docs/fuji-apex-kerb-kpi-2026-10-02.md", BOTH)
add("2 GPS lateral", "Antenna lever arm (not measured; assumed at wheelbase centre)", "lateral/along",
    {"registrationShiftFor0.5mRightM": {race: r(REG[race]["antennaSensitivityShiftM"]["right=0.5,forward=0"], 3) for race in ("fuji_aim_01", "fuji_aim_2020_07_30")},
     "registrationShiftFor1mForwardM": {race: r(REG[race]["antennaSensitivityShiftM"]["right=0,forward=1"], 3) for race in ("fuji_aim_01", "fuji_aim_2020_07_30")},
     "kerbFitAntennaEstimateM": {"right": -0.02, "forward": 0.04, "costRisesAt": "+-0.4 m"}},
    "m", "both sessions", "estimated (sensitivity scenarios; lever arm unknown)",
    "artifacts/gps-accuracy-2026-09-22/registration/sensitivity-audit.json; docs/fuji-apex-kerb-kpi-2026-10-02.md", BOTH,
    "Forward offset is essentially unconstrained by the registration (<=0.02 m response to 1 m) -> drawn car/camera could be up to ~1 m along-track off.")

# ---------- 3. GPS along ----------
add("3 GPS along", "Registration along-track variability (half-lap refit)", "along",
    {race: REG[race]["halfSplitDeviationM"]["alongMedianAlongLap"] for race in ("fuji_aim_01", "fuji_aim_2020_07_30")},
    "m", "both sessions", "derived (conditional)", "artifacts/gps-accuracy-2026-09-22/registration/analysis-summary.json", BOTH)
add("3 GPS along", "GPS timing vs logger clock (GPS along-acc vs IMU accx, 7/29 reference)", "along",
    {"perLapLagS": {L: v["accx_along"] for L, v in CT["0729_clockPairs_perLapLagS"].items()}, "metres_0.044": m_at("channelResidual_0729_maxClockPair")},
    "s / m", "fuji_aim_01", "measured", "artifacts/improvement-eval-2026-10-02/eval/iter2/lags_vs_0729.json", BOTH,
    "Lap start GPS positions 138.03-138.08 m on all 10 laps (consistency only; lap beacon is GPS-based, not an absolute check).")
add("3 GPS along", "Absolute along-track GPS vs reality (braking point / S-F line vs video)", "along",
    {"value": "not measured"}, "m", "both", "unknown", "no landmark-crossing comparison exists", BOTH)

# ---------- 4. course ----------
ko = B["kerbVsOrtho"]["pooledAfter"]
add("4 course", "Kerb lines vs the 2019 ortho they were drawn from", "lateral",
    {"paintMedianM": ko["paint"]["median"], "paintP95absM": ko["paint"]["p95abs"], "blockMedianM": ko["block"]["median"], "blockP95absM": ko["block"]["p95abs"]},
    "m", "14 kerbs", "measured (same-image consistency, not survey)",
    "artifacts/improvement-eval-2026-10-02/eval/kerb-geometry-ortho/results.json", BOTH)
add("4 course", "Ortho georeferencing (VIRTUAL SHIZUOKA 2019, 20 cm)", "lateral/along",
    {"rangeM": [0.2, 0.3]}, "m", "whole course", "estimated (not measured)",
    "kerb-geometry-ortho/results.json not_survey_truth; gps_registration_kerb.json uncertainty", BOTH)
add("4 course", "Road mesh overshoot beyond kerb outer line at kerb ends", "lateral (rendering of road edge)",
    {"committed_maxM": B["roadRepair"]["beforeRepairMaxOvershootM"], "workingTree_maxM": 0.0, "removedAreaM2": 10.48},
    "m", "k16 2130-2134 m, k19 2716-2718 m, k21 3342-3348 m", "measured (same-source geometry)",
    "docs/fuji-video-cg-alignment-review-2026-10-03.md; artifacts/video-cg-alignment-2026-10-03/sol-road-mesh-audit.json",
    f"{COMMITTED}: present; {WT}: repaired", "Repair did not change the 98-point boundary residuals (final-road-camera-effect.json).")
add("4 course", "2019 imagery vs 2020 track state", "lateral", {"value": "not verified"}, "m", "whole course", "unknown", "-", BOTH)

# ---------- 5. rendering ----------
add("5 rendering", "Heading lag after look-ahead smoothing", "lateral at 30 m ahead",
    {"headingErrDeg": VP["headingErrDeg"], "lateralAt30mM": VP["lateralShiftAt30mFromHeadingErrM"]},
    "deg / m", "10 laps, 60 fps", "measured", "artifacts/improvement-eval-2026-10-02/eval/c1c2-timebase-pose-kerbui-r2/summary.json", "committed d9a1ea9")
add("5 rendering", "Drawn heading (GPS chord) vs drawn path tangent", "lateral at 30 m ahead",
    {"deg": VP["headingVsDrawnPathTangentDeg"], "lateralAt30mM": VP["lateralShiftAt30mFromHeadingVsTangentM"]},
    "deg / m", "10 laps", "measured", "same", BOTH)
add("5 rendering", "Body yaw vs course (sideslip) and suspension roll/pitch not rendered", "image rotation / lateral at depth",
    {"sideslipDeg": "~1-3 at the limit (typical FWD value, not measured)", "rollDeg": "~2-5 at 1.0-1.2 g (typical, not measured)",
     "pxAt300to500pxRadiusPer3degRoll": [16, 26],
     "heldOutResidualVsAbsLateralG_spearman": {"rho": 0.721, "p": 0.019, "n": 10, "note": "exploratory; scenes correlated; |ax| rank is the mirror (rho -0.721)"}},
    "deg / px", "all; worst at high lateral g", "estimated (magnitudes) / derived (correlation)",
    "px_to_metres_summary.json + artifacts/onboard-comparison-2026-10-03/manifest.json telemetry", BOTH)
add("5 rendering", "Car model placement (Mazda2 track/wheelbase, generic body)", "lateral",
    {"bodyWidthM": 1.695, "tyreOuterFrontM": 1.680, "tyreOuterRearM": 1.665, "tyreWidthAssumptionM": "0.185 (0.195 option -> +-0.005 per side)"},
    "m", "both", "measured spec / estimated fitment", "public/data/vehicles/mazda2-dj.json; gps_registration.json method.vehicleGeometry", BOTH,
    "Placement relative to the GPS point is dominated by the unknown antenna lever arm (see group 2).")
add("5 rendering", "Camera (a): default cockpit camera vs SmartyCam", "image",
    {"cockpit": {"eyeUpM": 0.78, "eyeRightM": 0.28, "eyeBackM": 0.55, "pitchDeg": 0, "vfovDeg": 58, "hfovDeg": 89},
     "smartyCamFit": {"upM": "1.25 (assumed)", "rightM": 0, "pitchDeg": 18.2, "hfovDeg": 107.1, "f_px": 472.3},
     "heldOutBoundaryResidualPx": {"medianOfSceneMedians": 133.88, "sceneRange": [121.3, 144.15]}, "apparentLateralFromEyeOffsetM": 0.28},
    "px / m", "10 held-out scenes", "measured px", "docs/fuji-video-cg-alignment-review-2026-10-03.md; evidence/independent-camera-evaluation.json",
    f"{COMMITTED} (only camera available there)", "Horizon at image centre vs y~205/720: the two views are not commensurable; a metric conversion is not meaningful.")
add("5 rendering", "Camera (b): Codex estimated onboard camera", "image",
    {"heldOutBoundaryResidualPx": {"medianOfSceneMedians": r(HO["medianOfSceneMedianPx"], 2), "worst": {"7/30 L2 94 s": 54.49, "7/30 L2 45 s": 33.31}},
     "heightSensitivity_f_px": {"1.0m": 487.1, "1.25m": 472.3, "1.5m": 466.9}, "rollSensitivityDeg": {"1.0m": 0.53, "1.25m": 1.84, "1.5m": 3.02}},
    "px", "10 held-out scenes (+8 post-selection)", "measured px; camera parameters estimated", "evidence/independent-camera-evaluation.json; camera-fit.json", WT)

# ---------- 6. px -> metres ----------
add("6 video-CG residual in metres", "Minimum 3D displacement explaining each pixel residual (px * depth / f)", "any direction perpendicular to the ray",
    {"medianOfSceneMediansM": r(HO["medianOfSceneMedianNaiveM"], 3), "pointsAll": HO["minimum3dDisplacementPointsAll"],
     "sceneRangeM": [0.06, 1.98]},
    "m", "10 held-out scenes, 98 points", "derived", "artifacts/position-gap-2026-10-09/I1-gap-budget/px_to_metres_summary.json", WT)
add("6 video-CG residual in metres", "Equivalent lateral ground offset of the boundary (ray-road-plane intersection, line orientation accounted)", "lateral",
    {"conditionedRule": HO["conditionedRule"], "conditionedPoints": HO["conditionedPoints"], "unconditionedPoints": HO["unconditionedPoints"],
     "sceneMedianM": HO["conditionedSceneMedianLateralM"], "medianOfSceneMediansM": r(HO["conditionedMedianOfSceneMediansM"], 3),
     "maxSceneMedianM": r(HO["conditionedMaxSceneMedianM"], 3), "pointsM": HO["conditionedPointsLateral"],
     "pick4pxNoiseFloorM": HO["conditionedPointsPick4px"], "lateralOverNaiveRatio": HO["lateralOverNaive"],
     "heightSensitivity_medOfMed": {"1.0m": r(P["pooled"]["heldout10|h1.00"]["conditionedMedianOfSceneMediansM"], 3),
                                    "1.5m": r(P["pooled"]["heldout10|h1.50"]["conditionedMedianOfSceneMediansM"], 3)},
     "sequence8_postselection": {"medianOfSceneMediansM": r(SQ["conditionedMedianOfSceneMediansM"], 3), "maxSceneMedianM": r(SQ["conditionedMaxSceneMedianM"], 3)},
     "note94s": "only 1/14 points convertible: picks lie near/above the CG horizon -> attitude (roll/pitch) mismatch, not a lateral offset"},
    "m", "10 held-out scenes (58/98 well-conditioned points)", "derived (camera height assumed; mixes GPS, attitude, lens, course)",
    "artifacts/position-gap-2026-10-09/I1-gap-budget/px_to_metres_summary.json, px_to_metres_points.json", WT)

out = {"generatedBy": "artifacts/position-gap-2026-10-09/I1-gap-budget/table.py", "rows": rows,
       "speedsKmh": {k: {"timeWeighted": v["timeWeightedKmh"], "cornerMinima": v["cornerMinimaKmh"]} for k, v in S.items()}}
(HERE / "gap_budget_table.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", "utf-8")
print(len(rows), "rows")
