"""GPS boundary regression tests: constant motion must not shift at lap cuts."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from build_race import compact_from_timeseries, moving_average, smooth_with_lap_context, stream_telemetry


class LapBoundarySmoothingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2020, 7, 29, tzinfo=timezone.utc)
        self.samples = [self.sample(i) for i in range(10)]

    def sample(self, i):
        return {"t": i * 0.1, "lat": 35 + i * 0.00002, "lng": 139 + i * 0.00003}

    def timestamp(self, i):
        return (self.start + timedelta(seconds=i * 0.1)).isoformat()

    def context(self, indices):
        return {self.timestamp(i): self.sample(i) for i in indices}

    def test_constant_motion_is_not_displaced_at_a_lap_boundary(self):
        lat, lng, meta = smooth_with_lap_context(self.samples, self.context([-2, -1, 10, 11]), self.start, 5, 90)
        for key, values in [("lat", lat), ("lng", lng)]:
            for actual, sample in zip(values, self.samples):
                self.assertAlmostEqual(actual, sample[key], places=12)
        self.assertTrue(meta["complete_boundary_windows"])

    def test_nonlinear_path_matches_smoothing_before_cutting(self):
        full = [self.sample(i) for i in range(-2, 12)]
        for i, sample in enumerate(full):
            sample["lat"] += i * i * 0.00000003
        context = {self.timestamp(i - 2): sample for i, sample in enumerate(full) if i < 2 or i >= 12}
        lat, lng, _ = smooth_with_lap_context(full[2:-2], context, self.start, 5, 90)
        self.assertEqual(lat, moving_average([s["lat"] for s in full], 5)[2:-2])
        self.assertEqual(lng, moving_average([s["lng"] for s in full], 5)[2:-2])

    def test_real_session_endpoint_retains_available_mean_and_reports_missing_context(self):
        lat, _, meta = smooth_with_lap_context(self.samples, {}, self.start, 5, 90)
        self.assertEqual(lat, moving_average([s["lat"] for s in self.samples], 5))
        self.assertFalse(meta["complete_boundary_windows"])

    def test_context_gap_and_bad_fix_do_not_leak_into_lap(self):
        context = self.context([-5, -4, 10, 11])
        context[self.timestamp(10)]["lat"] = 0
        lat, _, meta = smooth_with_lap_context(self.samples, context, self.start, 5, 90)
        self.assertEqual(lat, moving_average([s["lat"] for s in self.samples], 5))
        self.assertEqual(meta["context_points_before"], 0)
        self.assertEqual(meta["context_points_after"], 0)

    def test_disabled_smoothing_uses_no_context(self):
        lat, _, meta = smooth_with_lap_context(self.samples, self.context([-2, -1, 10, 11]), self.start, 1, 90)
        self.assertEqual(lat, [s["lat"] for s in self.samples])
        self.assertEqual(meta["context_points_before"], 0)
        self.assertEqual(meta["context_points_after"], 0)

    def test_only_selected_lap_telemetry_is_kept_but_neighbour_gps_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.csv"
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["vehicle_id", "lap", "telemetry_name", "telemetry_value", "timestamp"])
                writer.writeheader()
                for lap in range(4):
                    for channel in ["speed", "VBOX_Lat_Min", "VBOX_Long_Minutes"]:
                        writer.writerow(dict(vehicle_id="car", lap=lap, telemetry_name=channel, telemetry_value=35, timestamp=self.timestamp(lap)))
            telemetry = stream_telemetry(path, [("car", 1)], "timestamp")
        self.assertEqual(set(telemetry), {("car", 0), ("car", 1), ("car", 2)})
        self.assertEqual(set(telemetry[("car", 0)][self.timestamp(0)]), {"lat", "lng"})
        self.assertIn("speed", telemetry[("car", 1)][self.timestamp(1)])

    def test_time_origin_and_sensor_channels_are_not_rebased(self):
        rows = {self.timestamp(i): {**self.sample(i), "speed": 100 + i, "aps": i} for i in range(10)}
        args = argparse.Namespace(track_id="test", race_id="test", telemetry=Path("source.csv"), time_column="timestamp", smooth_window=5, max_gps_speed_mps=90)
        marker = (self.start - timedelta(milliseconds=16)).isoformat()
        plain = compact_from_timeseries("car", 1, rows, {"start_time": marker}, args)
        padded = compact_from_timeseries("car", 1, rows, {"start_time": marker}, args, self.context([-2, -1, 10, 11]))
        for key in ["t", "speed", "aps", "brake", "steer", "gear", "accx", "accy"]:
            self.assertEqual(plain[key], padded[key])
        self.assertEqual(padded["t"][0], 0)
        self.assertEqual(padded["meta"]["first_sample_after_lap_start_seconds"], 0.016)


if __name__ == "__main__":
    unittest.main()
