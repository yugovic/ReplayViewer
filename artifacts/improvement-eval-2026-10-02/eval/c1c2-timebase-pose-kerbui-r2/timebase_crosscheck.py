"""Independent check of the C1 lap-clock / ghost-delta change.

  python timebase_crosscheck.py synth     -> synthetic_laps.json (self-pair truth test inputs)
  npx vite-node <this dir>/timebase_harness.ts   -> harness_out.json (runs BEFORE = git HEAD delta.ts
                                                     and AFTER = working-tree delta.ts/lapClock.ts)
  python timebase_crosscheck.py analyze   -> timebase_crosscheck.json

Self-pair truth test: each real lap's (t, dist) is treated as the continuous
truth T(S) with the timing line at S = 0, T = 0. It is re-sampled at 10 Hz with
timing-line phase delta (first sample delta s after the line; t and dist counted
from that first sample, as pipeline/build_race.py does). Two recordings of the
SAME physical lap with different delta must give a ghost delta of 0 everywhere
and the ghost car at the same physical point. Truth is known exactly (= 0).
"""
import json
import math
import os
import sys
from datetime import datetime

import numpy as np

ROOT = r"D:\00_Dev\ReplayViewer"
OUT = os.path.dirname(os.path.abspath(__file__))
SESSIONS = ["fuji_aim_01", "fuji_aim_2020_07_30"]
DELTAS = [0.007, 0.037, 0.067, 0.097]


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_laps():
    laps = []
    for race in SESSIONS:
        d = os.path.join(ROOT, "public", "data", "races", race)
        idx = json.load(open(os.path.join(d, "laps.json"), encoding="utf-8"))
        for rec in idx["laps"]:
            if rec.get("data_file"):
                laps.append((race, rec, json.load(open(os.path.join(d, rec["data_file"]), encoding="utf-8"))))
    return laps


def synth():
    out = []
    for race, rec, lap in load_laps():
        t = np.asarray(lap["t"], float)
        dist = np.asarray(lap["dist"], float)
        S = lambda T: np.interp(T, t, dist)  # truth distance from the line at true time T
        for delta in DELTAS:
            T = np.arange(delta, t[-1] + 1e-9, 0.1)
            rt = np.round(T - delta, 6)
            rd = S(T) - S(delta)
            sp = (S(T + 0.05) - S(T - 0.05)) / 0.1 * 3.6
            out.append({"source": f"{race}/L{lap['meta']['lap']}", "delta": delta,
                        "s0": float(S(delta)), "t": rt.tolist(), "dist": rd.tolist(), "speed": sp.tolist()})
    json.dump(out, open(os.path.join(OUT, "synthetic_laps.json"), "w"), separators=(",", ":"))
    print("wrote", len(out), "synthetic recordings")


def st(v, digits=4):
    v = np.asarray(v, float)
    if v.size == 0:
        return None
    return {"n": int(v.size), "median": round(float(np.median(v)), digits), "p95": round(float(np.quantile(v, 0.95)), digits),
            "max": round(float(v.max()), digits), "mean": round(float(v.mean()), digits)}


