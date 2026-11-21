"""T2 step 1: extract native XRK channels (IMU 50 Hz, ECU 20 Hz, GPS 10 Hz) to <T2_WORK>/xrk_<race>.npz.

Checks the lap mapping (tc0Ms from kerb_contacts.json timeBase.lapMapping) by re-matching GPS Speed.
Writes only logger numbers (local npz, not in the repo) and a small JSON report next to this script.
"""
import json

import numpy as np
from libxrk import aim_xrk

from t2_common import HERE, RACES, WORK, XRK, lap_info, load_lap

CH = ["InlineAcc", "LateralAcc", "VerticalAcc", "RollRate", "PitchRate", "YawRate", "ECU_STEER_ANG", "ECU_RPM",
      "ECU_WS_FL", "ECU_WS_FR", "ECU_WS_RL", "ECU_WS_RR", "ECU_BRK_P", "ECU_PPS", "GPS Speed", "GPS_Yaw_Rate",
      "GPS Latitude", "GPS Longitude"]


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    report = {}
    for race in RACES:
        log = aim_xrk(XRK[race])
        out = {}
        for c in CH:
            tb = log.channels[c]
            out[c + "_t"] = tb.column("timecodes").to_numpy().astype(np.float64)
            out[c] = tb.column(c).to_numpy().astype(np.float64)
        info = lap_info(race)
        gs_t, gs = out["GPS Speed_t"], out["GPS Speed"]
        unit = 3.6 if np.nanmax(gs) < 100 else 1.0
        chk = {}
        for k, li in info.items():
            d = load_lap(race, k)
            t = np.asarray(d["t"], float)
            v = np.interp(li["tc0Ms"] + 1000 * t, gs_t, gs) * unit
            chk[k] = round(float(np.sqrt(np.mean((v - np.asarray(d["speed"])) ** 2))), 5)
        out["laps_xrk"] = json.dumps(log.laps.to_pydict())
        np.savez_compressed(WORK / f"xrk_{race}.npz", **out)
        report[race] = {"lapMappingSpeedRmsKmh": chk,
                        "medianSampleMs": {c: float(np.median(np.diff(out[c + "_t"]))) for c in CH},
                        "lapInfo": {k: {kk: round(vv, 4) for kk, vv in li.items()} for k, li in info.items()}}
        print(race, chk)
    (HERE / "xrk_extract_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
