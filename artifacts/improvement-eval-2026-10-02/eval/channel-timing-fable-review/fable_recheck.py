"""Adversarial recheck of the 7/30 channel timing re-alignment.
Independent implementation: linear (not cubic) upsampling to 100 Hz, normalised
cross-correlation, argmax on a 10 ms grid with parabolic refinement.
Convention: tau = argmax corr(gpsref(t), ch(t+tau)); negative = channel early.
"""
import json, glob, os, sys, hashlib
import numpy as np

ROOT = r"D:\00_Dev\ReplayViewer"
BASE = os.path.join(ROOT, "artifacts/improvement-eval-2026-10-02/baseline/public/data/races")
CUR = os.path.join(ROOT, "public/data/races")
OUT = os.path.join(ROOT, "artifacts/improvement-eval-2026-10-02/eval/channel-timing-fable-review")

def load(p):
    return json.load(open(p, encoding="utf-8"))

def enu(lat, lng):
    lat = np.asarray(lat); lng = np.asarray(lng)
    lat0 = lat.mean(); lng0 = lng.mean()
    R = 6378137.0
    x = np.radians(lng - lng0) * R * np.cos(np.radians(lat0))
    y = np.radians(lat - lat0) * R
    return x, y

def gps_refs(d):
    t = np.asarray(d["t"]); v = np.asarray(d["speed"]) / 3.6
    along = np.gradient(v, t)                      # m/s^2
    x, y = enu(d["lat"], d["lng"])
    hd = np.unwrap(np.arctan2(np.gradient(y, t), np.gradient(x, t)))
    rate = np.gradient(hd, t)                      # rad/s
    lat_acc = v * rate                             # m/s^2 (sign: CCW positive)
    return t, along, rate, lat_acc, v

def xcorr_lag(t, ref, ch, lo=-1.0, hi=0.6, fs=100.0):
    tt = np.arange(t[0], t[-1], 1.0/fs)
    r = np.interp(tt, t, ref); c = np.interp(tt, t, ch)
    r = (r - r.mean()); c = (c - c.mean())
    if r.std() == 0 or c.std() == 0: return np.nan, np.nan
    taus = np.arange(lo, hi + 1e-9, 1.0/fs)
    best = []
    for tau in taus:
        k = int(round(tau * fs))
        if k >= 0:
            a = r[:len(r)-k] if k else r; b = c[k:]
        else:
            a = r[-k:]; b = c[:len(c)+k]
        best.append(np.corrcoef(a, b)[0, 1])
    best = np.asarray(best)
    i = int(np.nanargmax(best))
    tau = taus[i]
    if 0 < i < len(taus)-1:
        y0, y1, y2 = best[i-1], best[i], best[i+1]
        den = (y0 - 2*y1 + y2)
        if den != 0:
            tau = taus[i] + 0.5*(y0 - y2)/den * (1.0/fs)
    return float(tau), float(best[i])

def lap_lags(d):
    t, along, rate, lat_acc, v = gps_refs(d)
    accx = np.asarray(d["accx"]); accy = np.asarray(d["accy"])
    brake = np.asarray(d["brake"]); steer = np.asarray(d["steer"]); aps = np.asarray(d["aps"])
    out = {}
    out["accx_vs_gps_along"] = xcorr_lag(t, along, accx)
    # accy sign unknown a priori: pick the sign with the higher |corr|
    p = xcorr_lag(t, lat_acc, accy); n = xcorr_lag(t, -lat_acc, accy)
    out["accy_vs_gps_lat"] = p if p[1] >= n[1] else n
    out["brake_vs_gps_decel"] = xcorr_lag(t, np.clip(-along, 0, None), brake)
    p = xcorr_lag(t, rate, steer); n = xcorr_lag(t, -rate, steer)
    out["steer_vs_gps_rate"] = p if p[1] >= n[1] else n
    out["aps_vs_gps_along"] = xcorr_lag(t, np.clip(along, 0, None), aps)
    # logger-vs-logger control (should not move with a common shift)
    out["brake_vs_accx_ctrl"] = xcorr_lag(t, np.clip(-accx, 0, None), brake)
    return out

def extrema(d):
    return {k: (float(np.min(d[k])), float(np.max(d[k])), float(np.abs(d[k]).max())) for k in ("accx","accy","brake","steer","aps")}

