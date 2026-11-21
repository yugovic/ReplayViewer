"""T2 step 1b: decode the SmartyCam data track (audio stream index 2, handler 'TmcdHandler') and relate it to
the logger clocks. Only numbers are written to the repo; the WAV and record arrays stay in T2_WORK.

Finding (measured): stream 2 is not sound. It carries one 16000-byte record every 1/6 s (8000 samples) that
starts with the marker 00 00 0b 15 01 00. Little-endian u32 fields relative to the marker:
  @-2  video frame number (== 30 * PTS exactly, all records of both files)
  @+6  logger-clock milliseconds (10 ms resolution, updates every record except ~6 s around each lap line,
       where it updates at 1 Hz)
  @+38 the overlay BEST lap in ms (e.g. 133630 = "BEST 2:13.63" on 7/30)
  @+46 GPS iTOW milliseconds of the latest GPS epoch (100 ms steps)
  @+94 the logger's GPS timecode of that epoch (= iTOW - C). C equals the XRK's own NAV-SOL
       iTOW - raw timecode constant in both sessions -> @+94 is the XRK raw GPS timecode.
libxrk timecodes = raw timecodes - libOffset (libOffset = raw NAV-SOL tc - libxrk GPS Speed tc).
Per lap, with t_ovl = overlay-timer lap t of the record's frame:
  t_gpsLatest = (@+94 - libOffset - tc0) / 1000             (lap t of the newest GPS epoch held by the camera)
  t_logger    = (@+6 - libOffset - tc0) / 1000 + shift      (logger clock mapped to the GPS base like the IMU)
Output: datatrack_summary.json (this folder), <T2_WORK>/datatrack/<tag>_records.npz.
"""
import json
import re
import sys

import numpy as np

from t2_common import HERE, ROOT, RACES, TAG, WORK, XRK, lap_info, pts_to_t, t_to_pts

sys.path.insert(0, str(ROOT / "artifacts/position-gap-2026-10-09/R1-fusion"))
import navsol_probe  # noqa: E402  (reads raw NAV-SOL records incl. iTOW from the XRK byte stream)

MARK = bytes.fromhex("00000b150100")


def parse_records(tag):
    raw = (WORK / f"audio/{tag}_a2.wav").read_bytes()
    b = raw[raw.find(b"data") + 8:]
    pos = np.array([m.start() for m in re.finditer(re.escape(MARK), b)])
    u32 = lambda off: np.array([int.from_bytes(b[p + off:p + off + 4], "little") for p in pos], np.int64)
    rec = {"bytePos": pos, "frame": u32(-2), "loggerMs": u32(6), "bestMs": u32(38), "itowMs": u32(46),
           "gpsTcRaw": u32(94)}
    rec["audioPts"] = pos / 96000.0
    return rec


def lib_offset(race):
    from libxrk import aim_xrk
    a, _ = navsol_probe.read_navsol(XRK[race])
    a = a[np.argsort(a["tc"], kind="stable")]
    log = aim_xrk(XRK[race])
    lib = log.channels["GPS Speed"].column("timecodes").to_numpy().astype(np.int64)
    tc = a["tc"].astype(np.int64)
    C = np.unique(a["itow"].astype(np.int64) - tc)
    off = np.unique(tc[:len(lib)] - lib) if len(lib) == len(tc) else np.array([tc[0] - lib[0]])
    return int(off[0]), [int(c) for c in C], int(len(a))


def stats(x):
    x = np.asarray(x, float)
    return {"median": round(float(np.median(x)), 4), "p5": round(float(np.percentile(x, 5)), 4),
            "p95": round(float(np.percentile(x, 95)), 4), "min": round(float(x.min()), 4),
            "max": round(float(x.max()), 4), "n": int(len(x))}


