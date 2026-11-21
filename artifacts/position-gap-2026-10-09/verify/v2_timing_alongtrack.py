"""Verifier V2: timing -> along-track conversions recomputed from public lap JSON + manifests (own code).

Checks I1 claims:
  * time-weighted median speed per session (I1: 105 / 122 km/h), p10, top speed (191 / 196 km/h)
  * decoded-frame offset displacement in the 13 comparison scenes (I1: replay behind by 0.15-1.07 m),
    using the lap 'dist' channel (not speed x dt) at the actual scene time
  * video-to-lap timer mapping: within-lap deviation rms / max (I1: 0.017-0.019 s rms, max 0.039 s), 7/30 L5 +0.09 s
  * video-yaw lag medians (I1: 0.185 / 0.235 s) and their metres at the actual corner speeds of the scenes
Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
RACES = {"fuji_aim_01": 4, "fuji_aim_2020_07_30": 6}


def lap(race, n):
    return json.loads((ROOT / "public" / "data" / "races" / race / f"osaki_hmr_demio_101_lap_{n:03d}.json").read_text("utf-8"))


def weighted_quantile(x, w, q):
    o = np.argsort(x)
    cw = np.cumsum(w[o]) / np.sum(w)
    return float(np.interp(q, cw, x[o]))


def main():
    out = {"speed": {}, "scenes": [], "timer": {}, "videoYawLag": {}}
    for race, nl in RACES.items():
        sp, wt = [], []
        top = 0
        for n in range(1, nl + 1):
            d = lap(race, n)
            t = np.asarray(d["t"], float)
            v = np.asarray(d["speed"], float)
            dt = np.gradient(t)
            sp.append(v)
            wt.append(dt)
            top = max(top, float(v.max()))
        sp, wt = np.concatenate(sp), np.concatenate(wt)
        out["speed"][race] = {"timeWeightedMedianKmh": weighted_quantile(sp, wt, 0.5),
                              "p10Kmh": weighted_quantile(sp, wt, 0.10), "topKmh": top,
                              "share60to100": float(np.sum(wt[(sp >= 60) & (sp < 100)]) / np.sum(wt))}

    man = json.loads((ROOT / "artifacts" / "onboard-comparison-2026-10-03" / "manifest.json").read_text("utf-8"))
    for s in man["shots"]:
        d = lap(s["raceId"], s["lap"])
        t = np.asarray(d["t"], float)
        dist = np.asarray(d["dist"], float)
        v = np.asarray(d["speed"], float)
        tc = s["afterPlaybackSeconds"]
        dec = s["decodedFramePtsSeconds"] - s["videoPtsSeconds"]
        disp = float(np.interp(tc + dec, t, dist) - np.interp(tc, t, dist))
        vk = float(np.interp(tc, t, v))
        lag = 0.185 if s["raceId"] == "fuji_aim_01" else 0.235
        out["scenes"].append({
            "scene": s["id"], "speedKmh": round(vk, 1), "decodedMinusRequestedS": round(dec, 4),
            "decodedOffsetMetres": round(disp, 3),
            "speedTimesDtMetres": round(vk / 3.6 * dec, 3),
            "overlayMinusRequestedS": s.get("overlayMinusRequestedLapSeconds"),
            "videoYawLagMetresIfAllTiming": round(float(np.interp(tc + lag, t, dist) - np.interp(tc, t, dist)), 2),
            "plusMinus30msMetres": round(vk / 3.6 * 0.03, 2),
        })

    tr = json.loads((ROOT / "artifacts" / "video-position-gap-2026-10-02" / "video-timer-reads.json").read_text("utf-8"))
    for race, sess in tr["sessions"].items():
        devs, offs = [], {}
        for r in sess["reads"]:
            diff = np.array(r["read"]) - np.array(r["predicted"])
            offs[r["lap"]] = round(float(diff.mean()), 4)
            devs.extend((diff - diff.mean()).tolist())
        devs = np.array(devs)
        out["timer"][race] = {"perLapMeanOffsetS": offs, "withinLapRmsS": float(np.sqrt(np.mean(devs ** 2))),
                              "withinLapMaxAbsS": float(np.max(np.abs(devs))),
                              "storedPerLapCorrection": sess["perLapCorrectionSeconds"]}

    ev = json.loads((ROOT / "artifacts" / "improvement-eval-2026-10-02" / "eval" / "iter2" / "evaluation-iter2.json").read_text("utf-8"))
    for race, blk in ev["video_raw"].items():
        g = [l["gpsCourseRateAfterVideoS"] for l in blk["laps"]]
        i = [l["imuAccyOverSpeedAfterVideoS"] for l in blk["laps"]]
        out["videoYawLag"][race] = {"gpsMedian": float(np.median(g)), "gpsRange": [min(g), max(g)],
                                    "imuMedian": float(np.median(i)), "storedMedians": blk["medians"]}
    (HERE / "v2_timing_alongtrack.json").write_text(json.dumps(out, indent=1) + "\n", "utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
