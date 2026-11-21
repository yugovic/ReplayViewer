"""T2 step 0: which audio stream carries road / kerb noise? (numbers only -> audio_streams.json)

For each session and both PCM streams (index 1 handler 'SoundHandler', index 2 handler 'TmcdHandler'):
level statistics, fraction of full-scale samples, and the correlation of 0.1 s band energies (dB) with GPS speed
and ECU RPM over lap 2 (overlay mapping). WAVs were extracted losslessly with
  ffmpeg -i <video> -map 0:<1|2> -c:a pcm_s16le <T2_WORK>/audio/<tag>_a<1|2>.wav
"""
import json

import numpy as np
from scipy.io import wavfile
from scipy.signal import stft

from t2_common import HERE, RACES, TAG, WORK, lap_info, logger_to_t, t_to_pts

BANDS = [(20, 80), (80, 200), (200, 500), (500, 1500), (1500, 4000), (4000, 12000), (12000, 20000)]


def main():
    out = {"kind": "measured", "lapUsed": 2, "sessions": {}}
    for race in RACES:
        tag = TAG[race]
        info = lap_info(race)
        z = np.load(WORK / f"xrk_{race}.npz")
        li = info[2]
        res = {}
        for s in (1, 2):
            sr, a = wavfile.read(WORK / f"audio/{tag}_a{s}.wav")
            x = a.astype(np.float32) / 32768
            p0, p1 = t_to_pts(li, 0), t_to_pts(li, li["tEnd"])
            seg = x[int(p0 * sr):int(p1 * sr)]
            f, tt, Z = stft(seg, fs=sr, nperseg=4096, noverlap=4096 - 4800 // 10 * 1)
            P = np.abs(Z) ** 2
            gt = logger_to_t(li, z["GPS Speed_t"], "gps")
            sp = np.interp(tt, gt, z["GPS Speed"])
            rp = np.interp(tt, logger_to_t(li, z["ECU_RPM_t"], "logger"), z["ECU_RPM"])
            bands = []
            for lo, hi in BANDS:
                m = (f >= lo) & (f < hi)
                e = 10 * np.log10(P[m].sum(0) + 1e-12)
                bands.append({"bandHz": [lo, hi], "meanDb": round(float(e.mean()), 1),
                              "rSpeed": round(float(np.corrcoef(e, sp)[0, 1]), 3),
                              "rRpm": round(float(np.corrcoef(e, rp)[0, 1]), 3)})
            res[str(s)] = {"rms": round(float(np.sqrt(np.mean(x ** 2))), 4),
                           "fullScaleFraction": round(float(np.mean(np.abs(x) > 0.99)), 5),
                           "bands": bands}
        out["sessions"][race] = res
    out["verdict"] = ("stream index 1 is the cabin microphone (band energies follow speed/RPM; engine orders and kerb "
                      "rumble visible). Stream index 2 is not sound: constant level identical on both days, ~1 % "
                      "full-scale samples, no correlation with speed or RPM; it is a binary data track "
                      "(see datatrack_summary.json).")
    (HERE / "audio_streams.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for race, r in out["sessions"].items():
        for s, v in r.items():
            print(race, s, v["rms"], v["fullScaleFraction"], [(b["bandHz"], b["rSpeed"], b["rRpm"]) for b in v["bands"]])


if __name__ == "__main__":
    main()
