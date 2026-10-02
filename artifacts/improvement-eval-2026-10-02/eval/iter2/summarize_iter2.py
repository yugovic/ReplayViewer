#!/usr/bin/env python3
"""Collect the iteration-2 evaluator results into evaluation-iter2.json (this folder).

Inputs (all produced in this folder by the evaluator, except the iteration-1 / baseline video numbers):
  audit-iter2.json        scripts/quality/audit-fuji-channel-timing.py on current public races, with both videos
  peaks_integrity.json    peaks_integrity.py
  lags_vs_0729.json       lags_vs_0729.py
  native_supplement.json  native_supplement.py
  ../timing-video/before-after-summary.json  iteration-1 evaluation (baseline + iter1 video numbers)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main() -> None:
    audit = load("audit-iter2.json")
    prev = json.loads((HERE.parent / "timing-video/before-after-summary.json").read_text(encoding="utf-8"))
    pk = load("peaks_integrity.json")
    lg = load("lags_vs_0729.json")
    ns = load("native_supplement.json")

    vid = audit["video"]
    def lap_rows(race):
        rows = vid[race]["laps"] if isinstance(vid[race], dict) and "laps" in vid[race] else vid[race]
        return rows
    out = {"task": "Independent evaluation of GPS-REG-08 iteration 2 (7/30 logger-channel re-timing from native XRK, shift referenced to 7/29)",
           "date": "2026-10-02", "inputsNote": "videos and XRK local only; no frames or personal paths stored"}

    # 1. video
    v = {}
    for race in ("fuji_aim_01", "fuji_aim_2020_07_30"):
        v[race] = vid[race]
    out["video_raw"] = v
    out["video_medians"] = {
        "throttleD": {"0729": prev["medians"]["throttleD"]["0729"], "0730_baseline": prev["medians"]["throttleD"]["0730_before"],
                      "0730_iter1": prev["medians"]["throttleD"]["0730_after"]},
        "brakeD": {"0729": prev["medians"]["brakeD"]["0729"], "0730_baseline": prev["medians"]["brakeD"]["0730_before"],
                   "0730_iter1": prev["medians"]["brakeD"]["0730_after"]},
        "imuAccyOverSpeedAfterVideoYawS": {"0729": prev["medians"]["imuAccyOverSpeedAfterVideoYawS"]["0729"],
                                           "0730_baseline": prev["medians"]["imuAccyOverSpeedAfterVideoYawS"]["0730_before"],
                                           "0730_iter1": prev["medians"]["imuAccyOverSpeedAfterVideoYawS"]["0730_after"]},
        "gpsCourseRateAfterVideoYawS": {"0729": prev["medians"]["gpsCourseRateAfterVideoYawS"]["0729"],
                                        "0730_all_stages": prev["medians"]["gpsCourseRateAfterVideoYawS"]["0730_after"]},
    }
    out["video_medians_note"] = "0730_iter2 values: see audit-iter2.json video.<race>.median (copied below by hand from run-audit-iter2.log)"
    out["video_iter2"] = {
        "0729": {"throttleD": 0.100, "brakeD": 0.087, "imuAccyOverSpeedAfterVideoYawS": 0.215, "gpsCourseRateAfterVideoYawS": 0.185},
        "0730": {"throttleD": 0.098, "brakeD": 0.075, "imuAccyOverSpeedAfterVideoYawS": 0.285, "gpsCourseRateAfterVideoYawS": 0.235},
        "0730_perLap_throttleD": [0.12, 0.045, 0.11, 0.085, 0.215, 0.08],
        "0730_perLap_brakeD": [0.12, 0.045, 0.06, 0.075, 0.21, 0.075],
        "0730_perLap_imuAccyOverSpeedAfterVideoYawS": [0.205, 0.30, 0.30, 0.27, 0.235, 0.33],
        "0730_minus_0729": {"throttleD": -0.002, "brakeD": -0.012, "imuAccyOverSpeedAfterVideoYawS": 0.070,
                            "gpsCourseRateAfterVideoYawS(not re-timed)": 0.050,
                            "imuMinusGpsWithinVideoFrame": 0.020},
        "0730_excl_L5_median": {"throttleD": 0.085, "brakeD": 0.075},
        "reproducesImplementerAudit": "audit-iter2.json data+video sections identical to after/channel-timing-iter2/video-audit-iter2.json",
        "dataPart": {"0729": {"imuVsGpsMedianS": 0.014, "brakeToGpsDecelS": 0.103, "brakeToImuDecelS": 0.075},
                     "0730_baseline": {"imuVsGpsMedianS": -0.507, "brakeToGpsDecelS": 0.641, "brakeToImuDecelS": 0.080},
                     "0730_iter1": {"imuVsGpsMedianS": -0.008, "brakeToGpsDecelS": 0.151, "brakeToImuDecelS": 0.078},
                     "0730_iter2": {"imuVsGpsMedianS": 0.042, "brakeToGpsDecelS": 0.099, "brakeToImuDecelS": 0.076}},
    }
    # 2. peaks
    out["peaks"] = {
        "perLap": {k: {ch: {s: {m: pk["peaks"][k][ch][s][m] for m in pk["peaks"][k][ch][s]} for s in ("baseline", "iter1", "iter2")}
                       for ch in ("accx", "accy")} for k in pk["peaks"]},
        "aggregates": {ch: pk["peakAggregates"][ch] for ch in ("accx", "accy")},
        "nativeEnvelope": pk.get("nativeXrk"), "nativeAggregates": pk.get("nativeAggregates"),
        "decimationPhaseSpread": ns["decimation"],
    }
    # 3. lags
    out["lags"] = {m: {"ref0729MedianLagS": lg["results"][m]["summary"]["ref0729MedianLagS"],
                       **{s: lg["results"][m]["summary"][s] for s in ("baseline", "iter1", "iter2")}} for m in lg["results"]}
    out["lags"]["yawGyroRawXrk"] = {kk: vv for kk, vv in ns.items() if kk.startswith("yawGyro_")}
    # 4. integrity
    out["integrity"] = {"gps": pk["gpsIntegrity"], "registration0730": pk["registration0730"],
                        "wet0729UnchangedVsHead": pk["wet0729UnchangedVsHead"],
                        "handoffVerify": (HERE / "handoff-verify.log").read_text(encoding="utf-8").strip().splitlines(),
                        "vitestSrc": "1 failed / 372 passed (src/replay/apexKpi.test.ts expects 3 shipped label contradictions; now 4: new '2:k23:strong-but-off'); see vitest.log"}
    (HERE / "evaluation-iter2.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", HERE / "evaluation-iter2.json")


if __name__ == "__main__":
    main()
