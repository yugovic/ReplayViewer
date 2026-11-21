"""T2 chart (numbers only, no video/audio pixels): per-lap video<->logger offset estimates.
Run with a python that has matplotlib (e.g. pipeline/.venv-sr). Output: offsets_per_lap.png next to this script."""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
ev = json.loads((HERE / "events_video.json").read_text(encoding="utf-8"))
labels, xs = [], []
series = {"transientVideoYawVsGyro": ([], [], "o", "#1f6feb", "video yaw vs gyro, 0.5-3 Hz (measured)"),
          "kerbAudioOnsets": ([], [], "s", "#d97706", "kerb audio onsets, lap median (measured)"),
          "kerbShakeOnsets": ([], [], "^", "#7c3aed", "kerb video-shake onsets, lap median (measured)"),
          "dataTrackLoggerClockPlusCalibration": ([], [], "x", "#111827", "data-track logger clock + 7/30 calibration")}
i = 0
for race, tag in (("fuji_aim_01", "7/29"), ("fuji_aim_2020_07_30", "7/30")):
    for k, v in ev["sessions"][race]["perLapOffsets"].items():
        labels.append(f"{tag} L{k}")
        xs.append(i)
        for e in v["estimates"]:
            if e["method"] in series and e["lagS"] is not None:
                series[e["method"]][0].append(i + {"transientVideoYawVsGyro": -0.15, "kerbAudioOnsets": -0.05,
                                                   "kerbShakeOnsets": 0.05, "dataTrackLoggerClockPlusCalibration": 0.15}[e["method"]])
                series[e["method"]][1].append(e["lagS"])
        i += 1
fig, ax = plt.subplots(figsize=(12.5, 4.2))
for name, (x, y, m, c, lab) in series.items():
    ax.scatter(x, y, marker=m, color=c, label=lab, s=36)
ax.axhline(0, color="#9ca3af", lw=0.8)
ax.axvline(3.5, color="#d1d5db", lw=0.8, ls="--")
ax.set_xticks(xs)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("offset L (s): true lap t = overlay-mapped lap t + L")
ax.set_title("SmartyCam scene vs logger clock, per lap (Fuji 2020-07-29 / 07-30)", fontsize=10)
ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
ax.grid(axis="y", color="#e5e7eb")
fig.tight_layout()
fig.savefig(HERE / "offsets_per_lap.png", dpi=110)
print("saved")
