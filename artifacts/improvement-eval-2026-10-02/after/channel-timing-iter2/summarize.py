#!/usr/bin/env python3
"""Before / iteration 1 / iteration 2 summary of the 7/30 logger-channel re-timing (GPS-REG-08).

Inputs (repo files):
  baseline  artifacts/improvement-eval-2026-10-02/baseline/public/data/races/fuji_aim_2020_07_30/  (unshifted)
  iter1     artifacts/improvement-eval-2026-10-02/after/channel-timing/candidates/  (CSV-linear, IMU lag -> 0)
  iter2     public/data/races/fuji_aim_2020_07_30/  (XRK-native, referenced to 7/29)
  align-report.json next to this script (lags per channel pair, written by align-fuji-channel-timing.py)
  kerb_contacts.json / apex_kpi.json / gps_registration_kerb.json of iteration 1 from --iter1-public
  (a copy of public/data/races taken before iteration 2; optional)
The 7/30 XRK ($FUJI_DRY_XRK, local, not published) gives the raw 50 Hz IMU peaks and the
sampling-phase envelope of the 10 Hz lap maximum. Writes summary.json next to this script.

  python artifacts/improvement-eval-2026-10-02/after/channel-timing-iter2/summarize.py [--iter1-public DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RACE = "fuji_aim_2020_07_30"
SETS = {
    "baseline": ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races" / RACE,
    "iter1": ROOT / "artifacts/improvement-eval-2026-10-02/after/channel-timing/candidates",
    "iter2": ROOT / "public/data/races" / RACE,
}
GPS_KEYS = ("t", "lat", "lng", "speed", "dist")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native(xrk_path: str):
    from libxrk import aim_xrk

    log = aim_xrk(xrk_path)
    out = {}
    for name in ("InlineAcc", "LateralAcc"):
        table = log.channels[name]
        out[name] = (table.column("timecodes").to_numpy(zero_copy_only=False).astype(np.int64),
                     table.column(name).to_numpy(zero_copy_only=False).astype(np.float32))
    return out


def phase_envelope(series, tc0: int, t_end: float, shift: float) -> dict:
    """Lap max |value| of 10 Hz point samples over the 100 sampling phases (1 ms steps), sampled like
    convert_aim + build_race (libxrk linear interpolation cast to float32, round 5 then 4)."""
    tc, v = series
    k = np.arange(0, int(round(1000 * t_end)) + 1, 100)
    maxima = []
    for phase in range(100):
        x = tc0 - 1000 * shift + k + phase
        vals = np.interp(x, tc, v.astype(float)).astype(np.float32).astype(float)
        maxima.append(float(np.abs(np.round(np.round(vals, 5), 4)).max()))
    return {"min": round(min(maxima), 4), "median": round(statistics.median(maxima), 4), "max": round(max(maxima), 4),
            "_all": maxima}


def rank_pct(value: float, maxima: list[float]) -> float:
    """Percent of the 100 sampling phases whose lap maximum is <= value (ties count half)."""
    below = sum(m < value - 1e-9 for m in maxima)
    equal = sum(abs(m - value) <= 1e-9 for m in maxima)
    return round(100.0 * (below + 0.5 * equal) / len(maxima), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iter1-public", type=Path, default=None, help="copy of public/data/races before iteration 2")
    args = ap.parse_args()
    report = load(HERE / "align-report.json")
    index = load(SETS["iter2"] / "laps.json")
    files = [s["data_file"] for s in index["selected"]]
    laps = {name: {int(load(d / f)["meta"]["lap"]): load(d / f) for f in files} for name, d in SETS.items()}
    xrk = os.environ.get("FUJI_DRY_XRK")
    series = native(xrk) if xrk else None

    integrity = {}
    for lap, new in laps["iter2"].items():
        integrity[str(lap)] = all(new[k] == laps["baseline"][lap][k] == laps["iter1"][lap][k] for k in GPS_KEYS)

    peaks = {}
    for lap in sorted(laps["iter2"]):
        rep = next(r for r in report["laps"] if r["lap"] == lap)
        tc0 = laps["iter2"][lap]["meta"]["channel_time_alignment"]["source"]["lapStartTimecodeMs"]
        t_end = laps["iter2"][lap]["t"][-1]
        row = {}
        for axis, name in (("accx", "InlineAcc"), ("accy", "LateralAcc")):
            vals = {k: round(float(np.abs(np.asarray(laps[k][lap][axis], float)).max()), 4) for k in SETS}
            base = vals["baseline"]
            entry = {**{f"{k}MaxAbsG": v for k, v in vals.items()},
                     "iter1ChangePct": round(100 * (vals["iter1"] / base - 1), 1),
                     "iter2ChangePct": round(100 * (vals["iter2"] / base - 1), 1)}
            for k in SETS:
                rms = float(np.sqrt(np.mean(np.asarray(laps[k][lap][axis], float) ** 2)))
                entry[f"{k}RmsG"] = round(rms, 4)
            if series is not None:
                shifts = {"baseline": 0.0, "iter1": laps["iter1"][lap]["meta"]["channel_time_alignment"]["shiftSeconds"],
                          "iter2": rep["shiftSeconds"]}
                env = {k: phase_envelope(series[name], tc0, t_end, s) for k, s in shifts.items()}
                entry["insideEnvelope"] = {k: env[k]["min"] - 1e-9 <= vals[k] <= env[k]["max"] + 1e-9 for k in SETS}
                entry["phaseRankPct"] = {k: rank_pct(vals[k], env[k]["_all"]) for k in SETS}
                entry["tenHzPhaseEnvelope"] = {k: {x: y for x, y in e.items() if x != "_all"} for k, e in env.items()}
                entry["native50HzMaxAbsG"] = rep["peaks"]["native50HzLapWindow"][f"maxAbs{axis.capitalize()}G"]
            row[axis] = entry
        peaks[str(lap)] = row

    def pct(key):
        return [peaks[l][a][key] for l in peaks for a in ("accx", "accy")]

    residual = {}
    for stage, key in (("baseline", "unaligned"), ("iter1", "shippedBefore"), ("iter2", "aligned")):
        by_pair = {p: [] for p in report["reference"]["medianLagS"]}
        for r in report["laps"]:
            for p, v in r["residualVs0729"][key]["byPair"].items():
                by_pair[p].append(v)
        residual[stage] = {p: round(statistics.median(v), 3) for p, v in by_pair.items()}
        residual[stage]["medianOverPairsOfLapMedians"] = round(statistics.median(
            r["residualVs0729"][key]["medianS"] for r in report["laps"]), 3)

    dependents = {}
    if args.iter1_public:
        for race in ("fuji_aim_01", RACE):
            old = load(args.iter1_public / race / "kerb_contacts.json")
            new = load(ROOT / "public/data/races" / race / "kerb_contacts.json")
            changes = []
            for lap, rows in old["laps"].items():
                before = {r["kerb"]: r for r in rows}
                for r in new["laps"][lap]:
                    b = before[r["kerb"]]
                    if (b["class"], b["bin"]) != (r["class"], r["bin"]):
                        changes.append({"lap": int(lap), "kerb": r["kerb"], "before": [b["class"], b["bin"], b["peakRollRms"]],
                                        "after": [r["class"], r["bin"], r["peakRollRms"]]})
            counts = {}
            for doc, name in ((old, "iter1"), (new, "iter2")):
                c = {"strong": 0, "silent": 0, "mild": 0}
                for rows in doc["laps"].values():
                    for r in rows:
                        c[r["class"]] += 1
                counts[name] = c
            entry = {"kerbContactShiftSecondsByLap": {"iter1": old["timeBase"]["shiftSecondsByLap"],
                                                      "iter2": new["timeBase"]["shiftSecondsByLap"]},
                     "labelCounts": counts, "labelChanges": changes}
            for name, stage_dir in (("iter1", args.iter1_public / race), ("iter2", ROOT / "public/data/races" / race)):
                kpi = load(stage_dir / "apex_kpi.json")
                flagged = sorted(f"{lap}:{r['kerb']}:{r['labelCheck']}" for lap, rows in kpi["laps"].items() for r in rows
                                 if r.get("labelCheck") not in (None, "ok"))
                entry.setdefault("apexKpiLabelCheckFlags", {})[name] = flagged
            if race == RACE:
                for name, stage_dir in (("iter1", args.iter1_public / race), ("iter2", ROOT / "public/data/races" / race)):
                    reg = load(stage_dir / "gps_registration.json")
                    kerb = load(stage_dir / "gps_registration_kerb.json")
                    entry.setdefault("registrationOffsets", {})[name] = {
                        "shipped": {str(l["lap"]): l["offsetMeters"] for l in reg["laps"]},
                        "kerbCandidate": {str(l["lap"]): l["offsetMeters"] for l in kerb["laps"]},
                        "kerbCandidateSession": kerb["sessionOffsetMeters"],
                        "kerbCandidateUnresolved": [f"{u['lap']}:{u['kerb']}:{u['class']}" for u in kerb["unresolvedLabels"]],
                        "kerbCandidateLabelSummary": kerb["labelSummary"]}
            if race == RACE:
                evals = {"iter1": ROOT / "artifacts/improvement-eval-2026-10-02/after/registration-candidate/evaluation.json",
                         "iter2": HERE / "registration-candidate/evaluation.json"}
                if all(path.is_file() for path in evals.values()):
                    docs = {k: load(path) for k, path in evals.items()}
                    entry["kerbCandidateHeldOut"] = {
                        "source": {k: str(path.relative_to(ROOT)).replace("\\", "/") for k, path in evals.items()},
                        "metric": "[contradictions, AUC] of the published labels (strong/silent) at the evaluated offsets",
                        **{cfg: {sub: {k: [docs[k]["heldOut"][cfg][sub]["contradictionCount"], docs[k]["heldOut"][cfg][sub]["auc"]]
                                       for k in docs}
                                 for sub in ("allLabels", "withoutAnomalyLabels")}
                           for cfg in ("raw", "shipped", "trackOnlySessionPlusLap", "candidateLOKO", "candidateLOLO")}}
            dependents[race] = entry

    video = None
    audits = {"baseline": ROOT / "artifacts/improvement-eval-2026-10-02/eval/timing-video/audit-before.json",
              "iter1": ROOT / "artifacts/improvement-eval-2026-10-02/eval/timing-video/audit-after.json",
              "iter2": HERE / "video-audit-iter2.json"}
    if all(path.is_file() for path in audits.values()):
        docs = {k: load(path) for k, path in audits.items()}
        video = {
            "tool": "scripts/quality/audit-fuji-channel-timing.py (SmartyCam overlay THROTTLE/BRAKE bars and windshield "
                    "optical-flow yaw vs the shipped channels; local videos, no frames stored)",
            "source": {k: str(path.relative_to(ROOT)).replace(chr(92), "/") for k, path in audits.items()},
            "reference0729": docs["iter2"]["video"]["fuji_aim_01"]["medians"],
            "0730": {k: d["video"][RACE]["medians"] for k, d in docs.items()},
            "0730PerLapOverlayBrakeAfterRecordedS": {
                k: {str(l["lap"]): l["overlayBrakeAfterRecordedS"] for l in d["video"][RACE]["laps"]} for k, d in docs.items()},
            "dataAudit": {k: {"imuVsGpsMedianShiftS": {r: d["data"][r]["imuVsGpsMedianShiftS"] for r in d["data"]},
                              "gpsDecelAfterBrakeMedianS": {r: d["data"][r]["gpsDecelAfterBrakeMedianS"] for r in d["data"]}}
                          for k, d in docs.items()},
            "note": ("overlay*AfterRecordedS compares the logger data burned into the video with the shipped channel on the "
                     "lap clock: equal to 7/29 = same logger-vs-lap timing. gpsCourseRateAfterVideoS does not depend on the "
                     "re-timing; imuAccyOverSpeedAfterVideoS minus it is the IMU-vs-GPS relation seen through the video yaw."),
        }

    summary = {
        "task": "GPS-REG-08 iteration 2 (FIX-DATA): 7/30 logger-clock channels re-timed from the native XRK with shifts "
                "referenced to the 7/29 session",
        "stages": {
            "baseline": "unshifted (before any re-timing)",
            "iter1": "per-lap shift = -(mean accx/accy IMU-vs-GPS lag) [IMU lag -> 0]; linear interpolation of the 10 Hz CSV",
            "iter2": ("per-lap shift = -median over 5 channel pairs of (lag_7/30 - median lag of the pair over the 7/29 laps); "
                      "values re-sampled from the native XRK series (convert_aim replay on the delayed 10 Hz grid)"),
        },
        "shiftSecondsByLap": {
            "iter1": {str(l): d["meta"]["channel_time_alignment"]["shiftSeconds"] for l, d in sorted(laps["iter1"].items())},
            "iter2": {str(l): d["meta"]["channel_time_alignment"]["shiftSeconds"] for l, d in sorted(laps["iter2"].items())},
        },
        "reference0729MedianLagS": report["reference"]["medianLagS"],
        "residualVs0729SessionMedianS": residual,
        "residualNote": ("residual = lag of the 7/30 lap minus the 7/29 median of the same pair; 0 = 7/30 channels relate to "
                         "GPS like 7/29. The pairs of one lap disagree by 0.04-0.10 s (accel pairs higher, course-rate pairs "
                         "lower); the per-lap median is the brake pair in all six laps."),
        "nativeSelfCheck": {"shift0ReproducesEveryCsvRowAndUnalignedLapBytes": report["checks"]["nativeShift0ReproducesCsvAndUnalignedLap"],
                            "unalignedRebuildEqualsBaselineSnapshot": all(v["identicalBytes"] for v in report["unalignedVsBaselineSnapshot"]["laps"].values())},
        "gpsArraysIdenticalAllStages": integrity,
        "peaks": peaks,
        "peakSummary": {
            "iter1ChangePctRange": [min(pct("iter1ChangePct")), max(pct("iter1ChangePct"))],
            "iter2ChangePctRange": [min(pct("iter2ChangePct")), max(pct("iter2ChangePct"))],
            "iter1ChangePctMean": round(float(np.mean(pct("iter1ChangePct"))), 1),
            "iter2ChangePctMean": round(float(np.mean(pct("iter2ChangePct"))), 1),
            "note": ("A 10 Hz lap maximum of the 50 Hz IMU depends on the sampling phase: tenHzPhaseEnvelope gives its range "
                     "over the 100 possible 1 ms phases of each stage's window and phaseRankPct where the shipped value sits "
                     "(50 = typical point sample). Baseline and iteration 2 are point samples of the native series; "
                     "iteration 1 interpolated between two 10 Hz samples and sits at the bottom of (or below) the envelope."),
        },
        "videoCheck": video,
        "dependents": dependents or None,
        "evidence": {"alignReport": "artifacts/improvement-eval-2026-10-02/after/channel-timing-iter2/align-report.json",
                     "alignReportSha256": sha(HERE / "align-report.json")},
    }
    if series is not None:
        inside = {k: sum(peaks[l][a]["insideEnvelope"][k] for l in peaks for a in ("accx", "accy")) for k in SETS}
        summary["peakSummary"]["insideTenHzPhaseEnvelope"] = {k: f"{v}/{2 * len(peaks)}" for k, v in inside.items()}
        summary["peakSummary"]["meanPhaseRankPct"] = {
            k: round(float(np.mean([peaks[l][a]["phaseRankPct"][k] for l in peaks for a in ("accx", "accy")])), 1) for k in SETS}
    (HERE / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("shiftSecondsByLap", "residualVs0729SessionMedianS", "peakSummary")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
