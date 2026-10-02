#!/usr/bin/env python
"""Independent evaluator re-check of the 7/30 logger-channel re-timing (GPS-REG-08).

Written from scratch for the 2026-10-02 improvement evaluation. It does NOT import
or read outputs of scripts/quality/align-fuji-channel-timing.py or of
artifacts/improvement-eval-2026-10-02/after/channel-timing/.

Inputs (read-only):
  BEFORE 7/30 : artifacts/improvement-eval-2026-10-02/baseline/public/data/races/fuji_aim_2020_07_30/*.json
  AFTER  7/30 : public/data/races/fuji_aim_2020_07_30/*.json
  7/29 (ref)  : public/data/races/fuji_aim_01/*.json (+ git HEAD for the untouched check)
  optional    : $APEX_WORK/xrk_<race>.npz (raw XRK channels on logger ms, lap->tc0 map)

Lag convention (all lags): tau = argmax_tau corr(ref(t), sig(t + tau)).
  tau > 0  -> sig features occur LATER than the GPS-derived reference.
  A re-timing channel(t) = logger(t - s) therefore moves tau by +s.
Event convention: delta = t(GPS/IMU decel onset) - t(brake onset).

Output: results.json next to this script.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d
from scipy.signal import fftconvolve

ROOT = Path(r"D:/00_Dev/ReplayViewer")
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races"
CUR = ROOT / "public/data/races"
OUT = Path(__file__).resolve().parent
APEX = Path(os.environ["APEX_WORK"]) if os.environ.get("APEX_WORK") else None
R30, R29 = "fuji_aim_2020_07_30", "fuji_aim_01"
LAPS = {R30: list(range(1, 7)), R29: list(range(1, 5))}
G = 9.80665
DT = 0.002          # cubic upsampling grid [s]
MAXLAG = 1.0        # lag search half-range [s]
TRIM = 1.2          # edge trim per lap [s] (> MAXLAG)
GPS_KEYS = ["t", "lat", "lng", "speed", "dist"]
CH_KEYS = ["aps", "brake", "steer", "gear", "accx", "accy"]
RNG = np.random.default_rng(20261002)


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def lapfile(root: Path, race: str, k: int) -> Path:
    return root / race / f"osaki_hmr_demio_101_lap_{k:03d}.json"


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def git_head_bytes(relpath: str) -> bytes | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{relpath}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_lf(b: bytes) -> str:
    """sha256 after CRLF->LF (the checkout uses core.autocrlf=true)."""
    return sha(b.replace(bytes([13, 10]), bytes([10])))


def diff_paths(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k} (added/removed)")
            else:
                out += diff_paths(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i in range(len(a)) for x in diff_paths(a[i], b[i], f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {str(a)[:60]} -> {str(b)[:60]}"]


# --------------------------------------------------------------------------- integrity
def integrity() -> dict:
    res = {"0730_laps": {}, "0729_files": {}}
    for k in LAPS[R30]:
        pb, pa = lapfile(BASE, R30, k), lapfile(CUR, R30, k)
        b, a = load(pb), load(pa)
        head = git_head_bytes(rel(pa))
        rec = {"baseline_bytes_eq_git_HEAD": head is not None and sha_lf(head) == sha_lf(pb.read_bytes())}
        for key in GPS_KEYS:
            xa = np.asarray(a[key], dtype=np.float64)
            xb = np.asarray(b[key], dtype=np.float64)
            rec[f"{key}_bit_identical"] = bool(xa.shape == xb.shape and xa.tobytes() == xb.tobytes())
        for key in CH_KEYS:
            xa = np.asarray(a[key], dtype=np.float64)
            xb = np.asarray(b[key], dtype=np.float64)
            rec[f"{key}_frac_samples_changed"] = round(float(np.mean(xa != xb)), 4) if xa.shape == xb.shape else "shape-differs"
        mb, ma = b["meta"], a["meta"]
        rec["meta_keys_added"] = sorted(set(ma) - set(mb))
        rec["meta_keys_removed"] = sorted(set(mb) - set(ma))
        rec["meta_keys_changed"] = sorted(x for x in set(ma) & set(mb) if ma[x] != mb[x])
        rec["point_count_equal"] = len(a["t"]) == len(b["t"])
        res["0730_laps"][k] = rec
    # laps.json / gps_registration.json for 7/30 (informative)
    for name in ["laps.json", "gps_registration.json"]:
        b, a = load(BASE / R30 / name), load(CUR / R30 / name)
        res[f"0730_{name}_equal_to_baseline"] = a == b
        res[f"0730_{name}_changed_paths"] = diff_paths(b, a)[:40]
    # 7/29: working tree vs git HEAD and vs baseline snapshot
    files = [lapfile(CUR, R29, k) for k in LAPS[R29]] + [CUR / R29 / "laps.json", CUR / R29 / "gps_registration.json"]
    for p in files:
        head = git_head_bytes(rel(p))
        last = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %ci", "--", rel(p)],
                              capture_output=True, text=True).stdout.strip()
        rec = {"bytes_eq_git_HEAD_lf_normalised": head is not None and sha_lf(head) == sha_lf(p.read_bytes()),
               "bytes_eq_git_HEAD_raw": head is not None and sha(head) == sha(p.read_bytes()),
               "last_commit": last}
        bp = BASE / R29 / p.name
        if bp.exists():
            rec["bytes_eq_baseline_snapshot"] = sha_lf(bp.read_bytes()) == sha_lf(p.read_bytes())
        res["0729_files"][p.name] = rec
    return res


# --------------------------------------------------------------------------- re-timing reproduction
def abs_t(d: dict) -> np.ndarray:
    t0 = datetime.fromisoformat(d["meta"]["first_sample_time"].replace("Z", "+00:00")).timestamp()
    return t0 + np.asarray(d["t"], dtype=np.float64)


def reproduce_transform() -> dict:
    """Recompute AFTER channels from BEFORE channels with the declared per-lap shift."""
    base = [load(lapfile(BASE, R30, k)) for k in LAPS[R30]]
    T = np.concatenate([abs_t(d) for d in base])
    order = np.argsort(T)
    T = T[order]
    gaps = np.diff(T)
    out = {"concat_dt_min": round(float(gaps.min()), 4), "concat_dt_max": round(float(gaps.max()), 4), "laps": {}}
    for k in LAPS[R30]:
        a = load(lapfile(CUR, R30, k))
        s = float(a["meta"]["channel_time_alignment"]["shiftSeconds"])
        ta = abs_t(a) - s
        ok = (ta >= T[0]) & (ta <= T[-1])
        rec = {"declared_shift_s": s, "samples_checked": int(ok.sum()), "samples_outside_baseline": int((~ok).sum())}
        for key in CH_KEYS:
            src = np.concatenate([np.asarray(d[key], dtype=np.float64) for d in base])[order]
            if key == "gear":
                idx = np.clip(np.searchsorted(T, ta[ok]), 1, len(T) - 1)
                left = T[idx - 1]
                pick = np.where(np.abs(ta[ok] - left) <= np.abs(T[idx] - ta[ok]), idx - 1, idx)
                exp = src[pick]
            else:
                exp = np.interp(ta[ok], T, src)
            got = np.asarray(a[key], dtype=np.float64)[ok]
            rec[f"{key}_max_abs_diff"] = round(float(np.max(np.abs(got - exp))), 5)
        out["laps"][k] = rec
    return out


# --------------------------------------------------------------------------- signal helpers
def gps_derived(d: dict) -> dict:
    t = np.asarray(d["t"], dtype=np.float64)
    lat = np.radians(np.asarray(d["lat"], dtype=np.float64))
    lng = np.radians(np.asarray(d["lng"], dtype=np.float64))
    R = 6378137.0
    lat0 = lat.mean()
    x = R * np.cos(lat0) * (lng - lng.mean())
    y = R * (lat - lat0)
    vx, vy = np.gradient(x, t), np.gradient(y, t)
    psi = np.unwrap(np.arctan2(vx, vy))           # bearing, + clockwise
    r = np.gradient(psi, t)                       # rad/s, + = right turn
    v = np.asarray(d["speed"], dtype=np.float64) / 3.6
    return {"t": t, "v": v, "a_lon": np.gradient(v, t) / G, "a_lat": v * r / G,
            "yaw_deg_s": np.degrees(r), "kappa": r / np.maximum(v, 5.0),
            "dist": np.asarray(d["dist"], dtype=np.float64)}


def grid(t: np.ndarray) -> np.ndarray:
    return np.arange(t[0], t[-1], DT)


def up(t: np.ndarray, x, tt: np.ndarray) -> np.ndarray:
    return CubicSpline(t, np.asarray(x, dtype=np.float64))(tt)


def xcorr_lag(ref: np.ndarray, sig: np.ndarray, maxlag: float = MAXLAG, trim: float = TRIM) -> tuple[float, float]:
    """tau maximising |corr(ref(t), sig(t+tau))| on a uniform DT grid; parabolic sub-grid refinement."""
    m, tr = int(round(maxlag / DT)), int(round(trim / DT))
    i0, i1 = tr, len(ref) - tr
    rs = ref[i0:i1]
    rs = (rs - rs.mean()) / rs.std()
    n = len(rs)
    lags = np.arange(-m, m + 1)
    seg = np.asarray(sig[i0 - m:i1 + m], dtype=np.float64)
    num = fftconvolve(seg, rs[::-1], mode="valid")          # num[j] = sum_i seg[j+i] * rs[i]
    c1 = np.concatenate([[0.0], np.cumsum(seg)])
    c2 = np.concatenate([[0.0], np.cumsum(seg * seg)])
    s1 = c1[n:] - c1[:-n]
    s2 = c2[n:] - c2[:-n]
    sd = np.sqrt(np.maximum(s2 / n - (s1 / n) ** 2, 1e-30))
    cc = num / (n * sd)
    sgn = 1.0 if cc[np.argmax(np.abs(cc))] > 0 else -1.0
    c = sgn * cc
    j = int(np.argmax(c))
    delta = 0.0
    if 0 < j < len(c) - 1:
        den = c[j - 1] - 2 * c[j] + c[j + 1]
        if den != 0:
            delta = 0.5 * (c[j - 1] - c[j + 1]) / den
    return float((lags[j] + delta) * DT), float(sgn * c[j])


def crossings(t: np.ndarray, x: np.ndarray, thr: float, rising: bool) -> np.ndarray:
    y = x - thr if rising else thr - x
    i = np.where((y[:-1] < 0) & (y[1:] >= 0))[0]
    return t[i] + (t[i + 1] - t[i]) * (-y[i]) / (y[i + 1] - y[i])


def med_ci(x) -> dict:
    x = np.asarray([v for v in x if np.isfinite(v)], dtype=np.float64)
    if len(x) == 0:
        return {"n": 0}
    boots = np.median(RNG.choice(x, size=(4000, len(x)), replace=True), axis=1)
    return {"median": round(float(np.median(x)), 3), "ci95": [round(float(np.percentile(boots, 2.5)), 3),
            round(float(np.percentile(boots, 97.5)), 3)], "iqr": [round(float(np.percentile(x, 25)), 3),
            round(float(np.percentile(x, 75)), 3)], "n": int(len(x))}


# --------------------------------------------------------------------------- per-lap lag metrics (lap JSONs only)
def lap_lags(d: dict) -> dict:
    g = gps_derived(d)
    t = g["t"]
    tt = grid(t)
    res = {}
    pairs = {"accx_vs_gps_along": ("accx", "a_lon"), "accy_vs_gps_lateral": ("accy", "a_lat"),
             "steer_vs_gps_curvature": ("steer", "kappa"), "steer_vs_gps_course_rate": ("steer", "yaw_deg_s")}
    for name, (ch, ref) in pairs.items():
        tau, r = xcorr_lag(up(t, g[ref], tt), up(t, d[ch], tt))
        res[name] = {"lag_s": round(tau, 4), "r": round(r, 4)}
    return res


# --------------------------------------------------------------------------- braking events (lap JSONs only)
B_ON, B_QUIET, B_PEAK = 5.0, 2.0, 20.0     # bar
A_THRS = (-0.3, -0.5)                       # g


def brake_events(d: dict, lap: int) -> list[dict]:
    g = gps_derived(d)
    t = g["t"]
    brake = np.asarray(d["brake"], dtype=np.float64)
    accx = np.asarray(d["accx"], dtype=np.float64)
    tt = grid(t)
    alon_gps = up(t, g["a_lon"], tt)
    alon_imu = up(t, accx, tt)
    ev = []
    for tb in crossings(t, brake, B_ON, rising=True):
        pre = (t >= tb - 1.0) & (t < tb - 0.25)
        post = (t >= tb) & (t <= tb + 1.5)
        if tb < t[0] + 2.0 or tb > t[-1] - 2.5 or brake[pre].max(initial=0) > B_QUIET or brake[post].max(initial=0) < B_PEAK:
            continue
        e = {"lap": lap, "t_brake": round(float(tb), 3), "v_kmh": round(float(np.interp(tb, t, g["v"]) * 3.6), 1)}
        win = (tt >= tb - 1.5) & (tt <= tb + 2.0)
        for thr in A_THRS:
            for label, sig in (("gps", alon_gps), ("imu", alon_imu)):
                c = crossings(tt[win], sig[win], thr, rising=False)
                # first downward crossing whose signal stayed above thr for the preceding 0.3 s
                tc = np.nan
                for cand in c:
                    pre_w = (tt >= cand - 0.3) & (tt < cand - DT)
                    if np.all(sig[pre_w] > thr):
                        tc = float(cand)
                        break
                e[f"{label}_{abs(thr):.1f}g_dt_s"] = round(tc - tb, 4) if np.isfinite(tc) else None
                if label == "gps" and np.isfinite(tc):
                    e[f"gps_{abs(thr):.1f}g_dd_m"] = round(float(np.interp(tc, t, g["dist"]) - np.interp(tb, t, g["dist"])), 2)
        ev.append(e)
    return ev


def summarise_events(ev: list[dict]) -> dict:
    out = {}
    for key in sorted({k for e in ev for k in e if k.endswith("_dt_s") or k.endswith("_dd_m")}):
        out[key] = med_ci([e[key] for e in ev if e.get(key) is not None])
    return out


# --------------------------------------------------------------------------- raw-XRK cross-checks (optional)
def xrk_checks(race: str, laps: dict[int, dict], before: dict[int, dict] | None) -> dict | None:
    f = APEX / f"xrk_{race}.npz" if APEX else None
    if f is None or not f.exists():
        return None
    z = np.load(f)
    maps = json.loads(str(z["maps"]))

    def chan(name, tc0, tt):
        ts, v = z[name + "_t"], z[name]
        fin = np.isfinite(v)
        ts, v = ts[fin], v[fin]
        x = (ts - tc0) / 1000.0
        sel = (x >= tt[0] - 1.0) & (x <= tt[-1] + 1.0)
        return CubicSpline(x[sel], v[sel])(tt)

    res = {}
    for k, d in laps.items():
        if str(k) not in maps:
            continue
        tc0 = maps[str(k)]["tc0"]
        g = gps_derived(d)
        t = g["t"]
        tt = grid(t)
        sm = lambda x: gaussian_filter1d(x, sigma=0.08 / DT)  # identical zero-phase smoothing for both sides
        v_gps = sm(up(t, g["v"] * 3.6, tt))
        ws_rear = sm(0.5 * (chan("ECU_WS_RL", tc0, tt) + chan("ECU_WS_RR", tc0, tt)))
        ws_all = sm(0.25 * sum(chan(c, tc0, tt) for c in ("ECU_WS_FL", "ECU_WS_FR", "ECU_WS_RL", "ECU_WS_RR")))
        yaw_gps = sm(up(t, g["yaw_deg_s"], tt))
        gyro = sm(chan("YawRate", tc0, tt))
        rec = {}
        tau, r = xcorr_lag(np.gradient(v_gps, DT), np.gradient(ws_rear, DT))
        rec["rear_wheelspeed_accel_vs_gps_speed_accel"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        tau, r = xcorr_lag(np.gradient(v_gps, DT), np.gradient(ws_all, DT))
        rec["mean4_wheelspeed_accel_vs_gps_speed_accel"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        tau, r = xcorr_lag(v_gps, ws_rear)
        rec["rear_wheelspeed_vs_gps_speed"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        tau, r = xcorr_lag(yaw_gps, gyro)
        rec["gyro_yawrate_vs_gps_course_rate"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        # where do the shipped channels sit relative to the raw XRK logger channels?
        tau, r = xcorr_lag(chan("InlineAcc", tc0, tt), up(t, d["accx"], tt))
        rec["shipped_accx_vs_raw_xrk_InlineAcc"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        tau, r = xcorr_lag(chan("ECU_STEER_ANG", tc0, tt), up(t, d["steer"], tt))
        rec["shipped_steer_vs_raw_xrk_steer"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        if before is not None:
            tau, r = xcorr_lag(chan("InlineAcc", tc0, tt), up(t, before[k]["accx"], tt))
            rec["baseline_accx_vs_raw_xrk_InlineAcc"] = {"lag_s": round(tau, 4), "r": round(r, 4)}
        res[k] = rec
    return res


# --------------------------------------------------------------------------- derived comparisons
def paired_events(before: list[dict], after: list[dict], shifts: dict) -> dict:
    """Match BEFORE/AFTER brake events (same lap, onset moved by the declared shift)."""
    pairs = []
    for eb in before:
        cand = [ea for ea in after if ea["lap"] == eb["lap"] and abs(ea["t_brake"] - eb["t_brake"] - shifts[eb["lap"]]) < 0.15]
        if cand:
            pairs.append((eb, cand[0]))
    out = {"n_before": len(before), "n_after": len(after), "n_paired": len(pairs),
           "onset_move_minus_declared_shift_s": med_ci([ea["t_brake"] - eb["t_brake"] - shifts[eb["lap"]] for eb, ea in pairs])}
    for key in ("gps_0.3g_dt_s", "imu_0.3g_dt_s"):
        d = [ea[key] - eb[key] for eb, ea in pairs if ea.get(key) is not None and eb.get(key) is not None]
        out[f"after_minus_before_{key}"] = med_ci(d)
    return out


def corner_of(dist_m: float, centres: list[float]) -> int:
    return int(np.argmin([abs(dist_m - c) for c in centres]))


def corner_matched(events: dict, data: dict) -> dict:
    """Group brake events by lap distance of the onset (corner) and compare medians per corner."""
    def onset_dist(state, e):
        d = data[state][e["lap"]]
        return float(np.interp(e["t_brake"], np.asarray(d["t"], float), np.asarray(d["dist"], float)))
    allx = sorted(onset_dist(s, e) for s in events for e in events[s])
    centres, cur = [], [allx[0]]
    for x in allx[1:]:
        if x - cur[-1] > 60:
            centres.append(float(np.median(cur)))
            cur = [x]
        else:
            cur.append(x)
    centres.append(float(np.median(cur)))
    table = {}
    for s in events:
        for e in events[s]:
            c = corner_of(onset_dist(s, e), centres)
            for key in ("gps_0.3g_dt_s", "imu_0.3g_dt_s", "gps_0.3g_dd_m"):
                if e.get(key) is not None:
                    table.setdefault(c, {}).setdefault(s, {}).setdefault(key, []).append(e[key])
    out = {"corner_onset_dist_m": [round(c) for c in centres], "per_corner": {}, "after_minus_0729_gps_0.3g_dt_s_per_corner": {}}
    diffs = []
    for c, v in sorted(table.items()):
        out["per_corner"][c] = {s: {k: [round(float(np.median(x)), 3), len(x)] for k, x in kv.items()} for s, kv in v.items()}
        if "0730_after" in v and "0729" in v and "gps_0.3g_dt_s" in v["0730_after"] and "gps_0.3g_dt_s" in v["0729"]:
            dd = float(np.median(v["0730_after"]["gps_0.3g_dt_s"]) - np.median(v["0729"]["gps_0.3g_dt_s"]))
            out["after_minus_0729_gps_0.3g_dt_s_per_corner"][c] = round(dd, 3)
            diffs.append(dd)
    out["after_minus_0729_gps_0.3g_dt_s_median_over_corners"] = round(float(np.median(diffs)), 3) if diffs else None
    return out


def lapjson_metric_summary(lags: dict) -> dict:
    """Residual of 7/30 AFTER vs the 7/29 reference, and the session shift each metric alone would imply."""
    out = {}
    for name in lags["0729"][1]:
        ref = np.array([v[name]["lag_s"] for v in lags["0729"].values()])
        bef = np.array([v[name]["lag_s"] for v in lags["0730_before"].values()])
        aft = np.array([v[name]["lag_s"] for v in lags["0730_after"].values()])
        out[name] = {"ref0729_median": round(float(np.median(ref)), 4), "ref0729_range": [round(float(ref.min()), 4), round(float(ref.max()), 4)],
                     "before_median": round(float(np.median(bef)), 4), "after_median": round(float(np.median(aft)), 4),
                     "after_minus_ref_s": round(float(np.median(aft) - np.median(ref)), 4),
                     "before_minus_ref_s": round(float(np.median(bef) - np.median(ref)), 4),
                     "implied_session_shift_s": round(float(np.median(ref) - np.median(bef)), 4)}
    return out


def xrk_summary(xrk30: dict, xrk29: dict, shifts: dict) -> dict:
    """For raw XRK logger channels: 7/30 raw lag + declared shift = lag the shipped AFTER timing implies."""
    out = {}
    for name in ("gyro_yawrate_vs_gps_course_rate", "rear_wheelspeed_vs_gps_speed",
                 "rear_wheelspeed_accel_vs_gps_speed_accel", "mean4_wheelspeed_accel_vs_gps_speed_accel"):
        ref = np.array([v[name]["lag_s"] for v in xrk29.values()])
        raw = {k: v[name]["lag_s"] for k, v in xrk30.items()}
        laps = sorted(raw)
        implied = np.array([raw[k] + shifts[k] for k in laps])
        const = float(np.median([shifts[k] for k in laps]))
        implied_const = np.array([raw[k] + const for k in laps])
        rref = float(np.median(ref))
        out[name] = {"ref0729_median": round(rref, 4), "ref0729_per_lap": [round(float(x), 4) for x in ref],
                     "raw0730_per_lap": [round(raw[k], 4) for k in laps],
                     "implied_after_per_lap": [round(float(x), 4) for x in implied],
                     "after_minus_ref_median_s": round(float(np.median(implied) - rref), 4),
                     "after_minus_ref_range_s": [round(float(implied.min() - rref), 4), round(float(implied.max() - rref), 4)],
                     "before_minus_ref_median_s": round(float(np.median([raw[k] for k in laps]) - rref), 4),
                     "ideal_shift_per_lap_s": [round(rref - raw[k], 4) for k in laps],
                     "residual_std_per_lap_shift": round(float(np.std(implied)), 4),
                     "residual_std_if_constant_shift": round(float(np.std(implied_const)), 4),
                     "corr_raw_lag_vs_declared_shift": round(float(np.corrcoef([raw[k] for k in laps], [shifts[k] for k in laps])[0, 1]), 3)}
    return out


def intra_logger(laps: dict, race: str) -> dict:
    """Logger-internal lags (both signals on the logger clock -> unaffected by a uniform re-timing).
    Day-to-day differences here are vehicle dynamics / conditions, not clock: they bound how far a
    7/29-referenced residual can be attributed to timing."""
    z = np.load(APEX / f"xrk_{race}.npz") if APEX and (APEX / f"xrk_{race}.npz").exists() else None
    maps = json.loads(str(z["maps"])) if z is not None else {}
    out = {}
    for k, d in laps.items():
        t = np.asarray(d["t"], dtype=np.float64)
        tt = grid(t)
        accy = up(t, d["accy"], tt)
        rec = {"steer_vs_accy": round(xcorr_lag(accy, up(t, d["steer"], tt))[0], 4)}
        if z is not None and str(k) in maps:
            tc0 = maps[str(k)]["tc0"]
            ts, v = z["YawRate_t"], z["YawRate"]
            x = (ts - tc0) / 1000.0
            sel = (x >= tt[0] - 1) & (x <= tt[-1] + 1)
            gyro = CubicSpline(x[sel], v[sel])(tt)
            vv = up(t, np.asarray(d["speed"], float) / 3.6, tt)
            rec["gyro_x_speed_vs_accy"] = round(xcorr_lag(accy, gyro * vv)[0], 4)
            rec["steer_vs_gyro"] = round(xcorr_lag(gyro, up(t, d["steer"], tt))[0], 4)
        out[k] = rec
    names = sorted({n for r in out.values() for n in r})
    return {"per_lap": out, "median": {n: round(float(np.median([r[n] for r in out.values() if n in r])), 4) for n in names}}


def build_summary(res: dict) -> dict:
    """Timing error of 7/30 relative to the 7/29 reference, BEFORE vs AFTER, per evidence line.
    Sign: lag metrics -> negative = logger channel still EARLY vs GPS; brake->GPS metrics -> positive = brake still early."""
    lj, bs, bc = res["lapjson_metric_vs_0729"], res["braking_summary"], res["braking_corner_matched"]
    rows = []
    for name, used in (("accx_vs_gps_along", "used to estimate the shift (circular)"),
                       ("accy_vs_gps_lateral", "used to estimate the shift (circular)"),
                       ("steer_vs_gps_curvature", "independent sensor (ECU steering)"),
                       ("steer_vs_gps_course_rate", "independent sensor (ECU steering)")):
        rows.append({"metric": name, "evidence": used, "before_minus_ref_s": lj[name]["before_minus_ref_s"],
                     "after_minus_ref_s": lj[name]["after_minus_ref_s"], "ref0729_range_s": lj[name]["ref0729_range"]})
    for thr in ("0.3", "0.5"):
        k = f"gps_{thr}g_dt_s"
        rows.append({"metric": f"brake_onset_to_gps_decel_{thr}g", "evidence": "independent sensor (ECU brake pressure)",
                     "before_minus_ref_s": round(bs["0730_before"][k]["median"] - bs["0729"][k]["median"], 3),
                     "after_minus_ref_s": round(bs["0730_after"][k]["median"] - bs["0729"][k]["median"], 3),
                     "before_s": bs["0730_before"][k], "after_s": bs["0730_after"][k], "ref0729_s": bs["0729"][k]})
    rows.append({"metric": "brake_onset_to_gps_decel_0.3g_corner_matched", "evidence": "independent sensor (ECU brake pressure)",
                 "after_minus_ref_s": bc["after_minus_0729_gps_0.3g_dt_s_median_over_corners"],
                 "per_corner": bc["after_minus_0729_gps_0.3g_dt_s_per_corner"]})
    for name, v in (res.get("xrk_vs_0729") or {}).items():
        rows.append({"metric": "rawXRK_" + name, "evidence": "independent sensor, raw XRK channel not shipped (implied AFTER = raw + declared shift)",
                     "before_minus_ref_s": v["before_minus_ref_median_s"], "after_minus_ref_s": v["after_minus_ref_median_s"],
                     "after_minus_ref_range_s": v["after_minus_ref_range_s"], "corr_raw_lag_vs_declared_shift": v["corr_raw_lag_vs_declared_shift"],
                     "residual_std_per_lap_vs_constant_shift": [v["residual_std_per_lap_shift"], v["residual_std_if_constant_shift"]]})
    il = res["intra_logger"]
    return {"rows": rows,
            "intra_logger_day_difference_s (0730 minus 0729; clock-free, = dynamics/conditions floor)": {
                "steer_vs_accy": round(il["0730_before"]["median"]["steer_vs_accy"] - il["0729"]["median"]["steer_vs_accy"], 4),
                "gyro_x_speed_vs_accy": round(il["0730_before"]["median"]["gyro_x_speed_vs_accy"] - il["0729"]["median"]["gyro_x_speed_vs_accy"], 4),
                "steer_vs_gyro": round(il["0730_before"]["median"]["steer_vs_gyro"] - il["0729"]["median"]["steer_vs_gyro"], 4),
                "brake_to_imu_decel_0.3g": round(bs["0730_after"]["imu_0.3g_dt_s"]["median"] - bs["0729"]["imu_0.3g_dt_s"]["median"], 3)},
            "gps_untouched": all(all(v[f"{k}_bit_identical"] for k in GPS_KEYS) for v in res["integrity"]["0730_laps"].values()),
            "baseline_equals_git_HEAD": all(v["baseline_bytes_eq_git_HEAD"] for v in res["integrity"]["0730_laps"].values()),
            "0729_untouched": all(v["bytes_eq_git_HEAD_lf_normalised"] for v in res["integrity"]["0729_files"].values()),
            "transform_max_abs_diff": max(v for lap in res["transform_reproduction"]["laps"].values() for kk, v in lap.items() if kk.endswith("_max_abs_diff"))}


# --------------------------------------------------------------------------- main
def lagsummary(per_lap: dict) -> dict:
    names = sorted({n for v in per_lap.values() for n in v})
    out = {}
    for n in names:
        vals = [per_lap[k][n]["lag_s"] for k in per_lap if n in per_lap[k]]
        out[n] = {"median": round(float(np.median(vals)), 4), "min": round(float(min(vals)), 4),
                  "max": round(float(max(vals)), 4), "per_lap": [round(v, 4) for v in vals]}
    return out


def main() -> None:
    res: dict = {"conventions": __doc__.split("Lag convention")[1].split("Output:")[0].strip()}
    res["integrity"] = integrity()
    res["transform_reproduction"] = reproduce_transform()

    data = {"0730_before": {k: load(lapfile(BASE, R30, k)) for k in LAPS[R30]},
            "0730_after": {k: load(lapfile(CUR, R30, k)) for k in LAPS[R30]},
            "0729": {k: load(lapfile(CUR, R29, k)) for k in LAPS[R29]}}
    res["declared_shift_s"] = {k: data["0730_after"][k]["meta"]["channel_time_alignment"]["shiftSeconds"] for k in LAPS[R30]}

    # measured applied shift: AFTER channel vs BEFORE channel (same channel)
    applied = {}
    for k in LAPS[R30]:
        b, a = data["0730_before"][k], data["0730_after"][k]
        t = np.asarray(a["t"], dtype=np.float64)
        tt = grid(t)
        applied[k] = {ch: round(xcorr_lag(up(t, b[ch], tt), up(t, a[ch], tt))[0], 4) for ch in ("accx", "accy", "steer", "brake", "aps")}
    res["measured_applied_shift_s"] = applied

    lags, events = {}, {}
    for state, laps in data.items():
        lags[state] = {k: lap_lags(d) for k, d in laps.items()}
        events[state] = [e for k, d in laps.items() for e in brake_events(d, k)]
    res["lags_per_lap"] = lags
    res["lags_summary"] = {s: lagsummary(v) for s, v in lags.items()}
    res["braking_events"] = events
    res["braking_summary"] = {s: summarise_events(v) for s, v in events.items()}
    res["braking_summary_per_lap_median_gps0.3_dt_s"] = {
        s: {k: (round(float(np.median(x)), 3) if (x := [e["gps_0.3g_dt_s"] for e in v if e["lap"] == k and e.get("gps_0.3g_dt_s") is not None]) else None)
            for k in sorted({e["lap"] for e in v})} for s, v in events.items()}

    res["xrk"] = {"0730_after": xrk_checks(R30, data["0730_after"], data["0730_before"]),
                  "0729": xrk_checks(R29, data["0729"], None),
                  "source": str(APEX / "xrk_<race>.npz") if APEX else None}
    shifts = {k: float(v) for k, v in res["declared_shift_s"].items()}
    res["lapjson_metric_vs_0729"] = lapjson_metric_summary(lags)
    res["braking_paired_before_after"] = paired_events(events["0730_before"], events["0730_after"], shifts)
    res["braking_corner_matched"] = corner_matched(events, data)
    res["intra_logger"] = {"0730_after": intra_logger(data["0730_after"], R30), "0730_before": intra_logger(data["0730_before"], R30),
                           "0729": intra_logger(data["0729"], R29)}
    if res["xrk"]["0730_after"] and res["xrk"]["0729"]:
        res["xrk_vs_0729"] = xrk_summary(res["xrk"]["0730_after"], res["xrk"]["0729"], shifts)
    res["summary"] = build_summary(res)
    (OUT / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(res["summary"], indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res["summary"], indent=1))


if __name__ == "__main__":
    main()
