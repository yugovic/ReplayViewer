#!/usr/bin/env python
"""Independent re-computation of the 7/30 logger-channel re-timing (evaluator, 2026-10-02).

Written from scratch; does not import or read any output of
scripts/quality/align-fuji-channel-timing.py or after/channel-timing/.

Inputs (read-only)
  BEFORE 7/30 : artifacts/improvement-eval-2026-10-02/baseline/public/data/races/fuji_aim_2020_07_30
  AFTER  7/30 : public/data/races/fuji_aim_2020_07_30
  REF    7/29 : public/data/races/fuji_aim_01   (+ git HEAD copy for the untouched check)

Conventions
  lag tau : argmax_tau Pearson(ref(t), sig(t + tau)) on a 5 ms cubic-spline grid,
            parabolic peak refinement. tau > 0 -> sig happens LATER than ref.
            A re-timing channel(t) = logger(t - s) moves tau by +s.
  GPS refs: a_long = d(speed)/dt ; course rate r = d(psi)/dt with psi from the
            (already centred-MA) lat/lng ; a_lat = v * r ; curvature = r / v.
            All signals (GPS and logger) get the same zero-phase Gaussian (sigma 0.1 s).
  events  : delta = t(response) - t(brake), level crossings on the 5 ms spline.
Output: indep_timing_results.json next to this script.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d

ROOT = Path(r"D:/00_Dev/ReplayViewer")
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races"
CUR = ROOT / "public/data/races"
OUT = Path(__file__).resolve().parent / "indep_timing_results.json"
R30, R29 = "fuji_aim_2020_07_30", "fuji_aim_01"
N30, N29 = 6, 4
DT = 0.005           # upsampling grid
MAXLAG = 1.5         # s
TRIM = 2.0           # s trimmed at lap edges before correlating
SIG = 0.1            # s Gaussian on the 10 Hz series (1 sample)
GPS_ARRAYS = ("t", "lat", "lng", "speed", "dist")
LOGGER = ("aps", "brake", "steer", "gear", "accx", "accy")


def lap_path(base: Path, race: str, k: int) -> Path:
    return base / race / f"osaki_hmr_demio_101_lap_{k:03d}.json"


def load(base: Path, race: str, k: int) -> dict:
    return json.loads(lap_path(base, race, k).read_text(encoding="utf-8"))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- signals
def derive(d: dict) -> dict:
    t = np.asarray(d["t"], float)
    lat = np.radians(np.asarray(d["lat"], float))
    lng = np.radians(np.asarray(d["lng"], float))
    R = 6378137.0
    x = R * np.cos(lat.mean()) * (lng - lng[0])
    y = R * (lat - lat[0])
    vx, vy = np.gradient(x, t), np.gradient(y, t)
    psi = np.unwrap(np.arctan2(vy, vx))
    r = np.gradient(psi, t)                      # course rate rad/s
    v = np.asarray(d["speed"], float) / 3.6
    g = 9.80665
    along = np.gradient(v, t) / g                # g
    s = lambda a: gaussian_filter1d(np.asarray(a, float), SIG / 0.1, mode="nearest")
    out = {
        "t": t, "v": v, "dist": np.asarray(d["dist"], float),
        "gps_along": s(along), "gps_rate": s(r), "gps_lat": s(v * r / g),
        "gps_curv": s(np.where(v > 5, r / np.maximum(v, 5), 0.0)),
        "gps_decel": s(-along),
    }
    for c in ("aps", "brake", "steer", "accx", "accy"):
        out[c] = s(d[c])
        out[c + "_raw"] = np.asarray(d[c], float)
    return out


def spline_grid(t, y, tg):
    return CubicSpline(t, y)(tg)


def xcorr_lag(t, ref, sig, maxlag=MAXLAG, trim=TRIM, sign=None):
    """Return (tau, r_peak, sign) with tau = argmax corr(ref(t), sign*sig(t+tau))."""
    tg = np.arange(t[0], t[-1], DT)
    rg, sg = spline_grid(t, ref, tg), spline_grid(t, sig, tg)
    K = int(round(maxlag / DT))
    i0 = int(round((trim) / DT))
    i1 = len(tg) - i0
    a = rg[i0:i1]
    a = (a - a.mean()) / a.std()
    lags = np.arange(-K, K + 1)
    cc = np.empty(len(lags))
    for j, k in enumerate(lags):
        b = sg[i0 + k:i1 + k]
        b = (b - b.mean()) / b.std()
        cc[j] = np.mean(a * b)
    if sign is None:
        sign = 1.0 if cc.max() >= -cc.min() else -1.0
    cc = cc * sign
    j = int(np.argmax(cc))
    frac = 0.0
    if 0 < j < len(cc) - 1:
        y0, y1, y2 = cc[j - 1], cc[j], cc[j + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            frac = 0.5 * (y0 - y2) / den
    tau = (lags[j] + frac) * DT
    return float(tau), float(cc[j]), float(sign)


# ---------------------------------------------------------------- events
def crossing_up(tg, y, level, i_from, i_to):
    """First upward crossing of level in y[i_from:i_to], linear sub-step."""
    seg = y[i_from:i_to]
    idx = np.nonzero((seg[:-1] < level) & (seg[1:] >= level))[0]
    if len(idx) == 0:
        return None
    i = i_from + idx[0]
    return float(tg[i] + (level - y[i]) / (y[i + 1] - y[i]) * (tg[i + 1] - tg[i]))


def last_crossing_up(tg, y, level, i_from, i_to):
    seg = y[i_from:i_to]
    idx = np.nonzero((seg[:-1] < level) & (seg[1:] >= level))[0]
    if len(idx) == 0:
        return None
    i = i_from + idx[-1]
    return float(tg[i] + (level - y[i]) / (y[i + 1] - y[i]) * (tg[i + 1] - tg[i]))


def braking_events(sd: dict, p_thr=5.0, min_peak=30.0, quiet=1.0, min_dur=0.5):
    """Brake applications: raw brake rises above p_thr after >= quiet s below it,
    stays above for >= min_dur and peaks >= min_peak (bar)."""
    t, b = sd["t"], sd["brake_raw"]
    on = b >= p_thr
    ev = []
    i = 1
    while i < len(b):
        if on[i] and not on[i - 1]:
            j = i
            while j < len(b) and on[j]:
                j += 1
            k0 = max(0, i - int(round(quiet / 0.1)))
            if (not on[k0:i].any()) and (t[min(j, len(t) - 1)] - t[i] >= min_dur) and b[i:j].max() >= min_peak \
                    and t[i] > 2.0 and t[-1] - t[i] > 4.0:
                ev.append((i, j))
            i = j
        else:
            i += 1
    return ev


def event_deltas(sd: dict, accx_sign: float):
    """Per braking event: brake onset/half-rise vs GPS decel and logger (accx) decel."""
    t = sd["t"]
    tg = np.arange(t[0], t[-1], DT)
    # unsmoothed brake (10 Hz) for the brake timing; smoothed GPS decel & accx decel
    bg = CubicSpline(t, sd["brake_raw"])(tg)
    gd = CubicSpline(t, sd["gps_decel"])(tg)
    xd = CubicSpline(t, -accx_sign * sd["accx"])(tg)   # decel positive
    dg = np.interp(tg, t, sd["dist"])
    vg = np.interp(tg, t, sd["v"])
    rows = []
    for (i, j) in braking_events(sd):
        ti = t[i]
        a = np.searchsorted(tg, ti - 1.5)
        z = np.searchsorted(tg, ti + 3.0)
        peak_b = bg[a:z].max()
        # brake onset = first crossing of 10 % / 50 % of event peak, searching from 0.5 s before the 5-bar sample
        s0 = np.searchsorted(tg, ti - 0.5)
        tb10 = crossing_up(tg, bg, 0.10 * peak_b, s0, z)
        tb50 = crossing_up(tg, bg, 0.50 * peak_b, s0, z)
        res = {"t_brake_sample": float(ti), "peak_bar": float(peak_b), "v_kmh": float(vg[s0] * 3.6)}
        for name, y in (("gps", gd), ("imu", xd)):
            # pre-level: median over [ti-1.2, ti-0.6] ; peak: max over [ti, ti+3]
            p0 = np.median(y[np.searchsorted(tg, ti - 1.2):np.searchsorted(tg, ti - 0.6)])
            pk_i = np.searchsorted(tg, ti) + int(np.argmax(y[np.searchsorted(tg, ti):z]))
            pk = y[pk_i]
            amp = pk - p0
            if amp < 0.2:      # < 0.2 g rise -> skip
                res[name] = None
                continue
            # response crossings: last upward crossing before the peak (avoids pre-lift wiggles)
            lo = np.searchsorted(tg, ti - 1.5)
            t10 = last_crossing_up(tg, y, p0 + 0.10 * amp, lo, pk_i + 1)
            t50 = last_crossing_up(tg, y, p0 + 0.50 * amp, lo, pk_i + 1)
            r = {"pre_g": float(p0), "peak_g": float(pk)}
            if tb10 is not None and t10 is not None:
                r["d10_s"] = t10 - tb10
                r["d10_m"] = float(np.interp(t10, tg, dg) - np.interp(tb10, tg, dg))
            if tb50 is not None and t50 is not None:
                r["d50_s"] = t50 - tb50
                r["d50_m"] = float(np.interp(t50, tg, dg) - np.interp(tb50, tg, dg))
            res[name] = r
        rows.append(res)
    return rows


# ---------------------------------------------------------------- checks
def gps_identity():
    out = {}
    for k in range(1, N30 + 1):
        b, a = load(BASE, R30, k), load(CUR, R30, k)
        rec = {}
        for key in GPS_ARRAYS:
            rec[key] = (b[key] == a[key]) and len(b[key]) == len(a[key])
        rec["arrays_changed"] = sorted(key for key in b if isinstance(b[key], list) and b[key] != a[key])
        mb, ma = dict(b["meta"]), dict(a["meta"])
        cta = ma.pop("channel_time_alignment", None)
        rec["meta_other_keys_identical"] = mb == ma
        rec["shiftSeconds"] = cta["shiftSeconds"] if cta else None
        rec["estimate_basis"] = (cta or {}).get("estimate", {}).get("basis")
        out[f"lap{k}"] = rec
    return out


def untouched_0729():
    out = {}
    for k in range(1, N29 + 1):
        rel = f"public/data/races/{R29}/osaki_hmr_demio_101_lap_{k:03d}.json"
        head = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True).stdout
        cur = (ROOT / rel).read_bytes()
        same_bytes = sha(head) == sha(cur)
        same_json = json.loads(head) == json.loads(cur) if head else False
        out[f"lap{k}"] = {"sha_head": sha(head)[:16], "sha_cur": sha(cur)[:16], "bytes_identical": same_bytes,
                          "json_identical": same_json,
                          "has_channel_time_alignment": "channel_time_alignment" in json.loads(cur)["meta"]}
    for f in ("laps.json", "gps_registration.json"):
        b = (BASE / R29 / f).read_bytes()
        c = (CUR / R29 / f).read_bytes()
        out[f"baseline_vs_current_{f}"] = sha(b) == sha(c)
    st = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", f"public/data/races/{R29}"],
                        capture_output=True, text=True).stdout.strip().splitlines()
    out["git_status_fuji_aim_01"] = st
    return out


PAIRS = [
    # name, ref key, sig key, evidence class
    ("accx_vs_gps_along", "gps_along", "accx", "circular: accx was used to estimate the shift"),
    ("accy_vs_gps_lat", "gps_lat", "accy", "circular: accy was used to estimate the shift"),
    ("brake_vs_gps_decel", "gps_decel", "brake", "independent: brake not used in the estimate"),
    ("steer_vs_gps_rate", "gps_rate", "steer", "independent: steer not used in the estimate"),
    ("steer_vs_gps_curv", "gps_curv", "steer", "independent: steer not used in the estimate"),
    ("aps_vs_gps_along", "gps_along", "aps", "independent (weak physics: drivetrain response)"),
    ("brake_vs_accx_decel", "accx", "brake", "logger-logger control: must not change"),
    ("steer_vs_accy", "accy", "steer", "logger-logger control: must not change"),
]


def lag_table(base, race, n, signs):
    rows = {}
    for k in range(1, n + 1):
        sd = derive(load(base, race, k))
        rec = {}
        for name, rk, sk, _ in PAIRS:
            ref = sd[rk]
            if name == "brake_vs_accx_decel":
                ref = -signs["accx"] * sd["accx"]
            tau, r, sg = xcorr_lag(sd["t"], ref, sd[sk], sign=signs.get(name))
            rec[name] = {"tau_s": round(tau, 4), "r": round(r, 3), "sign": sg}
        rows[f"lap{k}"] = rec
    return rows


def summarize(tab):
    names = [p[0] for p in PAIRS]
    out = {}
    for nm in names:
        v = np.array([tab[l][nm]["tau_s"] for l in tab])
        out[nm] = {"median": round(float(np.median(v)), 4), "mean": round(float(v.mean()), 4),
                   "min": round(float(v.min()), 4), "max": round(float(v.max()), 4), "n": len(v)}
    return out


def ev_summary(rows_by_lap):
    out = {}
    for src in ("gps", "imu"):
        for key in ("d10_s", "d10_m", "d50_s", "d50_m"):
            vals = [e[src][key] for lap in rows_by_lap.values() for e in lap if e.get(src) and key in e[src]]
            if vals:
                v = np.array(vals)
                out[f"{src}_{key}"] = {"median": round(float(np.median(v)), 3),
                                       "p25": round(float(np.percentile(v, 25)), 3),
                                       "p75": round(float(np.percentile(v, 75)), 3), "n": len(v)}
    return out


def main():
    res = {"script": Path(__file__).name, "conventions": __doc__}
    res["gps_identity_0730"] = gps_identity()
    res["untouched_0729"] = untouched_0729()

    # fix correlation signs from 7/29 lap 1 so all datasets use the same physical sign
    sd = derive(load(CUR, R29, 1))
    signs = {}
    for name, rk, sk, _ in PAIRS:
        if name == "brake_vs_accx_decel":
            continue
        _, _, sg = xcorr_lag(sd["t"], sd[rk], sd[sk])
        signs[name] = sg
    signs["accx"] = signs["accx_vs_gps_along"]
    signs["brake_vs_accx_decel"] = 1.0
    res["signs"] = signs

    tabs = {
        "ref0729": lag_table(CUR, R29, N29, signs),
        "before0730": lag_table(BASE, R30, N30, signs),
        "after0730": lag_table(CUR, R30, N30, signs),
    }
    res["lags_per_lap"] = tabs
    res["lags_summary"] = {k: summarize(v) for k, v in tabs.items()}

    # per lap: before-after change vs applied shift
    shifts = {f"lap{k}": load(CUR, R30, k)["meta"]["channel_time_alignment"]["shiftSeconds"] for k in range(1, N30 + 1)}
    res["applied_shift_s"] = shifts
    chg = {}
    for nm, *_ in PAIRS:
        chg[nm] = {l: round(tabs["after0730"][l][nm]["tau_s"] - tabs["before0730"][l][nm]["tau_s"] - shifts[l], 4)
                   for l in shifts}
    res["after_minus_before_minus_shift_s"] = chg

    # gap to reference (median over laps)
    gap = {}
    for nm, _, _, ev in PAIRS:
        r = res["lags_summary"]["ref0729"][nm]
        b = res["lags_summary"]["before0730"][nm]
        a = res["lags_summary"]["after0730"][nm]
        gap[nm] = {"evidence": ev, "ref_median": r["median"], "ref_range": [r["min"], r["max"]],
                   "before_median": b["median"], "after_median": a["median"],
                   "before_minus_ref": round(b["median"] - r["median"], 4),
                   "after_minus_ref": round(a["median"] - r["median"], 4),
                   "after_range": [a["min"], a["max"]]}
    res["gap_to_ref"] = gap

    # circularity: does the per-lap shift equal -(mean of accx, accy lags BEFORE) from my own estimate?
    circ = {}
    for l in shifts:
        bx = tabs["before0730"][l]["accx_vs_gps_along"]["tau_s"]
        by = tabs["before0730"][l]["accy_vs_gps_lat"]["tau_s"]
        bb = tabs["before0730"][l]["brake_vs_gps_decel"]["tau_s"]
        bs = tabs["before0730"][l]["steer_vs_gps_rate"]["tau_s"]
        circ[l] = {"applied": shifts[l], "minus_mean_accx_accy_before": round(-(bx + by) / 2, 4),
                   "brake_implied_shift_vs_ref": round(res["lags_summary"]["ref0729"]["brake_vs_gps_decel"]["median"] - bb, 4),
                   "steer_implied_shift_vs_ref": round(res["lags_summary"]["ref0729"]["steer_vs_gps_rate"]["median"] - bs, 4)}
    res["shift_provenance_per_lap"] = circ

    # braking events
    evs = {}
    for label, base, race, n in (("ref0729", CUR, R29, N29), ("before0730", BASE, R30, N30), ("after0730", CUR, R30, N30)):
        per = {}
        for k in range(1, n + 1):
            per[f"lap{k}"] = event_deltas(derive(load(base, race, k)), signs["accx"])
        evs[label] = {"per_lap": per, "summary": ev_summary(per)}
    res["braking_events"] = evs

    OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")

    # console summary
    print("GPS identity 7/30:", {l: all(v[k] for k in GPS_ARRAYS) for l, v in res["gps_identity_0730"].items()})
    print("arrays changed:", {l: v["arrays_changed"] for l, v in res["gps_identity_0730"].items()})
    print("meta other keys identical:", {l: v["meta_other_keys_identical"] for l, v in res["gps_identity_0730"].items()})
    print("7/29 untouched:", json.dumps(res["untouched_0729"]))
    print("signs:", signs)
    print("\n%-22s %8s %16s %8s %8s %9s %9s" % ("pair", "ref", "ref range", "before", "after", "B-ref", "A-ref"))
    for nm, g in gap.items():
        print("%-22s %8.3f [%6.3f,%6.3f] %8.3f %8.3f %9.3f %9.3f  %s" % (
            nm, g["ref_median"], *g["ref_range"], g["before_median"], g["after_median"],
            g["before_minus_ref"], g["after_minus_ref"], g["evidence"]))
    print("\nper-lap tau (s):")
    for label in tabs:
        for l, rec in tabs[label].items():
            print(" %-10s %-5s " % (label, l) + " ".join("%s=%+.3f(r%.2f)" % (nm.split('_vs_')[0] + '/' + nm.split('_vs_')[1][:8], rec[nm]["tau_s"], rec[nm]["r"]) for nm in rec))
    print("\nafter-before-shift residual:", json.dumps(chg))
    print("\nshift provenance:", json.dumps(circ, indent=0))
    print("\nbraking event summaries:")
    for label in evs:
        print(" ", label, json.dumps(evs[label]["summary"]))


if __name__ == "__main__":
    main()
