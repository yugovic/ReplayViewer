"""T2 step 4: add the 'answers' block (tasks a-d) to events_video.json from the JSON outputs of the other steps.
Pure arithmetic on measured values; every number carries a label (measured / derived / estimated)."""
import json

import numpy as np

from t2_common import HERE, ROOT, RACES

TIMING = ROOT / "artifacts/position-gap-2026-10-09/verify/v2_timing_alongtrack.json"


def r3(x):
    return None if x is None else round(float(x), 3)


def rng(a):
    v = list(a["d_offset"]["bestOffsetPerLapS"].values())
    return r3(min(v)), r3(max(v)), a["d_offset"]["sessionMedianS"]


def drift_text(yaw):
    ok = [k for k, v in yaw.items() if v["transientYawLagS"] is not None]
    if not ok:
        return ("not measurable from the video: the 0.5-3 Hz video yaw is too noisy on every lap (r < 0.4); "
                "the data-track clock rates below give the drift")
    q41 = [np.mean([yaw[k]["estimators"][n]["bp0.5-3Hz"]["quarterLagsS"][3] for n in yaw[k]["estimators"]])
           - np.mean([yaw[k]["estimators"][n]["bp0.5-3Hz"]["quarterLagsS"][0] for n in yaw[k]["estimators"]]) for k in ok]
    return (f"no consistent drift over {len(ok)} laps: the 0.5-3 Hz quarter-lap lags change by a median of {r3(np.median(q41))} s "
            f"(range {r3(min(q41))}..{r3(max(q41))}) from the first to the last quarter; the data-track clock rates "
            "below bound the true drift to about 1-3 ms per lap for video vs GPS and about 14 ms per lap for video vs the logger clock")