def main():
    res = {"integrity": {}, "lags": {}, "extrema": {}, "meta_alignment": {}}
    # ---- integrity
    for lap in range(1, 7):
        fn = f"fuji_aim_2020_07_30/osaki_hmr_demio_101_lap_{lap:03d}.json"
        b = load(os.path.join(BASE, fn)); a = load(os.path.join(CUR, fn))
        same = {k: (b[k] == a[k]) for k in b if k != "meta"}
        mb = dict(b["meta"]); ma = dict(a["meta"])
        cta = ma.pop("channel_time_alignment", None)
        res["integrity"][f"0730_lap{lap}"] = {"arrays_equal": same, "meta_equal_excluding_cta": mb == ma,
                                              "meta_diff_keys": sorted(k for k in set(mb)|set(ma) if mb.get(k) != ma.get(k))}
        res["meta_alignment"][f"lap{lap}"] = cta
        res["extrema"][f"0730_lap{lap}"] = {"before": extrema(b), "after": extrema(a)}
    # 7/29 untouched vs baseline
    for lap in range(1, 5):
        fn = f"fuji_aim_01/osaki_hmr_demio_101_lap_{lap:03d}.json"
        import subprocess
        hb = hashlib.sha256(subprocess.run(["git","show",f"HEAD:public/data/races/{fn}"],cwd=ROOT,capture_output=True).stdout).hexdigest()
        ha = hashlib.sha256(open(os.path.join(CUR, fn), "rb").read()).hexdigest()
        res["integrity"][f"0729_lap{lap}_sha_equal_gitHEAD"] = (hb == ha)
    # ---- lags
    for tag, folder, race, laps in (("before0730", BASE, "fuji_aim_2020_07_30", range(1,7)),
                                    ("after0730", CUR, "fuji_aim_2020_07_30", range(1,7)),
                                    ("ref0729", CUR, "fuji_aim_01", range(1,5))):
        res["lags"][tag] = {}
        for lap in laps:
            d = load(os.path.join(folder, f"{race}/osaki_hmr_demio_101_lap_{lap:03d}.json"))
            res["lags"][tag][f"lap{lap}"] = {k: {"tau_s": round(v[0], 4), "r": round(v[1], 3)} for k, v in lap_lags(d).items()}
    # ---- summary medians
    summ = {}
    keys = list(next(iter(res["lags"]["ref0729"].values())).keys())
    for k in keys:
        row = {}
        for tag in ("before0730", "after0730", "ref0729"):
            vals = [res["lags"][tag][l][k]["tau_s"] for l in res["lags"][tag]]
            row[tag] = {"median": round(float(np.median(vals)), 4), "min": round(min(vals), 4), "max": round(max(vals), 4)}
        row["before_minus_ref"] = round(row["before0730"]["median"] - row["ref0729"]["median"], 4)
        row["after_minus_ref"] = round(row["after0730"]["median"] - row["ref0729"]["median"], 4)
        summ[k] = row
    # per-lap after-before vs applied shift
    applied = {l: res["meta_alignment"][l]["shiftSeconds"] if res["meta_alignment"][l] else None for l in res["meta_alignment"]}
    fid = {}
    for k in keys:
        fid[k] = {l: round(res["lags"]["after0730"][l][k]["tau_s"] - res["lags"]["before0730"][l][k]["tau_s"], 4) for l in res["lags"]["after0730"]}
    res["summary"] = summ
    res["after_minus_before_per_lap"] = fid
    res["applied_shift"] = applied
    json.dump(res, open(os.path.join(OUT, "fable_recheck_results.json"), "w"), indent=1)
    print(json.dumps({"summary": summ, "after_minus_before": fid, "applied": applied}, indent=1))
    print("INTEGRITY:", json.dumps(res["integrity"], indent=0)[:3000])
    print("EXTREMA accy/accx (before -> after):")
    for l, e in res["extrema"].items():
        print(l, "accy|max| %.3f -> %.3f  accx min %.3f -> %.3f  accx max %.3f -> %.3f  brake max %.1f -> %.1f  steer|max| %.1f -> %.1f" % (
            e["before"]["accy"][2], e["after"]["accy"][2], e["before"]["accx"][0], e["after"]["accx"][0], e["before"]["accx"][1], e["after"]["accx"][1],
            e["before"]["brake"][1], e["after"]["brake"][1], e["before"]["steer"][2], e["after"]["steer"][2]))

main()
