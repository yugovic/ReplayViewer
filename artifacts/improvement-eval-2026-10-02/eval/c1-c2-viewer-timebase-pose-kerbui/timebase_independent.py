"""Independent re-derivation of the C1 lap-clock / ghost-delta change.

Python port written from the TS source text (baseline delta.ts = BEFORE,
current delta.ts + lapClock.ts = AFTER); checked against the TS dump
(delta_ts_dump.json from lap_clock_display_check.ts) before any number is used.

Outputs (stdout JSON):
  clock[]        per lap: delta, v0, s0 = delta*v0, car-ahead error at the
                 first sample and lap-wide delta*v(t) (speed channel) BEFORE;
                 AFTER = 0 by construction (display = t + delta)
  portCheck      max |python - TS| for before/after delta and ghost time
  synthetic      truth test with MY resampler: each real lap's continuous
                 (t, dist) is the truth, re-sampled at 10 Hz starting delta
                 after the line; real deltas AND 200 random-delta draws
  realChange     |after - before| HUD delta on the real data, and the
                 analytic prediction -(dm-dg) + (s0m-s0g)/v_g
  endAnchor      real-data anchor: on the final straight (main sample 1.0 s
                 before its last sample) truth ~= official lap-time difference;
                 error of before/after there (dominated by GPS distance scale)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RACES = ["fuji_aim_01", "fuji_aim_2020_07_30"]


def load():
    laps = []
    for race in RACES:
        d = ROOT / "public/data/races" / race
        idx = json.loads((d / "laps.json").read_text(encoding="utf-8"))
        for rec in idx["laps"]:
            if rec.get("data_file"):
                lap = json.loads((d / rec["data_file"]).read_text(encoding="utf-8"))
                laps.append({
                    "id": f"{race}/L{lap['meta']['lap']}",
                    "t": np.asarray(lap["t"], float), "dist": np.asarray(lap["dist"], float),
                    "speed": np.asarray(lap["speed"], float),
                    "delta": lap["meta"].get("first_sample_after_lap_start_seconds") or 0.0,
                    "official": lap["meta"]["lap_time_seconds"],
                })
    return laps


# ---- port of delta.ts / lapClock.ts --------------------------------------
def time_at_dist(lap, d):
    t, x = lap["t"], lap["dist"]
    return float(np.interp(d, x, t))  # clamps to t[0]/t[-1] like timeAtDist


def dist_at_time(lap, tt):
    return float(np.interp(tt, lap["t"], lap["dist"]))


def delta_of(lap):
    v = lap["delta"]
    return v if isinstance(v, (int, float)) and 0 <= v <= 1 else 0.0


def s0_of(lap):
    dl = delta_of(lap)
    if dl <= 0:
        return 0.0
    dt = lap["t"][1] - lap["t"][0]
    v = (lap["dist"][1] - lap["dist"][0]) / dt if dt > 0 else float("nan")
    if not (np.isfinite(v) and v > 0):
        v = lap["speed"][0] / 3.6
    return v * dl if v > 0 else 0.0


def true_time_at_true_dist(lap, S):
    dl, s0 = delta_of(lap), s0_of(lap)
    rec = S - s0
    first = lap["dist"][0]
    if s0 > 0 and rec < first:
        frac = min(1.0, max(0.0, (S - first) / s0))
        return lap["t"][0] + dl * frac
    return time_at_dist(lap, rec) + dl


def delta_before(m, g, d):
    return time_at_dist(m, d) - time_at_dist(g, d)


def delta_after(m, g, d):
    S = d + s0_of(m)
    return true_time_at_true_dist(m, S) - true_time_at_true_dist(g, S)


def ghost_t_before(m, g, tm):
    return time_at_dist(g, dist_at_time(m, tm))


def ghost_t_after(m, g, tm):
    return time_at_dist(g, dist_at_time(m, tm) + s0_of(m) - s0_of(g))


# ---- helpers ---------------------------------------------------------------
def st(v, nd=3):
    v = np.asarray(v, float)
    return {"n": int(v.size), "median": round(float(np.median(v)), nd),
            "p95": round(float(np.quantile(v, 0.95)), nd), "max": round(float(v.max()), nd)}


def resample(src, dl, hz=10.0):
    """My resampler: truth T(S) = src (t, dist) with the line at T=0; first sample at T=dl."""
    T = np.arange(dl, src["t"][-1] + 1e-9, 1.0 / hz)
    S = np.interp(T, src["t"], src["dist"])
    rec = {"t": np.round(T - dl, 6), "dist": S - S[0], "delta": dl,
           "speed": np.gradient(S, T) * 3.6}
    return rec, float(S[0])


def synthetic(laps, deltas):
    errs_b, errs_a = [], []
    start_b, start_a = [], []
    for i, srcm in enumerate(laps):
        for j, srcg in enumerate(laps):
            if i == j:
                continue
            rm, s0m_true = resample(srcm, deltas[i])
            rg, s0g_true = resample(srcg, deltas[j])
            smax = min(rm["dist"][-1] + s0m_true, rg["dist"][-1] + s0g_true)
            first = True
            for d in rm["dist"]:
                S = d + s0m_true
                if S > smax:
                    break
                truth = time_at_dist(srcm, S) - time_at_dist(srcg, S)
                eb = delta_before(rm, rg, d) - truth
                ea = delta_after(rm, rg, d) - truth
                errs_b.append(abs(eb) * 1000)
                errs_a.append(abs(ea) * 1000)
                if first:
                    start_b.append(abs(eb) * 1000)
                    start_a.append(abs(ea) * 1000)
                    first = False
    return {"errorMs": {"before": st(errs_b), "after": st(errs_a)},
            "firstSampleErrorMs": {"before": st(start_b), "after": st(start_a)}}


def main():
    laps = load()
    # 0) port check against the TS dump
    dump = json.loads((HERE / "delta_ts_dump.json").read_text(encoding="utf-8"))
    by_id = {l["id"]: l for l in laps}
    mx = {"before": 0.0, "after": 0.0, "ghostTBefore": 0.0, "ghostTAfter": 0.0}
    for p in dump:
        m, g = by_id[p["main"]], by_id[p["ghost"]]
        tk = m["t"][::5]
        for k, d in enumerate(p["d"]):
            mx["before"] = max(mx["before"], abs(delta_before(m, g, d) - p["before"][k]))
            mx["after"] = max(mx["after"], abs(delta_after(m, g, d) - p["after"][k]))
            mx["ghostTBefore"] = max(mx["ghostTBefore"], abs(ghost_t_before(m, g, tk[k]) - p["ghostTBefore"][k]))
            mx["ghostTAfter"] = max(mx["ghostTAfter"], abs(ghost_t_after(m, g, tk[k]) - p["ghostTAfter"][k]))

    # 1) lap clock / car-ahead error
    clock = []
    for l in laps:
        dl = delta_of(l)
        v0_dist = (l["dist"][1] - l["dist"][0]) / (l["t"][1] - l["t"][0])
        v_lap = l["speed"] / 3.6
        clock.append({
            "lap": l["id"], "delta_s": dl,
            "displayedAtFirstSample": {"before": 0.0, "after": dl},
            "v0_mps": round(v0_dist, 2), "v0_speedChannel_mps": round(l["speed"][0] / 3.6, 2),
            "carAheadAtFirstSample_m": {"before": round(dl * v0_dist, 3), "after": 0.0},
            "carAheadLapWide_m_before": st(dl * v_lap),
            "lastSampleTrueTime": round(l["t"][-1] + dl, 3), "official": l["official"],
            "officialMinusLastTrue_s": round(l["official"] - (l["t"][-1] + dl), 3),
        })

    # 2) synthetic truth tests
    real_deltas = [delta_of(l) for l in laps]
    syn_real = synthetic(laps, real_deltas)
    rng = np.random.default_rng(20261002)
    rand_b, rand_a = [], []
    for _ in range(20):
        r = synthetic(laps, list(rng.uniform(0.0, 0.1, len(laps))))
        rand_b.append(r["errorMs"]["before"]["p95"])
        rand_a.append(r["errorMs"]["after"]["p95"])

    # 3) real data change + analytic prediction
    change, resid = [], []
    signed_change_by_pair = []
    for m in laps:
        for g in laps:
            if m is g:
                continue
            dm, dg, s0m, s0g = delta_of(m), delta_of(g), s0_of(m), s0_of(g)
            c_pair = []
            for d in m["dist"]:
                c = delta_after(m, g, d) - delta_before(m, g, d)
                # predicted change = -(legacy bias) = (dm - dg) - (s0m - s0g)/v_g at that point
                tg = time_at_dist(g, d)
                vg = max(1.0, float(np.interp(tg, g["t"], g["speed"])) / 3.6)
                pred = (dm - dg) - (s0m - s0g) / vg
                change.append(abs(c) * 1000)
                resid.append(abs(c - pred) * 1000)
                c_pair.append(c * 1000)
            signed_change_by_pair.append({"main": m["id"], "ghost": g["id"], "dm": dm, "dg": dg,
                                          "medianChangeMs": round(float(np.median(c_pair)), 2),
                                          "maxAbsChangeMs": round(float(np.max(np.abs(c_pair))), 2)})
    worst = max(signed_change_by_pair, key=lambda r: r["maxAbsChangeMs"])

    # 4) end-of-lap anchor (real data, official timing)
    end_b, end_a = [], []
    dist_spread = []
    for m in laps:
        for g in laps:
            if m is g:
                continue
            k = len(m["t"]) - 11  # 1.0 s before the main's last sample (final straight)
            d = m["dist"][k]
            truth = m["official"] - g["official"]
            end_b.append(abs(delta_before(m, g, d) - truth) * 1000)
            end_a.append(abs(delta_after(m, g, d) - truth) * 1000)
            dist_spread.append(m["dist"][-1] - g["dist"][-1])

    out = {
        "portCheckMaxAbsDiff_s": {k: float(f"{v:.3g}") for k, v in mx.items()},
        "clock": clock,
        "clockSummary": {
            "carAheadAtFirstSample_m_before": st([c["carAheadAtFirstSample_m"]["before"] for c in clock]),
            "carAheadLapWideMedian_m_before_medianOfLaps": round(float(np.median([c["carAheadLapWide_m_before"]["median"] for c in clock])), 3),
            "carAheadLapWideMax_m_before": max(c["carAheadLapWide_m_before"]["max"] for c in clock),
            "officialMinusLastTrue_s": st([c["officialMinusLastTrue_s"] for c in clock]),
        },
        "syntheticRealDeltas": syn_real,
        "syntheticRandomDeltas_p95ErrorMs_over20draws": {"before": st(rand_b), "after": st(rand_a)},
        "realDataChange_ms": st(change),
        "realDataChange_minusAnalyticPrediction_ms": st(resid),
        "realDataWorstPair": worst,
        "endAnchor_errorVsOfficialDiff_ms": {"before": st(end_b), "after": st(end_a)},
        "lastSampleDistSpread_m": st(np.abs(dist_spread)),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
