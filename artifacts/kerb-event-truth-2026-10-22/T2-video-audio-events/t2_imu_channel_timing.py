"""T2 diagnostic: do the IMU channels share one time base? For every published kerb event, the burst onset
(burst_times rule of t2_analyze) of each single IMU channel's HP (> 3 Hz) energy envelope, and of the cabin audio
rumble (20-120 Hz), relative to the VerticalAcc burst onset. Also cross-correlates the HP vibration envelopes of
the gyro and accelerometer channels over every whole lap (no audio/video involved).
Output: imu_channel_timing.json (numbers only).
"""
import json

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, sosfiltfilt

from t2_analyze import burst_times, r3
from t2_common import HERE, RACES, WORK, kerb_contacts, lap_info, logger_to_t, pts_to_t, t_to_pts, xcorr_lag
from t2_envelopes import audio_spec, band_db

CH = ["VerticalAcc", "LateralAcc", "InlineAcc", "RollRate", "PitchRate", "YawRate"]


def env1(z, li, c, t0, t1, hp=3.0, smooth=0.1):
    tt = logger_to_t(li, z[c + "_t"], "logger")
    m = (tt >= t0 - 3) & (tt <= t1 + 3)
    sos = butter(4, hp, "hp", fs=50, output="sos")
    v = sosfiltfilt(sos, z[c][m])
    e = uniform_filter1d(v * v, max(1, int(round(smooth * 50))))
    return tt[m], 10 * np.log10(e / (np.median(e) + 1e-12) + 1e-12)


def main():
    out = {"kind": "measured (XRK IMU channels; cabin audio for reference)", "sessions": {}}
    for race in RACES:
        info = lap_info(race)
        z = np.load(WORK / f"xrk_{race}.npz")
        kc = kerb_contacts(race)["events"]
        rows = []
        for k, evs in kc.items():
            li = info[int(k)]
            for e in evs:
                w0, w1 = e["t0"] - 2, e["t1"] + 2
                lo, hi = e["t0"] - 0.2, e["t1"] + 0.2
                ons = {}
                for c in CH:
                    tt, ee = env1(z, li, c, w0, w1)
                    m = (tt >= w0) & (tt <= w1)
                    b = burst_times(tt, ee, lo, hi, float(np.percentile(ee[m], 20)))
                    if b and b["onset50"] is not None and b["riseDb"] >= 10 and not b["peakAtWindowEdge"]:
                        ons[c] = b["onset50"]
                rows.append({"lap": int(k), "kerb": e["kerb"], "peak": e["peak"], "onset50": {c: r3(v) for c, v in ons.items()}})
        rel = {c: [r["onset50"][c] - r["onset50"]["VerticalAcc"] for r in rows
                   if c in r["onset50"] and "VerticalAcc" in r["onset50"]] for c in CH if c != "VerticalAcc"}
        # whole-lap HP envelope cross-correlation: each channel vs VerticalAcc
        lapx = {}
        for k, li in info.items():
            T0, T1 = 2.0, li["tEnd"] - 2.0
            tv, ev = env1(z, li, "VerticalAcc", T0, T1)
            for c in CH:
                if c == "VerticalAcc":
                    continue
                tc, ec = env1(z, li, c, T0, T1)
                L, r, _ = xcorr_lag(tv, ev, tc, ec, T0, T1, max_lag=0.3, step=0.002, dt_grid=0.01)
                lapx.setdefault(c, []).append((r3(L), r3(r)))
        out["sessions"][race] = {
            "events": rows,
            "onsetMinusVerticalAccS": {c: {"n": len(v), "median": r3(np.median(v)) if v else None,
                                           "iqr": [r3(np.percentile(v, 25)), r3(np.percentile(v, 75))] if v else None}
                                       for c, v in rel.items()},
            "wholeLapEnvelopeLagVsVerticalAccS": {c: {"perLap": v, "median": r3(np.median([x[0] for x in v]))} for c, v in lapx.items()},
            "signConvention": "positive = the channel's vibration occurs later than VerticalAcc"}
        print(race, json.dumps(out["sessions"][race]["onsetMinusVerticalAccS"]))
        print(race, json.dumps({c: v["median"] for c, v in out["sessions"][race]["wholeLapEnvelopeLagVsVerticalAccS"].items()}))
    out["kerbResidualsVsCorrectedVideo"] = kerb_channel_residuals()
    (HERE / "imu_channel_timing.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def kerb_channel_residuals():
    """Needs events_video.json (run t2_analyze.py first). For every kerb event with high/medium audio confidence:
    single-channel IMU burst onset minus the audio (and shake) onset placed on the lap clock with the lap's
    bestOffsetS. ~0 means that channel agrees with the yaw-derived video clock at kerb strikes."""
    p = HERE / "events_video.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    res_all = {}
    for race in RACES:
        info = lap_info(race)
        z = np.load(WORK / f"xrk_{race}.npz")
        S = d["sessions"][race]
        best = {k: v["bestOffsetS"] for k, v in S["perLapOffsets"].items()}
        res = {}
        for e in S["kerbEvents"]:
            if e["audio"]["confidence"] not in ("high", "medium"):
                continue
            k = e["lap"]
            li = info[k]
            w0, w1 = e["imuT0Published"] - 2, e["imuT1Published"] + 2
            lo, hi = e["imuT0Published"] - 0.2, e["imuT1Published"] + 0.2
            a_on = e["audio"]["onset50LapTOverlay"]
            s_on = e["shake"].get("onset50LapTOverlay")
            for c in CH:
                tt, ee = env1(z, li, c, w0, w1)
                m = (tt >= w0) & (tt <= w1)
                b = burst_times(tt, ee, lo, hi, float(np.percentile(ee[m], 20)))
                if b and b["onset50"] is not None and b["riseDb"] >= 8 and not b["peakAtWindowEdge"]:
                    res.setdefault(c + "_minusAudio", []).append(b["onset50"] - a_on - best[str(k)])
                    if s_on is not None and e["shake"]["confidence"] in ("high", "medium"):
                        res.setdefault(c + "_minusShake", []).append(b["onset50"] - s_on - best[str(k)])
        res_all[race] = {c: {"n": len(v), "median": r3(np.median(v)),
                             "iqr": [r3(np.percentile(v, 25)), r3(np.percentile(v, 75))]} for c, v in res.items()}
        print(race, json.dumps({c: v["median"] for c, v in res_all[race].items()}))
    return {"label": "measured", "definition": "single-channel IMU burst onset - (audio or shake onset on the overlay map + lap bestOffsetS), s",
            "sessions": res_all}


if __name__ == "__main__":
    main()