def analyze():
    laps = load_laps()
    h = json.load(open(os.path.join(OUT, "harness_out.json"), encoding="utf-8"))
    res = {}

    # ---------------------------------------------------------------- 1) lap clock
    clock = []
    by_race = {}
    for race, rec, lap in laps:
        m = lap["meta"]
        t = np.asarray(lap["t"], float)
        dist = np.asarray(lap["dist"], float)
        delta = m["first_sample_after_lap_start_seconds"]
        rederived = (iso(m["first_sample_time"]) - iso(m["lap_start_time"])).total_seconds()
        v0 = (dist[1] - dist[0]) / (t[1] - t[0])
        # BEFORE: at displayed time X the drawn sample is t = X, i.e. true lap time X + delta.
        # Car-ahead distance = dist(t = X) - dist(t = X - delta) (linear extrapolation before t0).
        X = t
        back = X - delta
        d_back = np.where(back >= t[0], np.interp(back, t, dist), dist[0] + (back - t[0]) * v0)
        ahead = dist - d_back
        hk = h["clock"][f"{race}/L{m['lap']}"]
        row = {
            "lap": f"{race}/L{m['lap']}",
            "t0": float(t[0]),
            "delta_s": delta,
            "delta_rederived_from_iso_s": round(rederived, 6),
            "displayed_at_first_sample_before": hk["beforeHud"],
            "displayed_at_first_sample_after": hk["afterHud"],
            "after_display_equals_delta": hk["afterHud"] == hk["expectedFromDelta"],
            "v0_kmh": round(v0 * 3.6, 1),
            "car_ahead_at_first_sample_before_m": round(delta * v0, 3),
            "car_ahead_over_lap_before_m": st(ahead, 3),
            "car_ahead_after_m": 0.0,
            "s0_lapClock_ts_m": hk["s0"],
            "lap_time_official_s": m["lap_time_seconds"],
            "last_sample_true_time_s": round(float(t[-1] + delta), 3),
            "lap_time_minus_last_true_s": round(m["lap_time_seconds"] - float(t[-1] + delta), 3),
        }
        clock.append(row)
        by_race.setdefault(race, []).append(row)
    # Grid continuity: lap_k end gap + delta_{k+1} must be one 10 Hz interval.
    grid = []
    for race, rows in by_race.items():
        for a, b in zip(rows, rows[1:]):
            grid.append({"pair": f"{a['lap']}->{b['lap']}", "gap_plus_next_delta_s": round(a["lap_time_minus_last_true_s"] + b["delta_s"], 4)})
    res["lapClock"] = {"perLap": clock, "gridContinuity": grid}

    # ---------------------------------------------- 2) self-pair truth (known = 0)
    syn = h["synthetic"]
    def agg(rows, key):
        return st(np.concatenate([np.abs(np.asarray(r[key], float)) for r in rows]) * 1000.0, 2)
    worst = [r for r in syn if r["deltaMain"] == 0.097 and r["deltaGhost"] == 0.007]
    worst_rev = [r for r in syn if r["deltaMain"] == 0.007 and r["deltaGhost"] == 0.097]
    signed_mean = lambda rows, key: round(float(np.mean(np.concatenate([np.asarray(r[key], float) for r in rows]))) * 1000, 2)
    res["selfPairTruthTest"] = {
        "pairs": len(syn),
        "ghostDeltaAbsErrorMs": {"before": agg(syn, "before"), "after": agg(syn, "after")},
        "worstPhasePair_0.097vs0.007": {
            "before_absMs": agg(worst, "before"), "after_absMs": agg(worst, "after"),
            "before_signedMeanMs": signed_mean(worst, "before"), "after_signedMeanMs": signed_mean(worst, "after"),
            "perSourceLapBeforeMaxMs": {r["source"]: round(float(np.max(np.abs(r["before"]))) * 1000, 1) for r in worst},
            "perSourceLapAfterMaxMs": {r["source"]: round(float(np.max(np.abs(r["after"]))) * 1000, 2) for r in worst},
        },
        "reversePair_0.007vs0.097_signedMeanMs": {"before": signed_mean(worst_rev, "before"), "after": signed_mean(worst_rev, "after")},
        "ghostPositionAbsErrorM": {"before": st(np.concatenate([np.abs(r["ghostPosBeforeM"]) for r in syn]), 3),
                                   "after": st(np.concatenate([np.abs(r["ghostPosAfterM"]) for r in syn]), 4)},
    }

    # ----------------------------- 3) real same-session pairs: change and its model
    real = h["real"]
    lapmap = {f"{race}/L{lap['meta']['lap']}": lap for race, _, lap in laps}
    rows = []
    resid_all = []
    change_all = []
    for p in real:
        m, g = lapmap[p["main"]], lapmap[p["ghost"]]
        dm, dg = m["meta"]["first_sample_after_lap_start_seconds"], g["meta"]["first_sample_after_lap_start_seconds"]
        s0m, s0g = p["s0Main"], p["s0Ghost"]
        tg = np.asarray(g["t"], float)
        distg = np.asarray(g["dist"], float)
        d = np.asarray(p["dist"], float)
        before = np.asarray(p["before"], float)
        after = np.asarray(p["after"], float)
        change = after - before
        # Model: change = (dm - dg) - [t_g(d + s0m - s0g) - t_g(d)]  (true-time shift minus origin shift)
        model = (dm - dg) - (np.interp(d + s0m - s0g, distg, tg) - np.interp(d, distg, tg))
        ok = d + s0m - s0g <= distg[-1]
        resid = (change - model)[ok]
        resid_all.append(np.abs(resid))
        change_all.append(np.abs(change))
        rows.append({"main": p["main"], "ghost": p["ghost"], "dm": dm, "dg": dg,
                     "changeMs": {"mean": round(float(change.mean()) * 1000, 1), "min": round(float(change.min()) * 1000, 1),
                                  "max": round(float(change.max()) * 1000, 1)},
                     "modelResidualMaxMs": round(float(np.abs(resid).max()) * 1000, 3),
                     "ghostOriginShiftM": round(s0m - s0g, 3)})
    rows.sort(key=lambda r: -max(abs(r["changeMs"]["min"]), abs(r["changeMs"]["max"])))
    res["realSameSessionPairs"] = {
        "pairs": len(real),
        "absChangeAfterMinusBeforeMs": st(np.concatenate(change_all) * 1000, 1),
        "modelResidualAbsMs": st(np.concatenate(resid_all) * 1000, 3),
        "worst5": rows[:5],
        "ghostOriginShiftAbsM": st([abs(r["ghostOriginShiftM"]) for r in rows], 3),
    }
    json.dump(res, open(os.path.join(OUT, "timebase_crosscheck.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False)[:9000])


if __name__ == "__main__":
    {"synth": synth, "analyze": analyze}[sys.argv[1]]()
