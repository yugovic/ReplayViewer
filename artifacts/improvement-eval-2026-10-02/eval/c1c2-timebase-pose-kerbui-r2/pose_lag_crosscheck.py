"""Independent cross-check of the playback pose-lag change (C1).

Re-implements, WITHOUT importing any viewer code, the yaw part of
  * the target heading (ReplayScene -> sampleReplay -> samplePose):
    +-6-sample chord heading per 10 Hz sample, unwrapped, linearly interpolated
    in t and clamped to [t0, t_end];
  * BEFORE: one first-order stage, tau = 140 ms, y += (1 - e^{-dt/tau}) (u(t) - y),
    snap when dt <= 0 or dt > 0.25 s;
  * AFTER (src/engine/poseSmoothing.ts as read): 2 cascaded stages, tau = 70 ms,
    stage-1 input u(t + 0.14 s), exact first-order-hold step
        y1 = e^{-x} y0 + (1 - e^{-x}) u0 + (1 - (1 - e^{-x})/x) (u1 - u0),  x = dt/tau
    (derived here from the ODE, not copied), stage 2 driven by stage 1's
    start/end values with the same formula; after a snap, stage 1 is charged
    with u(t_snap + tau) and the previous input with u(t_snap + 2 tau).
Yaw-only scalar simulation (rotation about one axis => slerp == lerp of the
angle). The viewer filters full quaternions incl. pitch (<=8 deg) and track
roll (<=10 deg), so small differences to the implementer's numbers are expected.

Extra metrics the implementer did not report:
  * effective delay: shift s minimising RMS(y(t) - u(t - s));
  * heading vs the drawn PATH tangent (derivative of the viewer's non-uniform
    Catmull-Rom Hermite position), i.e. how far the car's nose points away from
    the direction it is visibly moving (context: the +-0.6 s chord target itself
    is not the path tangent).

Usage: <venv python> pose_lag_crosscheck.py   (writes pose_lag_crosscheck.json next to itself)
"""
import json
import math
import os

import numpy as np

ROOT = r"D:\00_Dev\ReplayViewer"
OUT = os.path.dirname(os.path.abspath(__file__))
SESSIONS = ["fuji_aim_01", "fuji_aim_2020_07_30"]
MPD = 111_320.0
DEG = 180.0 / math.pi
WINDOW = 6

track = json.load(open(os.path.join(ROOT, r"public\data\tracks\fuji\track.json"), encoding="utf-8"))
LAT0, LNG0 = track["origin"]["lat"], track["origin"]["lng"]


def load_laps():
    laps = []
    for race in SESSIONS:
        d = os.path.join(ROOT, "public", "data", "races", race)
        idx = json.load(open(os.path.join(d, "laps.json"), encoding="utf-8"))
        for rec in idx["laps"]:
            if not rec.get("data_file"):
                continue
            lap = json.load(open(os.path.join(d, rec["data_file"]), encoding="utf-8"))
            laps.append((f"{race}/L{lap['meta']['lap']}", lap))
    return laps


def motion(lap):
    t = np.asarray(lap["t"], float)
    lat = np.asarray(lap["lat"], float)
    lng = np.asarray(lap["lng"], float)
    x = (lng - LNG0) * MPD * math.cos(math.radians(LAT0))
    z = -(lat - LAT0) * MPD
    n = len(t)
    h = np.zeros(n)
    prev = 0.0
    for i in range(n):
        s, e = max(0, i - WINDOW), min(n - 1, i + WINDOW)
        dx, dz = x[e] - x[s], z[e] - z[s]
        hh = prev
        if math.hypot(dx, dz) >= 0.001:
            hh = math.atan2(dx, dz)
            while hh - prev > math.pi:
                hh -= 2 * math.pi
            while hh - prev < -math.pi:
                hh += 2 * math.pi
        h[i] = hh
        prev = hh
    return t, x, z, h


