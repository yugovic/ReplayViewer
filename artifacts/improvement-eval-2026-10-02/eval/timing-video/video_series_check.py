#!/usr/bin/env python3
"""Supplementary video check for the 7/30 channel re-timing (evaluation helper, read-only on repo data).

Re-uses the decode / lap-mapping / lag functions of scripts/quality/audit-fuji-channel-timing.py, but
  * caches the per-frame overlay-bar fill fractions and windshield yaw flow (numbers only, no frames)
    to an .npz in the session scratchpad, so several channels can be scored against one decode;
  * adds an event-based estimator (overlay vs recorded brake / throttle onset times) next to the
    correlation estimator used by the audit;
  * adds ECU steer vs windshield yaw (steer physically leads yaw), which the audit does not score.

Usage:
  python video_series_check.py --cache <npz> --before <races dir> --after <races dir> --out <json>
      [--wet-video <SCHD0613.MOV> --dry-video <SCHD0624.MOV>]   (videos needed only if the cache is missing)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("audit", ROOT / "scripts/quality/audit-fuji-channel-timing.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
G = audit.G


def lap_mapping(race: str, k: int, reads: dict):
    d = audit.load_lap(race, k)
    meta, cfg = d["meta"], reads[race]
    lap_start_pts = ((audit.wall(meta["lap_start_time"]) - audit.wall(cfg["referenceLapStart"])).total_seconds()
                     + cfg["referencePtsSeconds"] - cfg["perLapCorrectionSeconds"][str(k)])
    return d, lap_start_pts + meta["first_sample_after_lap_start_seconds"]


def build_cache(videos: dict, cache: Path, races_dir: Path):
    import cv2

    audit.RACES_DIR = races_dir
    reads = json.loads((audit.OUT_DIR / "video-timer-reads.json").read_text(encoding="utf-8"))["sessions"]
    arrays = {}
    for race, video in videos.items():
        for k in range(1, audit.RACES[race] + 1):
            d, t0 = lap_mapping(race, k, reads)
            t = np.asarray(d["t"], float)
            fr = audit.decode(video, t0 + 0.5, t[-1] - 1.0, (96, 60, 200, 530), False)
            R, Gc = fr[..., 0], fr[..., 1]
            arrays[f"{race}|{k}|thr"] = ((Gc[:, 7:23, 8:88] > 170) & (R[:, 7:23, 8:88] < 150)).mean(axis=(1, 2))
            arrays[f"{race}|{k}|brk"] = ((R[:, 35:50, 8:88] > 190) & (Gc[:, 35:50, 8:88] < 110)).mean(axis=(1, 2))
            arrays[f"{race}|{k}|tbar"] = 0.5 + np.arange(len(fr)) / 30.0
            fr = audit.decode(video, t0 + 1.0, t[-1] - 2.0, (900, 170, 180, 130), True)
            h, w = fr.shape[1:]
            mR = np.zeros((h, w), bool); mR[(160 - 130) // 2:(285 - 130) // 2, (660 - 180) // 2:(1060 - 180) // 2] = True
            mL = np.zeros((h, w), bool); mL[(205 - 130) // 2:(290 - 130) // 2, (200 - 180) // 2:(440 - 180) // 2] = True
            fx = []
            for a, b in zip(fr[:-1], fr[1:]):
                f = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.1, 0)[..., 0]
                fx.append(0.5 * (np.median(f[mR]) + np.median(f[mL])))
            arrays[f"{race}|{k}|yaw"] = gaussian_filter1d(np.asarray(fx), 3.0)
            arrays[f"{race}|{k}|tyaw"] = 1.0 + (np.arange(len(fx)) + 0.5) / 30.0
            print("cached", race, k, flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **arrays)


def rising(x, t, thr, min_gap=1.0):
    out, last = [], -1e9
    for i in range(len(x) - 1):
        if x[i] < thr <= x[i + 1] and t[i] - last > min_gap:
            ti = float(t[i] + (thr - x[i]) / (x[i + 1] - x[i]) * (t[i + 1] - t[i]))
            out.append(ti)
            last = ti
    return out


def onset_lags(tv, ov, t, rec, frac=0.2):
    """Event estimator: overlay rising-edge time minus nearest recorded rising-edge time (s)."""
    ov_thr = frac * np.percentile(ov, 99)
    rec_thr = frac * np.percentile(rec, 99)
    eo, er = rising(ov, tv, ov_thr), rising(rec, t, rec_thr)
    lags = []
    for x in eo:
        if not er:
            break
        j = int(np.argmin([abs(x - y) for y in er]))
        if abs(x - er[j]) < 1.5:
            lags.append(x - er[j])
    return lags


def signed_best(tv, yaw, t, series):
    s1, r1 = audit.lag_linear(tv, yaw, t, series)
    s2, r2 = audit.lag_linear(tv, -yaw, t, series)
    s, r = (s1, r1) if r1 >= r2 else (s2, r2)
    return round(-s, 3), round(r, 3)  # positive = channel happens after the video yaw


def analyse(cache: Path, dirs: dict, origin: dict) -> dict:
    z = np.load(cache)
    out = {}
    for label, races_dir in dirs.items():
        audit.RACES_DIR = races_dir
        res = {}
        for race, n in audit.RACES.items():
            if f"{race}|1|thr" not in z:
                continue
            laps = []
            for k in range(1, n + 1):
                d = audit.load_lap(race, k)
                t, _, _, _, course_rate, speed = audit.gps_motion(d, origin)
                tbar, thr, brk = z[f"{race}|{k}|tbar"], z[f"{race}|{k}|thr"], z[f"{race}|{k}|brk"]
                tyaw, yaw = z[f"{race}|{k}|tyaw"], z[f"{race}|{k}|yaw"]
                aps, brake = np.asarray(d["aps"], float), np.asarray(d["brake"], float)
                row = {"lap": k}
                s, r = audit.lag_linear(tbar, thr, t, aps)
                row.update(corrThrottleD=round(s, 3), rThrottle=round(r, 3))
                s, r = audit.lag_linear(tbar, brk, t, brake)
                row.update(corrBrakeD=round(s, 3), rBrake=round(r, 3))
                bl = onset_lags(tbar, brk, t, brake)
                tl = onset_lags(tbar, thr, t, aps)
                row.update(eventBrakeD=round(float(np.median(bl)), 3) if bl else None, nBrakeEvents=len(bl),
                           eventThrottleD=round(float(np.median(tl)), 3) if tl else None, nThrottleEvents=len(tl),
                           brakeEventLags=[round(x, 3) for x in bl])
                imu = np.asarray(d["accy"]) * G / np.maximum(speed, 3.0)
                steer = np.asarray(d["steer"], float)
                for name, series in (("steer", steer), ("imuAccyOverSpeed", imu), ("gpsCourseRate", course_rate)):
                    s, r = signed_best(tyaw, yaw, t, series)
                    row[f"{name}AfterVideoYawS"], row[f"r_{name}"] = s, r
                laps.append(row)
            keys = ["corrThrottleD", "corrBrakeD", "eventBrakeD", "eventThrottleD",
                    "steerAfterVideoYawS", "imuAccyOverSpeedAfterVideoYawS", "gpsCourseRateAfterVideoYawS"]
            med = {k: round(float(np.median([l[k] for l in laps if l[k] is not None])), 3) for k in keys}
            allb = [x for l in laps for x in l["brakeEventLags"]]
            med["eventBrakeD_allEvents"] = {"n": len(allb), "median": round(float(np.median(allb)), 3),
                                            "p25": round(float(np.percentile(allb, 25)), 3),
                                            "p75": round(float(np.percentile(allb, 75)), 3)}
            res[race] = {"laps": laps, "medians": med}
            print(label, race, med, flush=True)
        out[label] = res
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--wet-video", type=Path)
    p.add_argument("--dry-video", type=Path)
    a = p.parse_args()
    if not a.cache.exists():
        videos = {r: v for r, v in (("fuji_aim_01", a.wet_video), ("fuji_aim_2020_07_30", a.dry_video)) if v}
        build_cache(videos, a.cache, a.after.resolve())
    origin = json.loads((ROOT / "public/data/tracks/fuji/track.json").read_text(encoding="utf-8"))["origin"]
    result = {"scope": "Supplementary video check (event-based overlay onsets, steer vs windshield yaw). Numbers only; no frames stored.",
              "signConventions": {"corr*/event*D": "overlay bar time minus recorded channel time (s); >0 = overlay trails recorded",
                                  "*AfterVideoYawS": ">0 = channel feature occurs after the video yaw feature"},
              "results": analyse(a.cache, {"before": a.before.resolve(), "after": a.after.resolve()}, origin)}
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