def main():
    ev = json.loads((HERE / "events_video.json").read_text(encoding="utf-8"))
    dt = json.loads((HERE / "datatrack_summary.json").read_text(encoding="utf-8"))["sessions"]
    ch = json.loads((HERE / "imu_channel_timing.json").read_text(encoding="utf-8"))
    old = json.loads((ROOT / "artifacts/video-position-gap-2026-10-02/channel-timing-audit.json").read_text(encoding="utf-8"))
    spd = json.loads(TIMING.read_text(encoding="utf-8"))["speed"]
    A = {}
    for race in RACES:
        S = ev["sessions"][race]
        D = dt[race]
        bests = {k: v["bestOffsetS"] for k, v in S["perLapOffsets"].items()}
        v_ms = spd[race]["timeWeightedMedianKmh"] / 3.6
        yaw = S["yawPerLap"]
        tr = {k: v["transientYawLagS"] for k, v in yaw.items()}
        kes = S["kerbEventSummary"]
        conf = {c: sum(1 for e in S["kerbEvents"] if e["audio"]["confidence"] == c) for c in ("high", "medium", "low", "none")}
        confs = {c: sum(1 for e in S["kerbEvents"] if e["shake"]["confidence"] == c) for c in ("high", "medium", "low", "none")}
        A[race] = {
            "a_audioKerbRumble": {
                "label": "measured",
                "stream": "audio stream index 1 (cabin microphone); index 2 is a binary data track, not sound",
                "detector": "20-120 Hz energy (0.1 s centred), main burst on the published event extent moved earlier by the "
                            "session prior; onset/offset = 50 % of the burst rise (start/end = base + 6 dB)",
                "events": len(S["kerbEvents"]), "confidenceCounts": conf,
                "imuAccelOnsetMinusAudioOnsetOverlayMapS": kes["imuMinusAudioOnsetS"],
                "afterLapOffsetCorrection_imuMinusAudioS": kes["imuOnsetMinusCorrectedAudioOnsetS"]},
            "b_videoShake": {
                "label": "measured",
                "detector": "HP > 4 Hz frame-to-frame motion energy of the windshield scene (Farneback dx/dy) and of the "
                            "dashboard (phase correlation), 30 fps, 0.1 s centred; same burst rule as audio",
                "confidenceCounts": confs,
                "imuAccelOnsetMinusShakeOnsetOverlayMapS": kes["imuMinusShakeOnsetS"],
                "afterLapOffsetCorrection_imuMinusShakeS": kes["imuOnsetMinusCorrectedShakeOnsetS"],
                "shakeMinusAudioOnsetS": kes["shakeMinusAudioOnset50S"]},
            "c_videoYaw": {
                "label": "measured",
                "transientYawLagPerLapS": tr,
                "transientYawLagRule": "mean of two video-yaw estimators (far scene; ground plane with parallax removed), "
                                       "0.5-3 Hz band-passed, cross-correlated with the 50 Hz gyro YawRate (logger clock + lap shift)",
                "broadbandCombinedPerLap": {k: [v["combinedBroadbandLagS"], v["combinedBroadbandR"]] for k, v in yaw.items()},
                "withinLapDrift": drift_text(yaw),
                "clockRatesPpm": D["clockRates"],
                "gyroVsGpsCourseRateLagS": {k: v["gyroVsGpsCourseRateLagS"] for k, v in yaw.items()},
                "turnInSummary": S["turnInSummary"],
                "turnInVideoOnsetMinusLapOffsetS": {
                    "label": "derived",
                    "definition": "(gyro onset - video onset) - bestOffsetS of the lap; 0 = the corner's video turn-in onset "
                                  "agrees with the lap clock offset; > 0 = the video onset rule fires early",
                    **{d: (lambda v: {"n": len(v), "median": r3(np.median(v)) if v else None,
                                      "iqr": [r3(np.percentile(v, 25)), r3(np.percentile(v, 75))] if v else None})(
                        [c["gyroMinusVideoOnsetS"] - bests[k] for k, cl in S["turnInPerLap"].items() for c in cl
                         if c["direction"] == d and c["gyroMinusVideoOnsetS"] is not None])
                       for d in ("right", "left")}}},
            "d_offset": {
                "bestOffsetPerLapS": bests,
                "bestBasisPerLap": {k: v["bestBasis"] for k, v in S["perLapOffsets"].items()},
                "bestLabelPerLap": {k: v["bestLabel"] for k, v in S["perLapOffsets"].items()},
                "sessionMedianS": S["sessionBestOffsetMedianS"],
                "alongTrackAtMedianSpeedM": {"label": "derived", "speedKmh": round(spd[race]["timeWeightedMedianKmh"], 1),
                                             "metres": r3(S["sessionBestOffsetMedianS"] * v_ms)},
                "lapStartPtsOverlay": {k: v["lapStartPtsOverlay"] for k, v in S["perLapOffsets"].items()},
                "lapStartPtsCorrected": {k: v["lapStartPtsCorrected"] for k, v in S["perLapOffsets"].items()},
                "bestMinusDataTrackLoggerS": {k: v["bestMinusDataTrackLoggerS"] for k, v in S["perLapOffsets"].items()},
                "bestMinusDataTrackGpsLatestS": {k: v["bestMinusDataTrackGpsLatestS"] for k, v in S["perLapOffsets"].items()},
                "previousAudit": {"label": "measured in 2026-10-02 (old windshield-window flow, r 0.72-0.81)",
                                  "medians": old["video"][race]["medians"]}},
        }
    cal = ev["dataTrackCalibration"]
    ev["answers"] = {
        "sessions": A,
        "explanation": {
            "label": "derived from the measured numbers above",
            "summary": ("The 0.185-0.285 s 'video yaw lead' is a real time offset between the scene in the video and the "
                        "overlay LAP timer used to place the video on the lap clock: the scene is about "
                        f"{rng(A['fuji_aim_01'])[2]}-{rng(A['fuji_aim_2020_07_30'])[2]} s later "
                        "on the lap clock than the overlay timer says (7/30 measured "
                        f"{rng(A['fuji_aim_2020_07_30'])[0]}-{rng(A['fuji_aim_2020_07_30'])[1]} s per lap, median "
                        f"{rng(A['fuji_aim_2020_07_30'])[2]}; 7/29 estimated {rng(A['fuji_aim_01'])[0]}-{rng(A['fuji_aim_01'])[1]}, "
                        f"median {rng(A['fuji_aim_01'])[2]}). It is not vehicle physics and not a GPS processing delay."),
            "evidence": [
                "Video yaw is the rigid-body yaw of the camera, the same motion as the gyro; the transient (0.5-3 Hz) "
                "lag of the gyro behind the overlay-mapped video yaw is 0.135-0.274 s per lap on 7/30 (mean of two "
                "independent video estimators, which agree within 0.04 s on every lap).",
                "Kerb strikes are physically simultaneous in sound, camera shake and accelerometers: the cabin-audio rumble "
                "onsets lead the accelerometer onsets by a median "
                f"{ev['sessions']['fuji_aim_2020_07_30']['kerbEventSummary']['imuMinusAudioOnsetS']['high'].get('median')} s "
                f"({ev['sessions']['fuji_aim_2020_07_30']['kerbEventSummary']['imuMinusAudioOnsetS']['high'].get('n')} "
                "high-confidence 7/30 events, overlay map); after applying "
                "each lap's yaw-derived offset the residual is "
                f"{ev['sessions']['fuji_aim_2020_07_30']['kerbEventSummary']['imuOnsetMinusCorrectedAudioOnsetS'].get('median')} s "
                "(median, IQR +-0.05 s): audio and video yaw agree on the offset.",
                "The gyro agrees with the GPS position course rate within +-0.025 s on 7/30 and -0.015..-0.041 s on 7/29 "
                "(gyroVsGpsCourseRateLagS), "
                "so the old comparisons against GPS course rate saw the same ~0.2 s; accy/v adds a few hundredths of "
                "physical lag (lateral acceleration builds after yaw rate), which is why that reference gave larger numbers.",
                "The SmartyCam data track (audio stream 2) stores, for every 5th frame, the logger clock and the newest GPS "
                "epoch held by the camera. On 7/30 the scene's true lap time equals the data-track logger-clock lap time "
                f"{cal['meanS']} +- {cal['sdS']} s (6 laps), and the newest GPS epoch is about 0.12 s older than the scene. "
                "The overlay-timer lap time is 0.01-0.13 s older than even that GPS epoch, i.e. the overlay graphics are "
                "rendered from a data snapshot about 0.2 s older than the image (the pedal bars a further ~0.09 s older).",
                "The lap-to-lap spread of the overlay-based offset (0.135-0.274 s) is reproduced by the data track to "
                f"+-{cal['sdS']} s, so it comes from the 3 manual overlay-timer reads per lap, not from the clocks.",
                "Clock rates from the data track: GPS iTOW vs video PTS -8.7 / +19 ppm (7/29 / 7/30); logger clock vs PTS "
                "-101 / -104 ppm. The logger clock therefore drifts ~0.11 ms/s against GPS, which is the growth of the "
                "7/30 per-lap shift (0.497 -> 0.583 s); on 7/29 the same drift (~40 ms over laps 1-4) is not corrected in "
                "the shipped data (shift 0)."],
            "residualUnknowns": [
                "The ~0.05 s by which the camera-held logger time leads the scene is either camera capture-to-record latency "
                "or IMU filter delay; the data cannot separate them (estimated).",
                "7/29 offsets are estimated: the wet video yaw is too noisy at 0.5-3 Hz (rain, fog, wipers), so the 7/30 "
                "data-track calibration is applied assuming the same camera/logger latency; one high-confidence 7/29 kerb "
                "event agrees within 0.02 s.",
                "ECU_STEER_ANG turn-in onsets are not earlier than the gyro onsets (median +0.03 s right-hand, +0.14 s "
                "left-hand corners on 7/30), although steering must precede yaw: the ECU steering channel likely carries "
                "extra latency (estimated) and should not be used as a sub-0.1 s time reference without calibration.",
                "Video turn-in onsets per corner have estimator-dependent left/right biases of about +-0.1 s; use the lap "
                "offsets, not single corners, for clock work."]},
        "howToUse": ("For a video frame at PTS p in lap k: true lap t = p - lapStartPtsCorrected[k] - delta[k] "
                     "(= overlay-mapped lap t + bestOffsetS[k]). Equivalent data-track form (7/30, measured): lap t = "
                     "(loggerMs(p) - libxrkOffsetMs - tc0Ms)/1000 + shift + " f"({cal['meanS']}) s.")}
    (HERE / "events_video.json").write_text(json.dumps(ev, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(ev["answers"]["explanation"]["summary"]))
    for race in RACES:
        print(race, A[race]["d_offset"]["bestOffsetPerLapS"], A[race]["d_offset"]["alongTrackAtMedianSpeedM"])


if __name__ == "__main__":
    main()