def path_heading(t, x, z, q):
    """Direction of motion of the viewer's Hermite position curve at times q."""
    n = len(t)
    q = np.clip(q, t[0], t[-1])
    i = np.clip(np.searchsorted(t, q, side="right") - 1, 0, n - 2)

    def tang(v, k):
        p, nx = np.maximum(k - 1, 0), np.minimum(k + 1, n - 1)
        return (v[nx] - v[p]) / (t[nx] - t[p])

    t0, t1 = t[i], t[i + 1]
    span = t1 - t0
    u = (q - t0) / span
    out = []
    for v in (x, z):
        m0, m1 = tang(v, i) * span, tang(v, i + 1) * span
        dv = ((6 * u * u - 6 * u) * v[i] + (3 * u * u - 4 * u + 1) * m0
              + (-6 * u * u + 6 * u) * v[i + 1] + (3 * u * u - 2 * u) * m1) / span
        out.append(dv)
    return np.arctan2(out[0], out[1])


def frame_times(duration, fps, rate=1.0, jitter=0.0, seed=1, pause_every=0.0):
    rng = np.random.default_rng(seed)
    times = [0.0]
    t = 0.0
    next_pause = pause_every if pause_every > 0 else math.inf
    while t < duration:
        scale = 1.0 + jitter * (2 * rng.random() - 1) if jitter > 0 else 1.0
        t = min(duration, t + rate / fps * scale)
        times.append(t)
        if t >= next_pause:
            times.extend([t] * 10)
            next_pause += pause_every
    return np.asarray(times)


def run_before(H, times, tau=0.14):
    y = np.empty(len(times))
    cur = None
    last = None
    for k, tt in enumerate(times):
        u = H(tt)
        dt = 0.0 if last is None else tt - last
        last = tt
        if not (0 < dt <= 0.25):
            cur = u
        else:
            cur += (1 - math.exp(-dt / tau)) * (u - cur)
        y[k] = cur
    return y


def run_after(H, times, tau=0.07, stages=2):
    look = stages * tau
    y = np.empty(len(times))
    st = [0.0] * (stages - 1)
    out = None
    prev_in = None
    charged = False
    snap_t = None
    last = None
    for k, tt in enumerate(times):
        u = H(tt)
        dt = 0.0 if last is None else tt - last
        last = tt
        if not (0 < dt <= 0.25):
            out, snap_t, charged = u, tt, False
            y[k] = out
            continue
        if not charged:
            st = [H(snap_t + (stages - (j + 1)) * tau) for j in range(stages - 1)]
            prev_in = H(snap_t + look)
            charged = True
        x = dt / tau
        ex = math.exp(-x)
        a = 1 - ex
        g = 1 - a / x  # exact weight of the in-step input change
        u1 = H(tt + look)
        u0 = prev_in
        prev_in = u1
        states = st + [out]
        for j in range(stages):
            s0 = states[j]
            s1 = ex * s0 + a * u0 + g * (u1 - u0)
            states[j] = s1
            u0, u1 = s0, s1  # next stage sees this stage moving s0 -> s1
        st, out = states[:-1], states[-1]
        y[k] = out
    return y


def wrap(a):
    return np.arctan2(np.sin(a), np.cos(a))


def stats(v):
    v = np.asarray(v, float)
    return {"n": int(v.size), "median": round(float(np.median(v)), 4), "p95": round(float(np.quantile(v, 0.95)), 4),
            "p99": round(float(np.quantile(v, 0.99)), 4), "max": round(float(v.max()), 4)}


def effective_delay(H, times, y):
    best = None
    for s in np.arange(-0.05, 0.2501, 0.001):
        r = math.sqrt(float(np.mean((y - H(times - s)) ** 2)))
        if best is None or r < best[1]:
            best = (round(float(s), 3), r)
    return best


