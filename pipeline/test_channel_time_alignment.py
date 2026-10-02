"""Logger-clock channel time alignment: synthetic checks.

* build_race: the linear CSV re-timing (iteration 1) and its config guard.
* scripts/quality/align-fuji-channel-timing.py: the native XRK replay (iteration 2:
  convert_aim on a delayed 10 Hz grid) and the 7/29-referenced multi-pair estimate.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np

import build_race as br
from build_race import (
    ALIGNABLE_CHANNELS,
    alignment_context_channels,
    compact_from_timeseries,
    lap_channel_alignment,
    sample_channel_at,
    stream_telemetry,
)

ROOT = Path(__file__).resolve().parents[1]


def load_align_script():
    """scripts/quality/align-fuji-channel-timing.py as a module.

    Its native replay reuses pipeline/convert_aim.py, whose module import needs libxrk and
    pyarrow; the helpers under test (CHANNEL_MAP, repair_non_finite, ms_to_iso) need neither,
    so absent packages are stubbed for the import.
    """
    for name in ("libxrk", "pyarrow"):
        if name not in sys.modules and importlib.util.find_spec(name) is None:
            stub = types.ModuleType(name)
            stub.aim_xrk = None
            sys.modules[name] = stub
    path = ROOT / "scripts" / "quality" / "align-fuji-channel-timing.py"
    spec = importlib.util.spec_from_file_location("align_fuji_channel_timing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

GPS_KEYS = ("t", "lat", "lng", "speed", "dist")
EARLY = 0.3  # the synthetic logger stamps its channels 0.3 s before GPS time


def truth(t):
    """Channel values as they happen on the GPS time base."""
    return {
        "aps": 10.0 + 20.0 * t,
        "brake_f": 5.0 * t if t > 0.55 else 0.0,
        "brake_r": 2.0 + t,
        "steer": -3.0 + 4.0 * t,
        "gear": 4.0 if t >= 0.55 else 3.0,
        "accx": 0.5 * t,
        "accy": -0.25 * t,
    }


class ChannelTimeAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2020, 7, 30, tzinfo=timezone.utc)
        self.args = argparse.Namespace(track_id="test", race_id="test", telemetry=Path("source.csv"),
                                       time_column="timestamp", smooth_window=5, max_gps_speed_mps=90)

    def timestamp(self, i):
        return (self.start + timedelta(seconds=i * 0.1)).isoformat()

    def row(self, i, early=EARLY):
        t = i * 0.1
        # Logger channel stamped at t holds what physically happens at t + early.
        return {"lat": 35 + i * 0.00002, "lng": 139 + i * 0.00003, "speed": 100.0 + i, **truth(t + early)}

    def rows(self, indices, early=EARLY):
        return {self.timestamp(i): self.row(i, early) for i in indices}

    def build(self, alignment=None, context=None, lap_indices=range(12)):
        context = self.rows(range(-8, 0)) if context is None else context
        return compact_from_timeseries("car", 1, self.rows(lap_indices), {"start_time": self.timestamp(0)},
                                       self.args, context, alignment)

    def alignment(self, shift, channels=None):
        config = {"shiftSeconds": shift, "estimate": {"method": "synthetic"}}
        if channels:
            config["channels"] = channels
        return lap_channel_alignment(config, "car", 1)

    def test_sampling_is_exact_on_samples_linear_between_and_nearest_for_discrete(self):
        times, values = [0.0, 0.1, 0.2], [1.0, 3.0, 2.0]
        self.assertEqual(sample_channel_at(times, values, 0.1), (3.0, False))
        value, held = sample_channel_at(times, values, 0.05)
        self.assertAlmostEqual(value, 2.0, places=12)
        self.assertFalse(held)
        self.assertEqual(sample_channel_at(times, values, 0.04, nearest=True), (1.0, False))
        self.assertEqual(sample_channel_at(times, values, 0.06, nearest=True), (3.0, False))
        self.assertEqual(sample_channel_at([0.0, 1.0], [1.0, 3.0], 0.5, nearest=True), (1.0, False))  # tie -> earlier
        self.assertEqual(sample_channel_at(times, values, -0.5), (1.0, True))
        self.assertEqual(sample_channel_at(times, values, 0.7), (2.0, True))

    def test_shift_delays_logger_channels_onto_gps_time(self):
        lap = self.build(self.alignment(EARLY))
        for i, t in enumerate(lap["t"]):
            expected = truth(t)
            self.assertAlmostEqual(lap["aps"][i], round(expected["aps"], 3), places=9)
            self.assertAlmostEqual(lap["steer"][i], round(expected["steer"], 3), places=9)
            self.assertAlmostEqual(lap["accx"][i], round(expected["accx"], 4), places=9)
            self.assertAlmostEqual(lap["accy"][i], round(expected["accy"], 4), places=9)
            self.assertEqual(lap["gear"][i], int(expected["gear"]))
        unaligned = self.build()
        self.assertEqual(unaligned["gear"].index(4), 3)  # recorded 0.3 s early
        self.assertEqual(lap["gear"].index(4), 6)        # back at GPS time 0.55 -> first sample 0.6

    def test_sub_sample_shift_interpolates_linearly(self):
        lap = self.build(self.alignment(0.25))
        for i, t in enumerate(lap["t"]):
            # logger(t - 0.25) = truth(t + 0.05); linear channels interpolate exactly
            self.assertAlmostEqual(lap["accx"][i], round(0.5 * (t + 0.05), 4), places=9)
            self.assertAlmostEqual(lap["aps"][i], round(10.0 + 20.0 * (t + 0.05), 3), places=9)

    def test_gps_arrays_and_other_meta_are_bit_identical(self):
        plain = self.build()
        lap = self.build(self.alignment(EARLY))
        for key in GPS_KEYS:
            self.assertEqual(lap[key], plain[key])
        record = lap["meta"].pop("channel_time_alignment")
        self.assertEqual(lap["meta"], plain["meta"])
        self.assertNotIn("channel_time_alignment", plain["meta"])
        self.assertEqual(record["shiftSeconds"], EARLY)
        self.assertEqual(record["channels"], list(ALIGNABLE_CHANNELS))
        self.assertEqual(record["nearestSampleChannels"], ["gear"])
        self.assertEqual(record["estimate"], {"method": "synthetic"})

    def test_zero_shift_reproduces_the_unaligned_build(self):
        plain = self.build()
        lap = self.build(self.alignment(0.0))
        for key in plain:
            if key != "meta":
                self.assertEqual(lap[key], plain[key])

    def test_absent_rear_brake_defaults_like_the_unaligned_build(self):
        def front_only(rows):
            return {ts: {**{k: v for k, v in row.items() if k != "brake_r"}, "brake_f": row["brake_f"] - 0.2}
                    for ts, row in rows.items()}
        args = ("car", 1, front_only(self.rows(range(12))), {"start_time": self.timestamp(0)}, self.args,
                front_only(self.rows(range(-8, 0))))
        plain = compact_from_timeseries(*args)
        lap = compact_from_timeseries(*args, self.alignment(0.0))
        self.assertEqual(lap["brake"], plain["brake"])
        self.assertEqual(min(lap["brake"]), 0.0)  # max(front - 0.2, absent rear = 0.0)

    def test_lap_start_uses_neighbour_lap_rows_without_holding_edges(self):
        lap = self.build(self.alignment(EARLY))
        record = lap["meta"]["channel_time_alignment"]
        self.assertEqual(record["samplesFromNeighbourLaps"], 3)  # t = 0.0, 0.1, 0.2 read from before the lap
        self.assertEqual(record["heldEdgeSamples"], 0)
        self.assertAlmostEqual(lap["aps"][0], 10.0, places=9)

    def test_missing_context_holds_the_edge_and_reports_it(self):
        gps_only = {ts: {"lat": row["lat"], "lng": row["lng"]} for ts, row in self.rows(range(-8, 0)).items()}
        lap = self.build(self.alignment(EARLY), context=gps_only)
        self.assertEqual(lap["meta"]["channel_time_alignment"]["heldEdgeSamples"], 3)
        self.assertEqual(lap["aps"][:3], [lap["aps"][3]] * 3)

    def test_negative_shift_advances_and_uses_the_following_lap(self):
        lap_rows = self.rows(range(12), early=-EARLY)  # logger stamped 0.3 s late
        context = self.rows([*range(-8, 0), *range(12, 20)], early=-EARLY)
        lap = compact_from_timeseries("car", 1, lap_rows, {"start_time": self.timestamp(0)}, self.args, context,
                                      self.alignment(-EARLY))
        for i, t in enumerate(lap["t"]):
            self.assertAlmostEqual(lap["accx"][i], round(0.5 * t, 4), places=9)
        self.assertEqual(lap["meta"]["channel_time_alignment"]["samplesFromNeighbourLaps"], 3)

    def test_only_listed_channels_move_and_brake_stays_front_rear_max(self):
        plain = self.build()
        lap = self.build(self.alignment(EARLY, channels=["brake", "accx"]))
        for key in ("aps", "steer", "gear", "accy"):
            self.assertEqual(lap[key], plain[key])
        for i, t in enumerate(lap["t"]):
            expected = truth(t)
            self.assertAlmostEqual(lap["brake"][i], round(max(expected["brake_f"], expected["brake_r"]), 3), places=9)
        self.assertNotEqual(lap["accx"], plain["accx"])

    def test_alignment_config_resolution(self):
        config = {"shiftSeconds": 0.5, "laps": {"2": {"shiftSeconds": 0.45}, "car:3": {"shiftSeconds": 0.55}, "3": {"shiftSeconds": 9}}}
        self.assertIsNone(lap_channel_alignment(None, "car", 1))
        self.assertIsNone(lap_channel_alignment({"laps": {"2": {"shiftSeconds": 0.4}}}, "car", 1))
        self.assertEqual(lap_channel_alignment(config, "car", 1)["shiftSeconds"], 0.5)
        self.assertEqual(lap_channel_alignment(config, "car", 2)["shiftSeconds"], 0.45)
        self.assertEqual(lap_channel_alignment(config, "car", 3)["shiftSeconds"], 0.55)  # vehicle:lap wins
        with self.assertRaises(SystemExit):
            lap_channel_alignment(config, "other", 3)  # falls back to "3": 9 s is implausible
        with self.assertRaises(SystemExit):
            lap_channel_alignment({"shiftSeconds": float("nan")}, "car", 1)
        with self.assertRaises(SystemExit):
            lap_channel_alignment({"shiftSeconds": 0.5, "channels": ["speed"]}, "car", 1)  # GPS is never re-timed

    def test_native_sampler_configs_are_refused(self):
        config = {"sampler": "csv-linear-v1", "shiftSeconds": 0.5}
        self.assertEqual(lap_channel_alignment(config, "car", 1)["shiftSeconds"], 0.5)
        with self.assertRaises(SystemExit):  # values would differ from the XRK replay that wrote the config
            lap_channel_alignment({"sampler": "xrk-native-v1", "shiftSeconds": 0.5}, "car", 1)

    def test_context_streaming_keeps_neighbour_logger_channels_only_when_asked(self):
        self.assertEqual(alignment_context_channels(None), ("lat", "lng"))
        keys = alignment_context_channels({"channels": ["brake", "accx"]})
        self.assertEqual(keys, ("lat", "lng", "brake_f", "brake_r", "accx"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.csv"
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["vehicle_id", "lap", "telemetry_name", "telemetry_value", "timestamp"])
                writer.writeheader()
                for lap in range(3):
                    for channel in ["VBOX_Lat_Min", "VBOX_Long_Minutes", "speed", "accx_can", "pbrake_f"]:
                        writer.writerow(dict(vehicle_id="car", lap=lap, telemetry_name=channel, telemetry_value=1, timestamp=self.timestamp(lap)))
            default = stream_telemetry(path, [("car", 1)], "timestamp")
            aligned = stream_telemetry(path, [("car", 1)], "timestamp", keys)
        self.assertEqual(set(default[("car", 0)][self.timestamp(0)]), {"lat", "lng"})
        self.assertEqual(set(aligned[("car", 0)][self.timestamp(0)]), {"lat", "lng", "accx", "brake_f"})
        self.assertEqual(aligned[("car", 1)], default[("car", 1)])


def fake_xrk(spike_tc=None, spike=2.5):
    """Native series like the 7/30 MXL2 XRK: GPS 10 Hz (67 ms phase, no fix for the first
    samples), ECU 20 Hz (13 ms phase, brake +inf without CAN message), IMU 50 Hz (5 ms phase).
    name -> (timecodes ms, values, interpolate)."""
    gps = np.arange(67, 9000, 100)
    ecu = np.arange(13, 9000, 50)
    imu = np.arange(5, 9000, 20)
    lat = (35.37 + 2e-6 * np.arange(len(gps))).astype(float)
    lng = (138.93 + 3e-6 * np.arange(len(gps))).astype(float)
    sats = np.full(len(gps), 9.0, np.float32)
    lat[:3] = 0
    lng[:3] = 0
    sats[:3] = 0
    brake = (40 * np.clip(np.sin(ecu / 700.0), 0, None)).astype(np.float32)
    brake[(ecu > 2000) & (ecu < 2900)] = np.inf     # long 'no message' run -> 0 bar
    brake[(ecu > 4500) & (ecu < 4560)] = np.inf     # one missing message -> interpolated
    accy = (1.2 * np.sin(imu / 400.0)).astype(np.float32)
    if spike_tc is not None:
        accy[imu == spike_tc] = spike
    return {
        "GPS Latitude": (gps, lat, True), "GPS Longitude": (gps, lng, True),
        "GPS Speed": (gps, 30 + np.sin(gps / 900.0), True), "GPS Altitude": (gps, 600 + 0.0 * gps, True),
        "GPS_Satellites": (gps, sats, False),
        "ECU_TPS": (ecu, (50 + 50 * np.sin(ecu / 300.0)).astype(np.float32), True),
        "ECU_BRK_P": (ecu, brake, True),
        "ECU_STEER_ANG": (ecu, (90 * np.sin(ecu / 500.0)).astype(np.float32), True),
        "ECU_GEAR": (ecu, (3 + (ecu // 1500) % 3).astype(np.int16), False),
        "ECU_RPM": (ecu, (3000 + ecu // 3).astype(np.int16), False),
        "InlineAcc": (imu, (0.6 * np.cos(imu / 350.0)).astype(np.float32), True),
        "LateralAcc": (imu, accy, True),
    }


def libxrk_like_resampler(series):
    """libxrk LogFile.resample_to_timecodes semantics: np.interp (cast back to the float type)
    for interpolated channels, previous sample (leading: first sample) otherwise."""
    def resample(grid):
        out = {}
        for name, (tc, values, interp) in series.items():
            if interp:
                r = np.interp(grid, tc, values.astype(float))
                out[name] = r.astype(values.dtype) if values.dtype.kind == "f" else r
            else:
                idx = np.searchsorted(tc, grid, side="right") - 1
                r = values[np.clip(idx, 0, len(values) - 1)].copy()
                r[idx < 0] = values[0]
                out[name] = r
        return out
    return resample


class NativeXrkReplayTests(unittest.TestCase):
    """Iteration 2: re-timed values come from the native series via the convert_aim replay."""

    @classmethod
    def setUpClass(cls):
        cls.align = load_align_script()
        cls.ca = cls.align.convert_aim_module()
        cls.anchor = datetime(2020, 7, 30, 15, 53, 29, tzinfo=timezone.utc)
        cls.keys = cls.align.converter_keys()
        cls.args = argparse.Namespace(track_id="test", race_id="test", telemetry=Path("source.csv"),
                                      time_column="timestamp", smooth_window=5, max_gps_speed_mps=90)

    def session(self, **kw):
        series = fake_xrk(**kw)
        resample = libxrk_like_resampler(series)
        hi = min(int(tc.max()) for tc, _, _ in series.values())
        grid = np.arange(0, hi + 1, 100, dtype=np.int64)
        align, ca = self.align, self.ca

        def converted(shift_ms):
            kept, values = align.convert_grid(resample, grid - shift_ms, ca.CHANNEL_MAP, ca, 100)
            return align.ConvertedGrid(kept, values, 100)

        conv0 = converted(0)
        rows = {}
        for j, tc in enumerate(conv0.grid):
            row = {key: float(conv0.values[name][j]) for key, name in self.keys.items() if name in conv0.values}
            rows[ca.ms_to_iso(self.anchor, int(tc))] = row
        return series, converted, rows

    def timecode(self, ts):
        return int(round((br.parse_iso(ts) - self.anchor).total_seconds() * 1000))

    def split(self, rows, t0=3000, t1=6000):
        lap = {ts: r for ts, r in rows.items() if t0 <= self.timecode(ts) < t1}
        context = {ts: r for ts, r in rows.items() if not t0 <= self.timecode(ts) < t1}
        return lap, context

    def build(self, lap_rows, context, alignment=None):
        start = min(lap_rows, key=self.timecode)
        return compact_from_timeseries("car", 1, lap_rows, {"start_time": start}, self.args, context, alignment)

    def test_convert_grid_replays_convert_aim(self):
        series, converted, _ = self.session()
        conv = converted(0)
        self.assertEqual(int(conv.grid[0]), 400)  # GPS-fix guard: no satellites before the 367 ms GPS sample
        brake = conv.values["pbrake_f"]
        self.assertTrue(np.isfinite(brake).all())
        t = conv.grid
        self.assertTrue((brake[(t > 2100) & (t < 2800)] == 0.0).all())  # long +inf run -> 0 bar
        j = conv.index(4500)                                       # one missing message -> interpolated
        self.assertAlmostEqual(brake[j], round((brake[j - 1] + brake[j + 1]) / 2, 3), delta=0.0011)
        self.assertGreater(brake[j], 0.0)
        self.assertTrue(np.array_equal(brake, conv.values["pbrake_r"]))
        k = conv.index(3000)
        gps_t, speed, _ = series["GPS Speed"]
        self.assertEqual(conv.values["speed"][k], round(float(np.interp(3000, gps_t, speed)) * 3.6, 3))
        imu_t, accx, _ = series["InlineAcc"]
        self.assertEqual(conv.values["accx_can"][k], round(float(np.float32(np.interp(3000, imu_t, accx.astype(float)))), 5))
        ecu_t, gear, _ = series["ECU_GEAR"]
        self.assertEqual(conv.values["gear"][k], float(gear[np.searchsorted(ecu_t, 3000, side="right") - 1]))
        self.assertIsNone(conv.index(3050))
        self.assertIsNone(conv.index(10 ** 7))

    def test_shift_zero_reproduces_the_csv_rows_and_lap(self):
        _, converted, rows = self.session()
        lap_rows, context = self.split(rows)
        replay = self.align.replay_matches_rows(lap_rows, converted(0), self.timecode, self.keys)
        self.assertEqual(set(replay.values()), {0.0})
        self.assertIn("accx", replay)
        same, held = self.align.native_rows(lap_rows, converted(0), self.timecode, 0, self.keys)
        self.assertEqual(held, 0)
        self.assertEqual(same, lap_rows)
        self.assertEqual(self.build(same, context), self.build(lap_rows, context))

    def test_delayed_values_are_the_native_series_at_t_minus_shift(self):
        series, converted, rows = self.session()
        lap_rows, context = self.split(rows)
        shift_ms = self.align.shift_ms_of(0.523)
        moved, held = self.align.native_rows(lap_rows, converted(shift_ms), self.timecode, shift_ms, self.keys)
        self.assertEqual(held, 0)
        plain, lap = self.build(lap_rows, context), self.build(moved, context)
        for key in GPS_KEYS:
            self.assertEqual(lap[key], plain[key])
        self.assertEqual(lap["meta"], plain["meta"])
        imu_t, accy, _ = series["LateralAcc"]
        tc0 = self.timecode(min(lap_rows, key=self.timecode))
        for i, t in enumerate(lap["t"]):
            src = tc0 + round(1000 * t) - shift_ms
            want = round(round(float(np.float32(np.interp(src, imu_t, accy.astype(float)))), 5), 4)
            self.assertEqual(lap["accy"][i], want)
        # the brake 'no message' run (logger 2000-2900 ms) shows up 0.523 s later on the GPS time base
        t_gps = np.asarray(lap["t"]) + tc0 / 1000
        brake = np.asarray(lap["brake"])
        window = (t_gps > 2.62) & (t_gps < 3.32)
        self.assertTrue(window.any())
        self.assertTrue((brake[window] == 0.0).all())

    def test_native_replay_keeps_peaks_that_10hz_interpolation_flattens(self):
        spike_tc = 4005                      # native IMU sample; +295 ms lands on the 10 Hz grid (4300)
        _, converted, rows = self.session(spike_tc=spike_tc, spike=2.5)
        lap_rows, context = self.split(rows)
        moved, _ = self.align.native_rows(lap_rows, converted(295), self.timecode, 295, self.keys)
        native = self.build(moved, context)
        linear = self.build(lap_rows, context, lap_channel_alignment({"shiftSeconds": 0.295}, "car", 1))
        self.assertEqual(max(native["accy"]), 2.5)
        self.assertLess(max(linear["accy"]), 0.8 * 2.5)   # the iteration-1 regression mechanism
        with self.assertRaises(SystemExit):
            self.align.shift_ms_of(0.2955)


def synthetic_lap(early=0.0, delays=None, duration=40.0):
    """Lap dict on the GPS time base plus a 50 Hz yaw-gyro series (lap seconds, deg/s).

    Logger channels hold what physically happens at t + early (stamped early) and, per pair,
    a session-independent physical delay (delays[pair] > 0: the channel lags the GPS quantity)."""
    delays = delays or {}
    origin = {"lat": 35.37, "lng": 138.93}
    dt = 0.001
    tf = np.arange(-5.0, duration + 5.0, dt)
    v = 30 + 8 * np.sin(2 * np.pi * tf / 9) + 3 * np.sin(2 * np.pi * tf / 4.1)
    w = 0.3 * np.sin(2 * np.pi * tf / 7) + 0.12 * np.sin(2 * np.pi * tf / 3.3)
    psi = np.cumsum(w) * dt
    x, z = np.cumsum(v * np.sin(psi)) * dt, np.cumsum(-v * np.cos(psi)) * dt
    along = np.gradient(v, dt)

    def at(sig, t):
        return np.interp(t, tf, sig)

    t = np.round(np.arange(0.0, duration, 0.1), 3)
    m = 6378137.0 * math.pi / 180
    lap = {"t": t.tolist(), "lat": (origin["lat"] - at(z, t) / m).tolist(),
           "lng": (origin["lng"] + at(x, t) / (m * math.cos(math.radians(origin["lat"])))).tolist(),
           "speed": (at(v, t) * 3.6).tolist()}

    def logger(sig, pair):
        return at(sig, t + early - delays.get(pair, 0.0))

    lap["accx"] = (logger(along, "accxVsGpsAlong") / 9.80665).tolist()
    lap["accy"] = (logger(v * w, "accyVsGpsLateral") / 9.80665).tolist()
    lap["brake"] = (10 * np.clip(-logger(along, "brakeVsGpsDecel"), 0, None)).tolist()
    lap["steer"] = (15 * logger(w, "steerVsGpsCourseRate")).tolist()
    tg = np.arange(-1.0, duration + 1.0, 0.02)
    gyro = (tg, np.degrees(at(w, tg + early - delays.get("yawGyroVsGpsCourseRate", 0.0))))
    return lap, origin, gyro


class ReferencedEstimateTests(unittest.TestCase):
    """Iteration 2: shift = -median over pairs of (lag - 7/29 median lag of the same pair)."""

    @classmethod
    def setUpClass(cls):
        cls.align = load_align_script()

    def test_median_over_pairs_relative_to_the_reference(self):
        pairs = self.align.PAIRS
        ref = dict(zip(pairs, [0.0, 0.03, -0.13, -0.05, 0.04]))
        lags = {p: {"lagS": round(ref[p] + d, 3), "r": 0.9} for p, d in zip(pairs, [-0.47, -0.49, -0.50, -0.57, -0.55])}
        est = self.align.ref0729_shift(lags, ref)
        self.assertEqual(est["shiftSeconds"], 0.5)
        self.assertEqual(est["deltaRangeS"], [-0.57, -0.47])
        self.assertEqual(est["pairs"]["brakeVsGpsDecel"]["ref0729MedianS"], -0.13)
        ref_laps = {1: {p: {"lagS": 0.01} for p in pairs}, 2: {p: {"lagS": -0.02} for p in pairs},
                    3: {p: {"lagS": 0.05} for p in pairs}}
        self.assertEqual(self.align.reference_medians(ref_laps), {p: 0.01 for p in pairs})
        after = {p: {"lagS": round(ref[p] + d, 3)} for p, d in zip(pairs, [0.03, 0.01, 0.0, -0.07, -0.05])}
        res = self.align.residuals(after, ref)
        self.assertEqual(res["medianS"], 0.0)
        self.assertEqual(res["maxAbsS"], 0.07)
        per_lap = {1: est, 2: {**est, "shiftSeconds": 0.6}}
        plan = self.align.plan_v2("per-lap", per_lap, ref)
        self.assertEqual(plan[2]["shiftSeconds"], 0.6)
        self.assertEqual(plan[1]["estimate"]["sessionMedianShiftSeconds"], 0.55)
        self.assertEqual(self.align.plan_v2("session-median", per_lap, ref)[1]["shiftSeconds"], 0.55)

    def test_pair_delays_cancel_against_the_reference_session(self):
        # Physical delays common to both sessions (brake leads decel, steer leads yaw, IMU slightly late);
        # the target logger clock is 0.3 s early. Referencing each pair to the reference session recovers
        # 0.3 s; forcing the IMU lag to 0 (iteration 1) would absorb the IMU's own +0.02 s.
        delays = {"accxVsGpsAlong": 0.02, "accyVsGpsLateral": 0.02, "brakeVsGpsDecel": -0.08,
                  "steerVsGpsCourseRate": -0.10, "yawGyroVsGpsCourseRate": 0.0}
        ref_lap, origin, ref_gyro = synthetic_lap(0.0, delays)
        ref_lags = self.align.pair_lags(ref_lap, origin, ref_gyro)
        for pair, delay in delays.items():  # brake is clipped at 0 bar: its lag is biased by ~0.015 s, cancelled below
            self.assertAlmostEqual(ref_lags[pair]["lagS"], delay, delta=0.02, msg=pair)
        target, origin, gyro = synthetic_lap(0.3, delays)
        lags = self.align.pair_lags(target, origin, gyro)
        est = self.align.ref0729_shift(lags, self.align.reference_medians({1: ref_lags}))
        self.assertAlmostEqual(est["shiftSeconds"], 0.3, delta=0.006)
        imu_only = -(lags["accxVsGpsAlong"]["lagS"] + lags["accyVsGpsLateral"]["lagS"]) / 2
        self.assertAlmostEqual(imu_only, 0.28, delta=0.006)
        self.assertGreater(lags["yawGyroVsGpsCourseRate"]["r"], 0.99)
        self.assertAlmostEqual(lags["yawGyroVsGpsCourseRate"]["gpsPerGyroScale"], 1.0, delta=0.02)


if __name__ == "__main__":
    unittest.main()
