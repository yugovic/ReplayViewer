#!/usr/bin/env python3
"""Audit time alignment between GPS position and logger-clock channels for the two Fuji sessions.

Data part (shipped lap JSON only):
  * IMU (InlineAcc/LateralAcc -> accx/accy) vs GPS-derived along/lateral acceleration,
    sub-sample cross-correlation after cubic upsampling.
  * Braking zones: ECU brake onset -> GPS-speed deceleration onset and -> IMU deceleration onset.
  * Lap-start station of the raw GPS (do lap boundaries follow the GPS time base?).

Video part (optional; user-supplied SmartyCam files, local reference only, no frames written):
  * Overlay THROTTLE/BRAKE bars (logger data burned in real time) vs recorded aps/brake.
  * Windshield horizontal optical flow (body yaw rate) vs GPS course rate and IMU accy/v.
  Video time is ffmpeg PTS; lap starts come from artifacts/video-position-gap-2026-10-02/video-timer-reads.json.

Usage:
  python scripts/quality/audit-fuji-channel-timing.py [--wet-video <7/29 SCHD0613.MOV> --dry-video <7/30 SCHD0624.MOV>]
      [--races-dir <dir containing fuji_aim_01/ and fuji_aim_2020_07_30/>] [--out <audit JSON path>]
  --races-dir defaults to public/data/races (the shipped laps); point it at a snapshot to audit
  an earlier build. --out defaults to artifacts/video-position-gap-2026-10-02/channel-timing-audit.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from register_gps_to_track import load_limits, to_local  # noqa: E402

OUT_DIR = ROOT / "artifacts/video-position-gap-2026-10-02"
RACES = {"fuji_aim_01": 4, "fuji_aim_2020_07_30": 6}
G = 9.80665
RACES_DIR = ROOT / "public/data/races"  # overridden by --races-dir


def load_lap(race: str, lap: int) -> dict:
    path = RACES_DIR / race / f"osaki_hmr_demio_101_lap_{lap:03d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def wall(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", ""))


def best_shift(t, a, b, lo=-1.0, hi=0.5, step=0.002):
    """Shift s maximising corr(a(t), b(t + s)); s < 0 means b's features come earlier than a's."""
    ca, cb = CubicSpline(t, a), CubicSpline(t, b)
    tt = np.arange(t[0] + 2, t[-1] - 2, 0.01)
    A = ca(tt)
    best = (math.nan, -2.0)
    for s in np.arange(lo, hi, step):
        r = float(np.corrcoef(A, cb(tt + s))[0, 1])
        if r > best[1]:
            best = (float(s), r)
    return best


def gps_motion(d: dict, origin: dict):
    t = np.asarray(d["t"], float)
    xy = to_local(d["lat"], d["lng"], origin)
    cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
    vx, vz, ax, az = cx(t, 1), cz(t, 1), cx(t, 2), cz(t, 2)
    speed = np.maximum(np.hypot(vx, vz), 1.0)
    along = (ax * vx + az * vz) / speed
    lateral = (vx * az - vz * ax) / speed
    course = np.unwrap(np.arctan2(vx, -vz))
    return t, xy, along, lateral, np.gradient(course, t), speed


def onset(x, t, i0, thr):
    for i in range(i0, len(x) - 1):
        if x[i] < thr <= x[i + 1]:
            return float(t[i] + (thr - x[i]) / (x[i + 1] - x[i]) * (t[i + 1] - t[i]))
    return None


def data_audit(origin: dict, limits) -> dict:
    out = {}
    for race, count in RACES.items():
        laps, zones = [], []
        for k in range(1, count + 1):
            d = load_lap(race, k)
            t, xy, along, lateral, _, _ = gps_motion(d, origin)
            accx, accy = np.asarray(d["accx"]) * G, np.asarray(d["accy"]) * G
            if np.corrcoef(lateral, accy)[0, 1] < 0:
                lateral = -lateral
            sx, rx = best_shift(t, along, accx)
            sy, ry = best_shift(t, lateral, accy)
            # lap-start station of the raw GPS, extrapolated to the true lap start
            delta = d["meta"]["first_sample_after_lap_start_seconds"]
            v0 = (xy[1] - xy[0]) / (t[1] - t[0])
            p0 = xy[0] - v0 * delta
            idx, _ = limits.lateral(p0[None, :])
            i = int(idx[0])
            tangent = np.array([-limits.normal[i][1], limits.normal[i][0]])
            if np.dot(tangent, v0) < 0:
                tangent = -tangent
            station = float(limits.dist[i] + np.dot(p0 - limits.centre[i], tangent))
            laps.append({"lap": k, "imuAccxShiftS": round(sx, 3), "rAccx": round(rx, 3),
                         "imuAccyShiftS": round(sy, 3), "rAccy": round(ry, 3),
                         "firstSampleAfterLapStartS": delta, "lapStartRawGpsStationM": round(station, 2)})
            # braking zones
            brake = np.asarray(d["brake"], float)
            gspeed = np.asarray(d["speed"]) / 3.6
            gdec = -np.gradient(gspeed, t) / G
            idec = -accx / G if np.corrcoef(-accx, gdec)[0, 1] > 0 else accx / G
            thr = 0.2 * np.percentile(brake, 99)
            i = 1
            while i < len(brake) - 1:
                if brake[i - 1] < thr <= brake[i] and gspeed[i] > 15:
                    tb = onset(brake, t, i - 1, thr)
                    j0 = max(0, int(np.searchsorted(t, tb - 1.5)))
                    tg, ti = onset(gdec, t, j0, 0.3), onset(idec, t, j0, 0.3)
                    if tg is not None and abs(tg - tb) < 1.5:
                        v = float(np.interp(tb, t, gspeed))
                        zones.append({"lap": k, "distM": round(float(np.interp(tb, t, d["dist"])), 1),
                                      "speedKmh": round(v * 3.6, 1), "gpsDecelAfterBrakeS": round(tg - tb, 3),
                                      "gpsDecelAfterBrakeM": round((tg - tb) * v, 1),
                                      "imuDecelAfterBrakeS": None if ti is None else round(ti - tb, 3)})
                    i += 20
                i += 1
        shifts = [x for lap in laps for x in (lap["imuAccxShiftS"], lap["imuAccyShiftS"])]
        g = [z["gpsDecelAfterBrakeS"] for z in zones]
        gm = [z["gpsDecelAfterBrakeM"] for z in zones]
        im = [z["imuDecelAfterBrakeS"] for z in zones if z["imuDecelAfterBrakeS"] is not None and abs(z["imuDecelAfterBrakeS"]) < 1.5]
        out[race] = {"laps": laps, "imuVsGpsMedianShiftS": round(float(np.median(shifts)), 3),
                     "imuVsGpsRangeS": [round(min(shifts), 3), round(max(shifts), 3)],
                     "brakingZones": len(zones), "gpsDecelAfterBrakeMedianS": round(float(np.median(g)), 3),
                     "gpsDecelAfterBrakeMedianM": round(float(np.median(gm)), 1),
                     "imuDecelAfterBrakeMedianS": round(float(np.median(im)), 3), "zones": zones}
    return out


def decode(path: Path, start: float, dur: float, crop: tuple[int, int, int, int], gray_half: bool):
    w, h, x, y = crop
    vf = f"crop={w}:{h}:{x}:{y}" + (f",scale={w // 2}:{h // 2},format=gray" if gray_half else "")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path), "-t", f"{dur:.3f}", "-vf", vf]
    cmd += ["-f", "rawvideo"] + ([] if gray_half else ["-pix_fmt", "rgb24"]) + ["-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if gray_half:
        return np.frombuffer(raw, np.uint8).reshape(-1, h // 2, w // 2)
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.int16)


def lag_linear(ta, a, tb, b, maxlag=1.5, step=0.005):
    """Shift D maximising corr(a(t), b(t - D)): D > 0 means a happens D seconds after b."""
    best = (math.nan, -2.0)
    for s in np.arange(-maxlag, maxlag + 1e-9, step):
        bb = np.interp(ta - s, tb, b, left=np.nan, right=np.nan)
        m = np.isfinite(bb) & np.isfinite(a)
        if m.sum() < 100:
            continue
        r = float(np.corrcoef(a[m], bb[m])[0, 1])
        if r > best[1]:
            best = (float(s), r)
    return best


def video_audit(origin: dict, videos: dict[str, Path]) -> dict:
    import cv2  # local optional dependency

    reads = json.loads((OUT_DIR / "video-timer-reads.json").read_text(encoding="utf-8"))["sessions"]
    out = {}
    for race, video in videos.items():
        cfg = reads[race]
        laps = []
        for k in range(1, RACES[race] + 1):
            d = load_lap(race, k)
            meta = d["meta"]
            lap_start_pts = ((wall(meta["lap_start_time"]) - wall(cfg["referenceLapStart"])).total_seconds()
                             + cfg["referencePtsSeconds"] - cfg["perLapCorrectionSeconds"][str(k)])
            t0_pts = lap_start_pts + meta["first_sample_after_lap_start_seconds"]
            t, _, _, _, course_rate, speed = gps_motion(d, origin)
            row = {"lap": k, "lapStartPts": round(lap_start_pts, 3)}
            # overlay pedal bars (x 200-296, y 530-590)
            fr = decode(video, t0_pts + 0.5, t[-1] - 1.0, (96, 60, 200, 530), False)
            tv = 0.5 + np.arange(len(fr)) / 30.0
            R, Gc = fr[..., 0], fr[..., 1]
            thr = ((Gc[:, 7:23, 8:88] > 170) & (R[:, 7:23, 8:88] < 150)).mean(axis=(1, 2))
            brk = ((R[:, 35:50, 8:88] > 190) & (Gc[:, 35:50, 8:88] < 110)).mean(axis=(1, 2))
            s, r = lag_linear(tv, thr, t, np.asarray(d["aps"], float))
            row.update(overlayThrottleAfterRecordedS=round(s, 3), rThrottle=round(r, 3))
            s, r = lag_linear(tv, brk, t, np.asarray(d["brake"], float))
            row.update(overlayBrakeAfterRecordedS=round(s, 3), rBrake=round(r, 3))
            # windshield optical flow -> body yaw-rate proxy
            fr = decode(video, t0_pts + 1.0, t[-1] - 2.0, (900, 170, 180, 130), True)
            h, w = fr.shape[1:]
            mR = np.zeros((h, w), bool); mR[(160 - 130) // 2:(285 - 130) // 2, (660 - 180) // 2:(1060 - 180) // 2] = True
            mL = np.zeros((h, w), bool); mL[(205 - 130) // 2:(290 - 130) // 2, (200 - 180) // 2:(440 - 180) // 2] = True
            fx = []
            for a, b in zip(fr[:-1], fr[1:]):
                f = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.1, 0)[..., 0]
                fx.append(0.5 * (np.median(f[mR]) + np.median(f[mL])))
            yaw = gaussian_filter1d(np.asarray(fx), 3.0)
            tv = 1.0 + (np.arange(len(yaw)) + 0.5) / 30.0
            imu = np.asarray(d["accy"]) * G / np.maximum(speed, 3.0)
            for name, series in (("gpsCourseRate", course_rate), ("imuAccyOverSpeed", imu)):
                s1, r1 = lag_linear(tv, yaw, t, series)
                s2, r2 = lag_linear(tv, -yaw, t, series)
                s, r = (s1, r1) if r1 >= r2 else (s2, r2)
                # convert: a = video yaw, D > 0 means video after channel -> channel leads video
                row[f"{name}AfterVideoS"] = round(-s, 3)
                row[f"r_{name}"] = round(r, 3)
            laps.append(row)
            print(race, row, flush=True)
        keys = ["overlayThrottleAfterRecordedS", "overlayBrakeAfterRecordedS", "gpsCourseRateAfterVideoS", "imuAccyOverSpeedAfterVideoS"]
        out[race] = {"laps": laps, "medians": {k: round(float(np.median([l[k] for l in laps])), 3) for k in keys}}
    return out


def main() -> int:
    global RACES_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--wet-video", type=Path, help="7/29 SmartyCam SCHD0613.MOV (local, not published)")
    parser.add_argument("--dry-video", type=Path, help="7/30 SmartyCam SCHD0624.MOV (local, not published)")
    parser.add_argument("--races-dir", type=Path, default=RACES_DIR,
                        help="directory holding <race>/osaki_hmr_demio_101_lap_NNN.json (default: shipped public/data/races)")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "channel-timing-audit.json", help="audit JSON to write")
    args = parser.parse_args()
    RACES_DIR = args.races_dir.resolve()
    track = json.loads((ROOT / "public/data/tracks/fuji/track.json").read_text(encoding="utf-8"))
    limits = load_limits(ROOT / "public/data/tracks/fuji", 0.0)
    result = {"scope": "GPS position vs logger-clock channel timing (Fuji 2020-07-29/30). Read-only audit; no data modified.",
              "signConventions": {"imuShiftS": "s maximising corr(GPS-derived(t), IMU(t+s)); negative = IMU features occur earlier than GPS",
                                  "AfterRecordedS / AfterVideoS": "positive = the first-named signal happens later"},
              "racesDir": RACES_DIR.relative_to(ROOT).as_posix() if RACES_DIR.is_relative_to(ROOT) else RACES_DIR.name,
              "data": data_audit(track["origin"], limits)}
    videos = {r: p for r, p in (("fuji_aim_01", args.wet_video), ("fuji_aim_2020_07_30", args.dry_video)) if p}
    if videos:
        result["video"] = video_audit(track["origin"], videos)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for race, r in result["data"].items():
        print(f"{race}: IMU-vs-GPS median {r['imuVsGpsMedianShiftS']:+.3f}s range {r['imuVsGpsRangeS']}; "
              f"brake->GPS decel {r['gpsDecelAfterBrakeMedianS']:+.3f}s ({r['gpsDecelAfterBrakeMedianM']:+.1f} m), "
              f"brake->IMU decel {r['imuDecelAfterBrakeMedianS']:+.3f}s over {r['brakingZones']} zones")
    for race, r in result.get("video", {}).items():
        print(race, r["medians"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
