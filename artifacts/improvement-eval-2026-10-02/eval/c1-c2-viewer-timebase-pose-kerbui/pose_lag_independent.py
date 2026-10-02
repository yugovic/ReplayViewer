"""Independent cross-check of the playback pose-lag change (C1).

Re-implements, from the source text only (no viewer code imported):
  * the target heading of src/replay/interpolation.ts (getLapMotionArrays /
    samplePose): heading[i] = atan2 of the chord x[i+6]-x[i-6], z[i+6]-z[i-6]
    (clamped at lap ends), unwrapped, linearly interpolated in t, time clamped
    to [0, t_end];
  * BEFORE = baseline ReplayScene: y <- slerp(y, target(t), 1-exp(-dt/0.140)),
    snap when dt <= 0 or dt > 0.25;
  * AFTER  = src/engine/poseSmoothing.ts DEFAULT (2 stages x 70 ms, look-ahead
    140 ms, first-order-hold input weight c), incl. snap + pre-charge.
Yaw-only scalar simulation: the target pose is yaw * pitch * roll(about local
forward), so the yaw of the target is exactly `heading`, and slerp between
yaw-dominated quaternions reduces to linear interpolation of the angle (the
TS run, which uses real quaternions, is the reference for the small pitch/roll
cross-terms).

Extra metric not in the implementer's eval: misalignment against the drawn
PATH tangent (direction of d/dt of the C1 Hermite position the viewer draws),
i.e. "does the car point where it is moving" (GPS cannot see slip angle, so
this is an upper bound on visible crabbing, not a truth).

Usage: python pose_lag_independent.py [--fps 60] > out.json
Reads only public/data/races/<fuji race>/*.json. Writes stdout only.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
RACES = ["fuji_aim_01", "fuji_aim_2020_07_30"]
ORIGIN_LAT, ORIGIN_LNG = 35.3717, 138.9256  # public/data/tracks/fuji/track.json origin
MPD = 111_320.0
WIN = 6
DEG = 180.0 / math.pi


def wrap(a):
    return np.arctan2(np.sin(a), np.cos(a))


def load_laps():
    laps = []
    for race in RACES:
        d = ROOT / "public/data/races" / race
        idx = json.loads((d / "laps.json").read_text(encoding="utf-8"))
        for rec in idx["laps"]:
            if not rec.get("data_file"):
                continue
            lap = json.loads((d / rec["data_file"]).read_text(encoding="utf-8"))
            laps.append((f"{race}/L{lap['meta']['lap']}", lap))
    return laps


def motion_arrays(lap):
    t = np.asarray(lap["t"], float)
    lat = np.asarray(lap["lat"], float)
    lng = np.asarray(lap["lng"], float)
    x = (lng - ORIGIN_LNG) * MPD * math.cos(math.radians(ORIGIN_LAT))
    z = -(lat - ORIGIN_LAT) * MPD
    n = len(t)
    hd = np.zeros(n)
    prev = 0.0
    for i in range(n):
        s, e = max(0, i - WIN), min(n - 1, i + WIN)
        dx, dz = x[e] - x[s], z[e] - z[s]
        h = prev
        if math.hypot(dx, dz) >= 0.001:
            h = math.atan2(dx, dz)
            while h - prev > math.pi:
                h -= 2 * math.pi
            while h - prev < -math.pi:
                h += 2 * math.pi
        hd[i] = h
        prev = h
    return t, x, z, hd


def hermite_tangent_heading(t, v_x, v_z, times):
    """Heading of d/dt of the non-uniform Catmull-Rom Hermite (interpolation.ts)."""
    n = len(t)

    def tangents(v):
        prev = np.clip(np.arange(n) - 1, 0, n - 1)
        nxt = np.clip(np.arange(n) + 1, 0, n - 1)
        span = t[nxt] - t[prev]
        return np.where(span > 0, (v[nxt] - v[prev]) / np.where(span > 0, span, 1), 0.0)

    mx, mz = tangents(v_x), tangents(v_z)
    tc = np.clip(times, t[0], t[-1])
    idx = np.clip(np.searchsorted(t, tc, side="right") - 1, 0, n - 2)
    span = t[idx + 1] - t[idx]
    u = (tc - t[idx]) / span
    # derivative of the Hermite basis w.r.t. u, divided by span
    d00 = 6 * u * u - 6 * u
    d10 = 3 * u * u - 4 * u + 1
    d01 = -6 * u * u + 6 * u
    d11 = 3 * u * u - 2 * u

    def deriv(v, m):
        return (d00 * v[idx] + d10 * m[idx] * span + d01 * v[idx + 1] + d11 * m[idx + 1] * span) / span

    return np.arctan2(deriv(v_x, mx), deriv(v_z, mz))


def frame_times(duration, fps):
    ts = [0.0]
    t = 0.0
    while t < duration:
        t = min(duration, t + 1.0 / fps)
        ts.append(t)
    return np.asarray(ts)


def simulate(t, hd, times, mode):
    tgt = lambda q: np.interp(min(max(q, 0.0), t[-1]), t, hd)  # noqa: E731 (clamped like sampleReplay)
    out = np.empty(len(times))
    y = None
    last = None
    # AFTER state
    tau2, N = 0.070, 2
    look = N * tau2
    s1 = prev_in = 0.0
    charged = False
    snap_t = float("nan")
    for k, tk in enumerate(times):
        dt = 0.0 if last is None else tk - last
        last = tk
        u_now = tgt(tk)
        if not (0.0 < dt <= 0.25):
            y = u_now
            snap_t = tk
            charged = False
            out[k] = y
            continue
        if mode == "before":
            a = 1.0 - math.exp(-dt / 0.140)
            y = y + a * (u_now - y)
        else:
            if not charged:
                s1 = tgt(snap_t + (N - 1) * tau2)
                prev_in = tgt(snap_t + look)
                charged = True
            a = -math.expm1(-dt / tau2)
            xx = dt / tau2
            c = 0.5 + xx / 12 if xx < 1e-4 else 1 + 1 / math.expm1(xx) - 1 / xx
            u_in = tgt(tk + look)
            held = prev_in + c * (u_in - prev_in)
            prev_in = u_in
            before = s1
            s1 = s1 + a * (held - s1)
            held = before + c * (s1 - before)
            y = y + a * (held - y)
        out[k] = y
    return out


def q(v, p):
    return float(np.quantile(v, p))


def stats(v, nd=3):
    v = np.asarray(v)
    return {"n": int(v.size), "median": round(q(v, 0.5), nd), "p95": round(q(v, 0.95), nd),
            "p99": round(q(v, 0.99), nd), "max": round(float(v.max()), nd)}


def main():
    fps = 60.0
    if "--fps" in sys.argv:
        fps = float(sys.argv[sys.argv.index("--fps") + 1])
    laps = load_laps()
    agg = {m: {"err": [], "err_tan": [], "jit": [], "step": []} for m in ("before", "after", "raw")}
    per_lap = []
    worst = {m: (0.0, "", 0.0) for m in ("before", "after")}
    for lid, lap in laps:
        t, x, z, hd = motion_arrays(lap)
        times = frame_times(t[-1], fps)
        target = np.interp(times, t, hd)
        tan = hermite_tangent_heading(t, x, z, times)
        row = {"lap": lid, "frames": int(len(times))}
        for m in ("before", "after", "raw"):
            y = target.copy() if m == "raw" else simulate(t, hd, times, m)
            err = np.abs(wrap(y - target)) * DEG
            err_tan = np.abs(wrap(y - tan)) * DEG
            jit = np.abs(y[2:] - 2 * y[1:-1] + y[:-2]) * DEG
            step = np.abs(np.diff(y)) * DEG
            agg[m]["err"].append(err)
            agg[m]["err_tan"].append(err_tan)
            agg[m]["jit"].append(jit)
            agg[m]["step"].append(step)
            row[m] = {"p95": round(q(err, 0.95), 3), "max": round(float(err.max()), 3)}
            if m in worst and err.max() > worst[m][0]:
                k = int(err.argmax())
                worst[m] = (float(err.max()), lid, float(times[k]))
        per_lap.append(row)
    result = {"fps": fps, "laps": len(laps), "per_lap": per_lap}
    for m in ("before", "after", "raw"):
        cat = {k: np.concatenate(v) for k, v in agg[m].items()}
        result[m] = {
            "headingErrorVsTargetDeg": stats(cat["err"]),
            "headingErrorVsDrawnPathTangentDeg": stats(cat["err_tan"]),
            "stepDegPerFrame": stats(cat["step"], 4),
            "jitterDegPerFrame(2nd diff)": stats(cat["jit"], 4),
        }
        if m in worst:
            result[m]["worst"] = {"errDeg": round(worst[m][0], 3), "lap": worst[m][1], "time": round(worst[m][2], 3)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
