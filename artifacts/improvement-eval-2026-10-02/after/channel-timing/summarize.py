#!/usr/bin/env python3
"""Collect the GPS-REG-08 before/after metrics into before-after-summary.json (evaluation only).

Inputs (all in this folder unless noted):
  audit-before.json  scripts/quality/audit-fuji-channel-timing.py --races-dir <baseline 7/30 + shipped 7/29>
  audit-after.json   scripts/quality/audit-fuji-channel-timing.py (shipped laps after --apply)
  align-report.json  scripts/quality/align-fuji-channel-timing.py --apply
  baseline/after registrations and laps.json under artifacts/improvement-eval-2026-10-02/baseline and public/.
Run with the pipeline/.venv-sr interpreter (numpy/scipy).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RACE = "fuji_aim_2020_07_30"
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline/public/data/races" / RACE
CUR = ROOT / "public/data/races" / RACE


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def median_ci(values, seed=0, n=5000):
    x = np.asarray(values, float)
    rng = np.random.default_rng(seed)
    boot = [np.median(rng.choice(x, len(x))) for _ in range(n)]
    return {"median": round(float(np.median(x)), 3), "ci95": [round(float(np.percentile(boot, 2.5)), 3), round(float(np.percentile(boot, 97.5)), 3)],
            "iqr": [round(float(np.percentile(x, 25)), 3), round(float(np.percentile(x, 75)), 3)], "n": int(len(x))}


def braking(race_audit: dict) -> dict:
    zones = race_audit["zones"]
    imu_ok = [z for z in zones if z["imuDecelAfterBrakeS"] is not None and abs(z["imuDecelAfterBrakeS"]) < 1.5]
    return {"brakeToGpsDecelS": median_ci([z["gpsDecelAfterBrakeS"] for z in zones]),
            "brakeToGpsDecelM": median_ci([z["gpsDecelAfterBrakeM"] for z in zones]),
            "brakeToImuDecelS": median_ci([z["imuDecelAfterBrakeS"] for z in imu_ok]),
            "imuDecelToGpsDecelS": median_ci([z["gpsDecelAfterBrakeS"] - z["imuDecelAfterBrakeS"] for z in imu_ok])}


def main() -> int:
    before, after, align = load(HERE / "audit-before.json")["data"], load(HERE / "audit-after.json")["data"], load(HERE / "align-report.json")
    spec = importlib.util.spec_from_file_location("align", ROOT / "scripts/quality/align-fuji-channel-timing.py")
    al = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(al)
    origin = load(ROOT / "public/data/tracks/fuji/track.json")["origin"]
    wet_steer = [al.lags(load(ROOT / f"public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_{k:03d}.json"), origin)["steerVsGpsCourseRateLagS"] for k in range(1, 5)]

    reg_b, reg_a = load(BASE / "gps_registration.json"), load(CUR / "gps_registration.json")
    offsets = [{"lap": b["lap"], "before": b["offsetMeters"], "after": a["offsetMeters"], "identical": b["offsetMeters"] == a["offsetMeters"]}
               for b, a in zip(reg_b["laps"], reg_a["laps"])]
    strip = lambda reg: [{k: v for k, v in lap.items() if k != "dataSha256"} for lap in reg["laps"]]  # noqa: E731
    sizes_b = {e["data_file"]: e["file_size"] for e in load(BASE / "laps.json")["selected"]}
    sizes_a = {e["data_file"]: e["file_size"] for e in load(CUR / "laps.json")["selected"]}

    def imu_table(audit):
        return [{"lap": l["lap"], "accxLagS": l["imuAccxShiftS"], "accyLagS": l["imuAccyShiftS"]} for l in audit[RACE]["laps"]]

    summary = {
        "task": "GPS-REG-08: re-time 7/30 logger-clock channels onto the GPS time base",
        "conventions": {"lagS": "s maximising corr(GPS-derived(t), channel(t+s)); negative = logger channel earlier than GPS",
                        "shiftSeconds": "channel(t) = logger(t - shiftSeconds); positive = delayed"},
        "mode": align["mode"],
        "shiftSecondsByLap": align["summary"]["shiftSecondsByLap"][align["mode"]],
        "imuVsGpsLag": {
            "before": {"median": before[RACE]["imuVsGpsMedianShiftS"], "range": before[RACE]["imuVsGpsRangeS"], "laps": imu_table(before)},
            "after": {"median": after[RACE]["imuVsGpsMedianShiftS"], "range": after[RACE]["imuVsGpsRangeS"], "laps": imu_table(after),
                      "maxAbs": max(abs(x) for x in after[RACE]["imuVsGpsRangeS"])},
            "reference0729": {"median": before["fuji_aim_01"]["imuVsGpsMedianShiftS"], "range": before["fuji_aim_01"]["imuVsGpsRangeS"]},
            "target": "|lag| < 0.05 s on every lap and axis",
        },
        "braking": {"before": braking(before[RACE]), "after": braking(after[RACE]), "reference0729": braking(before["fuji_aim_01"]),
                    "zones": {"before": before[RACE]["brakingZones"], "after": after[RACE]["brakingZones"], "reference0729": before["fuji_aim_01"]["brakingZones"]}},
        "independentSteerCheck": {
            "description": "ECU steering vs GPS course rate (not used to estimate the shift); steering physically leads yaw",
            "before": align["summary"]["before"]["steerVsGpsCourseRateMedianS"],
            "after": align["summary"]["after"][align["mode"]]["steerVsGpsCourseRateMedianS"],
            "reference0729": {"median": round(float(np.median(wet_steer)), 3), "laps": wet_steer},
        },
        "alternativeSessionMedian": {
            "shiftSeconds": align["summary"]["shiftSecondsByLap"]["session-median"]["1"],
            "imuLagAfterMaxAbsS": align["summary"]["after"]["session-median"]["imuLagMaxAbsS"],
            "crossChannelCheck": align["summary"]["crossChannelCheck"],
        },
        "registration": {"offsetsIdentical": all(o["identical"] for o in offsets),
                         "identicalExceptDataSha256": strip(reg_b) == strip(reg_a) and reg_b["sessionOffsetMeters"] == reg_a["sessionOffsetMeters"],
                         "sessionOffsetMeters": reg_a["sessionOffsetMeters"], "laps": offsets},
        "fileSizeBytes": {f: {"before": sizes_b[f], "after": sizes_a[f]} for f in sizes_b},
        "checks": align["checks"],
        "wetSession0729Unchanged": {k: before["fuji_aim_01"][k] == after["fuji_aim_01"][k] for k in ("laps", "zones")},
    }
    (HERE / "before-after-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("shiftSecondsByLap", "independentSteerCheck", "registration")}, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