def main():
    out = {"kind": "measured (data track of the local SmartyCam files + XRK NAV-SOL); numbers only",
           "recordLayout": __doc__.split("Finding (measured):")[1].split("Output:")[0].strip(),
           "signConvention": "dt_* = t_* - t_ovl (s); positive = the camera already holds logger/GPS time that is "
                             "later than the overlay-timer lap time of the same frame",
           "sessions": {}}
    for race in RACES:
        tag = TAG[race]
        rec = parse_records(tag)
        pts = rec["frame"] / 30.0
        off, C, n_nav = lib_offset(race)
        cam_C = np.unique(rec["itowMs"] - rec["gpsTcRaw"])
        chg = np.r_[True, np.diff(rec["loggerMs"]) != 0]
        info = lap_info(race)
        laps = {}
        for k, li in info.items():
            m = (pts >= t_to_pts(li, 0)) & (pts <= t_to_pts(li, li["tEnd"]))
            t_ovl = pts_to_t(li, pts[m])
            t_g = (rec["gpsTcRaw"][m] - off - li["tc0Ms"]) / 1000.0
            t_l = (rec["loggerMs"][m] - off - li["tc0Ms"]) / 1000.0 + li["shift"]
            fresh = chg[m]
            dl = (t_l - t_ovl)[fresh]
            # robust drift of the logger mapping within the lap (Theil-Sen on fresh records)
            tt = t_ovl[fresh]
            idx = np.linspace(0, len(tt) - 1, min(len(tt), 300)).astype(int)
            sl = [(dl[j] - dl[i]) / (tt[j] - tt[i]) for a, i in enumerate(idx) for j in idx[a + 1:] if tt[j] - tt[i] > 10]
            laps[str(k)] = {"dtGpsLatest": stats(t_g - t_ovl), "dtLoggerClock": stats(dl),
                            "loggerClockDriftPpm": round(float(np.median(sl)) * 1e6, 1),
                            "rawLoggerMinusGpsTcMs": round(float(np.median((rec["loggerMs"][m] - rec["gpsTcRaw"][m])[fresh])), 1),
                            "shiftSeconds": li["shift"], "lapStartPts": round(li["lapStartPts"], 4)}
        def rate_ppm(val, fresh):
            x, y = pts[fresh], val[fresh].astype(float)
            c = np.array([1000.0, float(np.median(y - 1000.0 * x))])  # robust start: nominal rate
            for _ in range(3):
                keep = np.abs(y - np.polyval(c, x)) < 500.0  # drop resets / stale outliers (> 0.5 s)
                x, y = x[keep], y[keep]
                c = np.polyfit(x, y, 1)
            c = np.polyfit(x, y, 1)
            return round((c[0] / 1000.0 - 1.0) * 1e6, 1), int(len(x))

        itow_fresh = np.r_[False, np.diff(rec["itowMs"]) != 0]
        r_gps, n_gps = rate_ppm(rec["itowMs"], itow_fresh)
        r_log, n_log = rate_ppm(rec["loggerMs"], chg)
        out["sessions"][race] = {
            "records": int(len(pts)), "recordPeriodS": 1 / 6,
            "clockRates": {"gpsItowVsPtsPpm": r_gps, "nGps": n_gps, "loggerClockVsPtsPpm": r_log, "nLogger": n_log,
                           "note": "slope of the camera-held clock vs video PTS over the whole file (records where the value "
                                   "changed); ppm < 0 = that clock runs slower than the video PTS"},
            "frameEquals30xAudioPts": bool(np.all(rec["frame"] == np.round(30 * rec["audioPts"]))),
            "xrkNavsolRecords": n_nav, "xrkItowMinusRawTcMs": C,
            "cameraItowMinusGpsTcRawMs": [int(c) for c in cam_C],
            "libxrkOffsetMs": off,
            "bestLapMsValues": [int(v) for v in np.unique(rec["bestMs"])][:12],
            "loggerMsStaleFraction": round(float(1 - chg.mean()), 4),
            "laps": laps}
        np.savez_compressed(WORK / f"datatrack/{tag}_records.npz", pts=pts, libOffsetMs=off, **rec)
        print(race, json.dumps({k: (v["dtGpsLatest"]["median"], v["dtLoggerClock"]["median"]) for k, v in laps.items()}))
    (HERE / "datatrack_summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