def evaluate(laps, fps=60, rate=1.0, jitter=0.0, pause_every=0.0):
    res = {"before": {}, "after": {}, "raw": {}}
    acc = {k: {"err": [], "d2": [], "rate": [], "path": [], "delay": [], "worst": (None, -1, 0)} for k in res}
    per_lap = []
    for li, (lid, lap) in enumerate(laps):
        t, x, z, h = motion(lap)
        H = (lambda q, t=t, h=h: np.interp(q, t, h))
        times = frame_times(t[-1], fps, rate, jitter, seed=17 + li, pause_every=pause_every)
        target = H(times)
        ph = path_heading(t, x, z, times)
        ys = {"before": run_before(H, times), "after": run_after(H, times), "raw": target.copy()}
        row = {"lap": lid}
        for k, y in ys.items():
            err = np.abs(wrap(y - target)) * DEG
            d1 = np.diff(y)
            d2 = np.abs(np.diff(d1)) * DEG
            dts = np.diff(times)
            ok = (dts[:-1] > 0) & (dts[1:] > 0)
            rate_chg = np.abs(d1[1:][ok] / dts[1:][ok] - d1[:-1][ok] / dts[:-1][ok]) * DEG
            pth = np.abs(wrap(y - ph)) * DEG
            a = acc[k]
            a["err"].append(err); a["d2"].append(d2); a["rate"].append(rate_chg); a["path"].append(pth)
            if pause_every == 0 and jitter == 0:
                a["delay"].append(effective_delay(H, times, y)[0])
            wi = int(np.argmax(err))
            if err[wi] > a["worst"][1]:
                d = float(np.interp(times[wi], t, np.asarray(lap["dist"], float)))
                a["worst"] = (lid, round(float(err[wi]), 3), round(float(times[wi]), 3), round(d, 1))
            row[k] = {"errMax": round(float(err.max()), 3), "errP95": round(float(np.quantile(err, 0.95)), 3)}
        per_lap.append(row)
    for k, a in acc.items():
        res[k] = {
            "headingErrorVsTargetDeg": stats(np.concatenate(a["err"])),
            "jitter2ndDiffDegPerFrame": stats(np.concatenate(a["d2"])),
            "yawRateChangeDegPerSecPerFrame": stats(np.concatenate(a["rate"])),
            "headingVsDrawnPathTangentDeg": stats(np.concatenate(a["path"])),
            "effectiveDelaySecondsPerLap": a["delay"],
            "worst": {"lap": a["worst"][0], "errDeg": a["worst"][1], "t": a["worst"][2], "dist": a["worst"][3]},
        }
    return res, per_lap


def main():
    laps = load_laps()
    assert len(laps) == 10, len(laps)
    out = {"laps": [l for l, _ in laps], "method": __doc__}
    base, per_lap = evaluate(laps, fps=60)
    out["fps60_rate1"] = base
    out["fps60_rate1_perLap"] = per_lap
    scen = {
        "fps30": dict(fps=30), "fps144": dict(fps=144), "fps60_rate4": dict(fps=60, rate=4.0),
        "fps60_dtJitter40": dict(fps=60, jitter=0.4), "fps60_pause10framesEvery3s": dict(fps=60, pause_every=3.0),
    }
    out["robustness"] = {}
    for name, kw in scen.items():
        r, _ = evaluate(laps, **kw)
        out["robustness"][name] = {k: {m: r[k][m] for m in ("headingErrorVsTargetDeg", "jitter2ndDiffDegPerFrame",
                                                            "yawRateChangeDegPerSecPerFrame")} for k in ("before", "after")}
    with open(os.path.join(OUT, "pose_lag_crosscheck.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    for k in ("raw", "before", "after"):
        r = base[k]
        print(k, "err", r["headingErrorVsTargetDeg"], "\n   jitter", r["jitter2ndDiffDegPerFrame"],
              "\n   rateChg", r["yawRateChangeDegPerSecPerFrame"], "\n   vsPath", r["headingVsDrawnPathTangentDeg"],
              "\n   delay", r["effectiveDelaySecondsPerLap"], "\n   worst", r["worst"])
    for name, r in out["robustness"].items():
        print(name, {k: (r[k]["headingErrorVsTargetDeg"]["median"], r[k]["headingErrorVsTargetDeg"]["p95"],
                         r[k]["headingErrorVsTargetDeg"]["max"], r[k]["jitter2ndDiffDegPerFrame"]["p99"],
                         r[k]["jitter2ndDiffDegPerFrame"]["max"]) for k in ("before", "after")})


if __name__ == "__main__":
    main()
