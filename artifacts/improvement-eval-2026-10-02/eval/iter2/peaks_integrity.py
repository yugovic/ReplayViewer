#!/usr/bin/env python3
"""Iteration-2 evaluator check (independent): peaks, integrity, native-XRK provenance of the 7/30 re-timed channels.

Stages compared for fuji_aim_2020_07_30:
  baseline = artifacts/improvement-eval-2026-10-02/baseline/public/data/races/fuji_aim_2020_07_30 (before any re-timing)
  iter1    = artifacts/improvement-eval-2026-10-02/after/channel-timing/candidates (iteration-1 lap JSONs)
  iter2    = public/data/races/fuji_aim_2020_07_30 (current)

Native check needs APEX_WORK (env) pointing at a folder with xrk_<race>.npz (InlineAcc/LateralAcc 50 Hz, maps lap->tc0).
Usage: python peaks_integrity.py   (writes peaks_integrity.json next to this file)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RACE = "fuji_aim_2020_07_30"
STAGES = {
    "baseline": ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races" / RACE,
    "iter1": ROOT / "artifacts/improvement-eval-2026-10-02/after/channel-timing/candidates",
    "iter2": ROOT / "public/data/races" / RACE,
}
GPS_KEYS = ("t", "lat", "lng", "speed", "dist")
LOGGER_KEYS = ("aps", "brake", "steer", "gear", "accx", "accy")


def lap(stage: str, k: int) -> dict:
    return json.loads((STAGES[stage] / f"osaki_hmr_demio_101_lap_{k:03d}.json").read_text(encoding="utf-8"))


def stats(x: np.ndarray) -> dict:
    a = np.abs(x)
    return {"maxAbs": round(float(a.max()), 4), "max": round(float(x.max()), 4), "min": round(float(x.min()), 4),
            "p99Abs": round(float(np.percentile(a, 99)), 4), "p95Abs": round(float(np.percentile(a, 95)), 4),
            "rms": round(float(np.sqrt(np.mean(x ** 2))), 4),
            # first-difference RMS: drops when a fractional linear interpolation low-passes the 10 Hz series
            "diffRms": round(float(np.sqrt(np.mean(np.diff(x) ** 2))), 4)}


def pct(a: float, b: float) -> float:
    return round(100.0 * (b - a) / a, 2)


def main() -> None:
    out: dict = {"race": RACE, "stages": {k: str(v.relative_to(ROOT)) for k, v in STAGES.items()}}

    # ---------- integrity: GPS arrays + non-channel meta ----------
    integ = {}
    for k in range(1, 7):
        b, i1, i2 = lap("baseline", k), lap("iter1", k), lap("iter2", k)
        row = {}
        for key in GPS_KEYS:
            row[key] = {"iter2==baseline": b[key] == i2[key], "iter1==baseline": b[key] == i1[key]}
        mb = {kk: vv for kk, vv in b["meta"].items() if kk != "channel_time_alignment"}
        m2 = {kk: vv for kk, vv in i2["meta"].items() if kk != "channel_time_alignment"}
        row["metaExceptAlignmentEqual"] = mb == m2
        row["metaDiffKeys"] = sorted(kk for kk in set(mb) | set(m2) if mb.get(kk) != m2.get(kk))
        row["lengthsEqual"] = all(len(i2[key]) == len(b[key]) for key in GPS_KEYS + LOGGER_KEYS)
        row["iter2ShiftS"] = i2["meta"]["channel_time_alignment"]["shiftSeconds"]
        row["iter1ShiftS"] = i1["meta"].get("channel_time_alignment", {}).get("shiftSeconds")
        integ[k] = row
    out["gpsIntegrity"] = integ

    # registration offsets: baseline snapshot vs current
    rb = json.loads((STAGES["baseline"] / "gps_registration.json").read_text(encoding="utf-8"))
    rc = json.loads((STAGES["iter2"] / "gps_registration.json").read_text(encoding="utf-8"))
    reg = {"sessionOffsetEqual": rb.get("sessionOffsetMeters") == rc.get("sessionOffsetMeters"),
           "sessionOffsetMeters": rc.get("sessionOffsetMeters"), "laps": {}}
    for lb, lc in zip(rb["laps"], rc["laps"]):
        strip = lambda d: {kk: vv for kk, vv in d.items() if kk != "dataSha256"}
        reg["laps"][lb["lap"]] = {"offsetEqual": lb["offsetMeters"] == lc["offsetMeters"], "offset": lc["offsetMeters"],
                                  "allFieldsExceptShaEqual": strip(lb) == strip(lc),
                                  "dataShaMatchesFile": hashlib.sha256((STAGES["iter2"] / lc["dataFile"]).read_bytes()).hexdigest() == lc["dataSha256"]}
    top_b = {kk: vv for kk, vv in rb.items() if kk != "laps"}
    top_c = {kk: vv for kk, vv in rc.items() if kk != "laps"}
    reg["topLevelDiffKeys"] = sorted(kk for kk in set(top_b) | set(top_c) if top_b.get(kk) != top_c.get(kk))
    out["registration0730"] = reg

    # 7/29 lap files vs git HEAD
    w = {}
    for k in range(1, 5):
        rel = f"public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_{k:03d}.json"
        head = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, check=True).stdout
        w[k] = hashlib.sha256(head).hexdigest() == hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    rel = "public/data/races/fuji_aim_01/gps_registration.json"
    head = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, check=True).stdout
    w["gps_registration.json(CR-stripped; core.autocrlf=true)"] = head.replace(b"\r", b"") == (ROOT / rel).read_bytes().replace(b"\r", b"")
    out["wet0729UnchangedVsHead"] = w

    # ---------- peaks ----------
    peaks = {}
    for k in range(1, 7):
        peaks[k] = {}
        for ch in ("accx", "accy", "brake", "steer", "aps"):
            peaks[k][ch] = {s: stats(np.asarray(lap(s, k)[ch], float)) for s in STAGES}
            b = peaks[k][ch]["baseline"]
            for s in ("iter1", "iter2"):
                peaks[k][ch][s]["maxAbsChangePct"] = pct(b["maxAbs"], peaks[k][ch][s]["maxAbs"])
                peaks[k][ch][s]["p99AbsChangePct"] = pct(b["p99Abs"], peaks[k][ch][s]["p99Abs"])
                peaks[k][ch][s]["rmsChangePct"] = pct(b["rms"], peaks[k][ch][s]["rms"])
                peaks[k][ch][s]["diffRmsChangePct"] = pct(b["diffRms"], peaks[k][ch][s]["diffRms"])
    out["peaks"] = peaks
    agg = {}
    for ch in ("accx", "accy", "brake", "steer"):
        agg[ch] = {}
        for s in ("iter1", "iter2"):
            for m in ("maxAbsChangePct", "p99AbsChangePct", "rmsChangePct", "diffRmsChangePct"):
                vals = [peaks[k][ch][s][m] for k in range(1, 7)]
                agg[ch][f"{s}_{m}"] = {"mean": round(float(np.mean(vals)), 2), "min": min(vals), "max": max(vals),
                                       "worstAbs": max(vals, key=abs)}
        # session-level: concatenate laps
        for s in STAGES:
            x = np.concatenate([np.asarray(lap(s, k)[ch], float) for k in range(1, 7)])
            agg[ch][f"session_{s}"] = stats(x)
    out["peakAggregates"] = agg

    # ---------- native XRK provenance + 10 Hz phase envelope ----------
    apex = os.environ.get("APEX_WORK")
    if apex:
        z = np.load(Path(apex) / f"xrk_{RACE}.npz", allow_pickle=True)
        maps = json.loads(str(z["maps"]))
        nat = {}
        for k in range(1, 7):
            d2, db = lap("iter2", k), lap("baseline", k)
            t = np.asarray(d2["t"], float)
            tc0 = maps[str(k)]["tc0"]
            shift = d2["meta"]["channel_time_alignment"]["shiftSeconds"]
            row = {"tc0": tc0, "shiftS": shift, "metaLapStartTimecodeMs": d2["meta"]["channel_time_alignment"]["source"].get("lapStartTimecodeMs")}
            for ch, src in (("accx", "InlineAcc"), ("accy", "LateralAcc")):
                tt, vv = z[f"{src}_t"], z[src]
                pred2 = np.interp(tc0 + 1000.0 * t - 1000.0 * shift, tt, vv)
                predb = np.interp(tc0 + 1000.0 * t, tt, vv)
                row[f"{ch}_iter2_vs_native_at_t_minus_shift_maxAbsErr"] = round(float(np.max(np.abs(np.asarray(d2[ch]) - pred2))), 5)
                row[f"{ch}_baseline_vs_native_at_t_maxAbsErr"] = round(float(np.max(np.abs(np.asarray(db[ch]) - predb))), 5)
                # window of the native series covering this lap (iter2 window)
                lo, hi = tc0 + 1000.0 * (t[0] - shift), tc0 + 1000.0 * (t[-1] - shift)
                m = (tt >= lo) & (tt <= hi)
                row[f"{ch}_native50HzMaxAbs_iter2Window"] = round(float(np.max(np.abs(vv[m]))), 4)
                lob, hib = tc0 + 1000.0 * t[0], tc0 + 1000.0 * t[-1]
                mb = (tt >= lob) & (tt <= hib)
                row[f"{ch}_native50HzMaxAbs_baselineWindow"] = round(float(np.max(np.abs(vv[mb]))), 4)
                # envelope of 10 Hz point samples over all sub-sample phases (1 ms steps over 100 ms), iter2 window
                env = []
                for ph in np.arange(0.0, 100.0, 1.0):
                    s = np.interp(tc0 + 1000.0 * t - 1000.0 * shift + ph, tt, vv)
                    env.append(float(np.max(np.abs(s))))
                env = np.asarray(env)
                a2 = float(np.max(np.abs(d2[ch])))
                ab = float(np.max(np.abs(db[ch])))
                row[f"{ch}_tenHzPhaseEnvelope"] = {"min": round(float(env.min()), 4), "median": round(float(np.median(env)), 4),
                                                   "max": round(float(env.max()), 4)}
                row[f"{ch}_iter2MaxAbs_phaseRankPct"] = round(float(100.0 * np.mean(env < a2)), 1)
                row[f"{ch}_baselineMaxAbs_phaseRankPct"] = round(float(100.0 * np.mean(env < ab)), 1)
                row[f"{ch}_iter2MaxAbs_vs_envelopeMedianPct"] = pct(float(np.median(env)), a2)
                row[f"{ch}_baselineMaxAbs_vs_envelopeMedianPct"] = pct(float(np.median(env)), ab)
            nat[k] = row
        out["nativeXrk"] = nat
        for ch in ("accx", "accy"):
            out.setdefault("nativeAggregates", {})[ch] = {
                "iter2_vs_envelopeMedianPct_mean": round(float(np.mean([nat[k][f"{ch}_iter2MaxAbs_vs_envelopeMedianPct"] for k in nat])), 2),
                "baseline_vs_envelopeMedianPct_mean": round(float(np.mean([nat[k][f"{ch}_baselineMaxAbs_vs_envelopeMedianPct"] for k in nat])), 2),
                "iter2_phaseRank_mean": round(float(np.mean([nat[k][f"{ch}_iter2MaxAbs_phaseRankPct"] for k in nat])), 1),
                "baseline_phaseRank_mean": round(float(np.mean([nat[k][f"{ch}_baselineMaxAbs_phaseRankPct"] for k in nat])), 1),
            }
    (HERE / "peaks_integrity.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"gpsIntegrity": integ, "registration0730": reg, "wet": w}, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
