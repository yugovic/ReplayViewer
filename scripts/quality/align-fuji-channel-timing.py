#!/usr/bin/env python3
"""Re-time the 2020-07-30 Fuji logger-clock channels onto the GPS time base (BACKLOG GPS-REG-08).

On 7/30 the logger's own channels (ECU via CAN: aps, brake, steer, gear; internal IMU:
accx/accy) are stamped about 0.5 s early relative to the GPS fixes. Lap boundaries, t,
lat/lng/speed and dist follow GPS time and stay bit-identical. 7/29 (fuji_aim_01) is the
reference session: its channels are not re-timed (shift 0 by definition).

Iteration 2 (default; needs libxrk + pyarrow + numpy/scipy, e.g. the system Python):

  --sampler xrk-native   Re-timed values come from the NATIVE XRK series (IMU 50 Hz, ECU 20 Hz),
      not from the 10 Hz CSV. pipeline/convert_aim.py is replayed on its own 10 Hz grid
      delayed by shiftSeconds (libxrk resample_to_timecodes: linear for interpolated
      channels, previous sample for ECU_GEAR; GPS-fix guard; non-finite repair, e.g.
      ECU_BRK_P 'no CAN message' -> 0 bar; units and CSV rounding), the delayed values
      replace the logger channels of the CSV rows, and pipeline/build_race.py builds the lap
      (its rounding, brake = max(front, rear)). Self-check: with shift 0 the replay must
      reproduce every converted CSV row of the lap and the unaligned lap JSON byte for
      byte. This removes the peak loss of iteration 1 (linear interpolation between 10 Hz
      samples at fractional offsets: accy |max| up to -13 %).

  --estimate ref-0729    shift_lap = -median over five channel pairs of
      (lag_7/30,lap - median lag of the same pair over the 7/29 laps), on the UNSHIFTED data:
        accxVsGpsAlong          IMU accx      vs GPS along acceleration (position spline)
        accyVsGpsLateral        IMU accy      vs GPS lateral acceleration
        brakeVsGpsDecel         ECU brake     vs GPS deceleration (-d speed/dt)
        steerVsGpsCourseRate    ECU steer     vs GPS course rate
        yawGyroVsGpsCourseRate  XRK YawRate   vs GPS course rate (raw 50 Hz gyro, sign fitted;
                                              the scale is reported, correlation is scale-free)
      lag = s maximising corr(GPS-derived(t), channel(t + s)) (cubic splines, 10 ms grid,
      2 ms scan; scripts/quality/audit-fuji-channel-timing.py best_shift). Referencing each
      pair to its own 7/29 median cancels the pair's physical delay (hydraulics, yaw
      response, GPS smoothing phase), so the result is "7/30 behaves like 7/29" instead of
      "IMU lag = 0" (iteration 1 under-corrected by 0.02-0.08 s). The median over three
      sensor groups (IMU accel, ECU CAN, gyro) resists one biased pair.

  Iteration 1 is reproducible with --sampler csv-linear --estimate imu-v1 (no XRK needed):
  linear interpolation over the 10 Hz converted CSV (build_race --channel-time-alignment)
  and shift = -(mean of the accx/accy IMU-vs-GPS lags).

Steps (fail closed on any unexpected difference):
  1. Rebuild the 7/30 laps from the converted CSV with build_race (no alignment) and require
     the shipped lap JSON to equal that build or the rebuild from its own recorded
     meta.channel_time_alignment (so the script can be re-run after --apply).
  2. Native self-check (xrk-native): shift-0 replay == converted CSV rows and == unaligned lap.
  3. Estimate per-lap shifts, build the aligned laps, re-measure the residual lags, and check
     that t/lat/lng/speed/dist and all other meta equal the shipped files, only the logger
     channels change and no edge value is held.
  4. Write candidates, the alignment config and align-report.json to --out. --apply also
     writes the lap JSONs and laps.json file sizes into public/data/races/fuji_aim_2020_07_30/.

After --apply, re-run the shipped registration so its dataSha256 values match:
  pipeline/.venv-sr/Scripts/python.exe pipeline/register_gps_to_track.py --track fuji
      --race fuji_aim_2020_07_30 --surface dry --vehicle-profile public/data/vehicles/mazda2-dj.json
then update artifacts/gps-accuracy-2026-09-22/verification-manifest.json, and regenerate
kerb_contacts.json / apex_kpi.json (scripts/quality/apex_kpi/export_inputs.py --write-public,
build_apex_kpi.py) and gps_registration_kerb.json (pipeline/register_gps_kerb_contact.py).

Note: scripts/quality/rebuild-fuji-gps-preprocessing.py --apply rebuilds the laps without
channel alignment; run this script with --apply again afterwards.

Local source media are passed by argument or environment (never written into outputs; only
their SHA-256 is recorded and checked against the lap provenance):
  python scripts/quality/align-fuji-channel-timing.py --dry-xrk <7/30 .xrk> --wet-xrk <7/29 .xrk> [--apply]
  (or FUJI_DRY_XRK / FUJI_WET_XRK)
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import sys
from pathlib import Path, PureWindowsPath

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import build_race as br  # noqa: E402

RACE = "fuji_aim_2020_07_30"
REFERENCE_RACE = "fuji_aim_01"
DEFAULT_CSV = Path("pipeline/cache/osaki_hmr_demio_101_fuji_generic_testing_a_1741_converted.csv")
DEFAULT_OUT = Path("artifacts/improvement-eval-2026-10-02/after/channel-timing-iter2")
DEFAULT_BASELINE = Path("artifacts/improvement-eval-2026-10-02/baseline/public/data/races") / RACE
GPS_KEYS = ("t", "lat", "lng", "speed", "dist")
ALIGNED = tuple(br.ALIGNABLE_CHANNELS)
RESIDUAL_LIMIT_S = 0.05        # imu-v1: residual accx/accy IMU-vs-GPS lag
PAIR_MEDIAN_LIMIT_S = 0.01     # ref-0729: median over pairs of (lag - 7/29 median of the pair) after re-timing
PAIR_RESIDUAL_LIMIT_S = 0.10   # ref-0729: every pair; the pairs of one lap disagree by 0.04-0.10 s (estimator spread)
G = 9.80665
NATIVE_METHOD = "xrk_native_resample_v1"
NATIVE_SAMPLER = "xrk-native-v1"
CONVERTER_HZ = 10.0            # pipeline/convert_aim.py --hz default used for the shipped CSVs
TOOL = "scripts/quality/align-fuji-channel-timing.py"
LAG_DEFINITION = ("lag = s maximising corr(GPS-derived(t), channel(t + s)); cubic splines on a 10 ms grid, "
                  "2 ms scan; negative = channel features earlier than GPS")
ESTIMATOR_V1 = ("s maximising corr(GPS-derived(t), IMU(t+s)); cubic splines on a 10 ms grid, s in [-1.0, 0.5) "
                "step 2 ms; GPS along/lateral acceleration from the shipped smoothed lat/lng")
PAIRS = ("accxVsGpsAlong", "accyVsGpsLateral", "brakeVsGpsDecel", "steerVsGpsCourseRate", "yawGyroVsGpsCourseRate")
ESTIMATOR_V2 = ("shift_lap = -median over pairs (" + ", ".join(PAIRS) + ") of (lag_7/30,lap - median lag of the same "
                "pair over the 7/29 laps), on the unshifted data. " + LAG_DEFINITION + ". GPS along/lateral acceleration "
                "and course rate from the shipped smoothed lat/lng, deceleration from -d(GPS speed)/dt; yaw gyro = raw "
                "XRK YawRate (50 Hz) at XRK timecode tc0 + 1000 t, sign fitted to the course rate.")
SHIFT_LOGGER_KEYS = tuple(key for channel in ALIGNED for key in br.ALIGNABLE_CHANNELS[channel])


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_module("audit_fuji_channel_timing", ROOT / "scripts/quality/audit-fuji-channel-timing.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def payload(lap: dict) -> bytes:
    return (json.dumps(lap, separators=(",", ":")) + "\n").encode()


def corr(a, b) -> float:
    return float(np.corrcoef(a, b)[0, 1])


# --------------------------------------------------------------------------- native XRK replay

def convert_aim_module():
    """pipeline/convert_aim.py (its module import needs libxrk and pyarrow)."""
    try:
        import convert_aim
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"--sampler xrk-native / --estimate ref-0729 need libxrk and pyarrow ({exc})")
    return convert_aim


def converter_keys() -> dict[str, str]:
    """build_race source key -> CSV telemetry_name (inverse of build_race.CHANNEL_ALIASES)."""
    inverse: dict[str, str] = {}
    for telemetry_name, key in br.CHANNEL_ALIASES.items():
        if key in inverse:
            raise SystemExit(f"CHANNEL_ALIASES maps two telemetry names onto {key}")
        inverse[key] = telemetry_name
    return inverse


def convert_grid(resample, grid: np.ndarray, channel_map, ca, step_ms: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """pipeline/convert_aim.py main() on ``grid`` (uniform, ``step_ms`` apart).

    ``resample(grid)`` returns {XRK channel: values on grid} like libxrk's
    resample_to_timecodes (convert_aim resamples every CHANNEL_MAP source plus
    GPS_Satellites). Then, exactly as convert_aim: GPS-fix guard (drop leading and
    trailing samples with lat == lon == 0 or no satellites), non-finite repair per
    source channel on the grid (NONFINITE_LONG_GAP_FILL policy), unit transform and CSV
    rounding (ndig > 0: round, else int). Values are returned as build_race parses the
    CSV text (float). Returns (kept grid timecodes, {telemetry_name: values}).
    """
    cols = resample(grid)
    lat, lon = np.asarray(cols["GPS Latitude"]), np.asarray(cols["GPS Longitude"])
    valid = ~((lat == 0) & (lon == 0))
    if "GPS_Satellites" in cols:
        valid &= np.asarray(cols["GPS_Satellites"]) > 0
    idx = np.flatnonzero(valid)
    if len(idx) == 0:
        raise SystemExit("native replay: no valid GPS fix on the grid")
    first, last = int(idx[0]), int(idx[-1])
    repaired = {}
    for src in sorted({row[0] for row in channel_map}):
        values, _ = ca.repair_non_finite(np.asarray(cols[src])[first:last + 1], step_ms,
                                         ca.NONFINITE_LONG_GAP_FILL.get(src, "ffill"))
        if values is None:
            raise SystemExit(f"native replay: channel {src} is entirely non-finite")
        repaired[src] = values
    out = {}
    for src, telemetry_name, transform, ndig in channel_map:
        vals = []
        for raw in repaired[src]:
            value = transform(float(raw))
            vals.append(float(round(value, ndig)) if ndig > 0 else float(int(value)))
        out[telemetry_name] = np.asarray(vals, dtype=float)
    return np.asarray(grid[first:last + 1], dtype=np.int64), out


class ConvertedGrid:
    """Converted values on one (delayed) grid with timecode lookup."""

    def __init__(self, grid: np.ndarray, values: dict[str, np.ndarray], step_ms: int):
        self.grid, self.values, self.step = grid, values, step_ms

    def index(self, timecode: int) -> int | None:
        j, rem = divmod(int(timecode) - int(self.grid[0]), self.step)
        return j if rem == 0 and 0 <= j < len(self.grid) else None


class XrkSession:
    """One AiM XRK: convert_aim replay on delayed grids and raw native series."""

    def __init__(self, path: Path):
        ca = convert_aim_module()
        import pyarrow as pa
        from libxrk import aim_xrk

        self.ca, self._pa = ca, pa
        self.sha256 = file_sha(path)
        self.log = aim_xrk(str(path))
        self.anchor = ca.derive_anchor_datetime(self.log.metadata)
        sources = sorted({row[0] for row in ca.CHANNEL_MAP})
        missing = [c for c in sources if c not in self.log.channels]
        self.channel_map = [row for row in ca.CHANNEL_MAP if row[0] not in missing]
        names = sorted(set(sources) - set(missing))
        if "GPS_Satellites" in self.log.channels:
            names = sorted(set(names) | {"GPS_Satellites"})
        self.names = names
        lo = max(int(self.log.channels[c].column("timecodes").to_numpy(zero_copy_only=False).min()) for c in names)
        hi = min(int(self.log.channels[c].column("timecodes").to_numpy(zero_copy_only=False).max()) for c in names)
        lo = min(lo, 0)
        self.step = round(1000.0 / CONVERTER_HZ)
        self.base_grid = np.arange(lo, hi + 1, self.step, dtype=np.int64)
        self._cache: dict[int, ConvertedGrid] = {}

    def resample(self, grid: np.ndarray) -> dict[str, np.ndarray]:
        pa = self._pa
        df = self.log.resample_to_timecodes(pa.array(grid, type=pa.int64()), channel_names=self.names) \
            .get_channels_as_table().to_pandas()
        if not np.array_equal(df["timecodes"].to_numpy(), grid):
            raise SystemExit("native replay: libxrk changed the target timecodes")
        return {name: df[name].to_numpy() for name in self.names}

    def converted(self, shift_ms: int) -> ConvertedGrid:
        """convert_aim output on its grid delayed by shift_ms (value at grid point g = logger at g - shift_ms)."""
        if shift_ms not in self._cache:
            kept, values = convert_grid(self.resample, self.base_grid - shift_ms, self.channel_map, self.ca, self.step)
            self._cache[shift_ms] = ConvertedGrid(kept, values, self.step)
        return self._cache[shift_ms]

    def timecode(self, ts: str) -> int:
        ms = (br.parse_iso(ts) - self.anchor).total_seconds() * 1000.0
        if abs(ms - round(ms)) > 1e-6:
            raise SystemExit(f"timestamp {ts} is not on the XRK millisecond clock")
        return int(round(ms))

    def native(self, channel: str) -> tuple[np.ndarray, np.ndarray]:
        table = self.log.channels[channel]
        return (table.column("timecodes").to_numpy(zero_copy_only=False).astype(np.int64),
                table.column(channel).to_numpy(zero_copy_only=False).astype(float))


def shift_ms_of(shift: float) -> int:
    ms = shift * 1000.0
    if abs(ms - round(ms)) > 1e-6:
        raise SystemExit(f"native re-timing needs a whole-millisecond shift: {shift}")
    return int(round(ms))


def native_rows(rows: dict[str, dict[str, float]], converted: ConvertedGrid, timecode, shift_ms: int,
                keys: dict[str, str], logger_keys=SHIFT_LOGGER_KEYS) -> tuple[dict[str, dict[str, float]], int]:
    """CSV rows of one lap with the logger channels replaced by the delayed native replay.

    Row at timestamp ts (XRK timecode tc) gets channel = converted value at tc - shift_ms.
    Returns (rows, held) where held counts rows whose source time is outside the replay.
    """
    out, held = {}, 0
    for ts, channels in rows.items():
        j = converted.index(timecode(ts) - shift_ms)
        new = dict(channels)
        if j is None:
            held += 1
        else:
            for key in logger_keys:
                if key in new:
                    new[key] = float(converted.values[keys[key]][j])
        out[ts] = new
    return out, held


def replay_matches_rows(rows: dict[str, dict[str, float]], converted: ConvertedGrid, timecode,
                        keys: dict[str, str]) -> dict[str, float]:
    """Max |replay - CSV row| per build_race source key (shift 0 self-check of the converter replay)."""
    diff: dict[str, float] = {}
    for ts, channels in rows.items():
        j = converted.index(timecode(ts))
        if j is None:
            raise SystemExit(f"native self-check: CSV row {ts} is outside the replayed grid")
        for key, value in channels.items():
            name = keys.get(key)
            if name is None or name not in converted.values:
                continue
            diff[key] = max(diff.get(key, 0.0), abs(float(converted.values[name][j]) - value))
    return diff


def lap_start_timecode(lap: dict, xrk: XrkSession) -> int:
    """XRK timecode of lap t = 0 (the shipped t maps to tc0 + 1000 t)."""
    t = lap["t"]
    first = xrk.timecode(lap["meta"]["first_sample_time"])
    return first + int(round(1000 * t[0]))


def check_lap_mapping(lap: dict, tc0: int, xrk: XrkSession) -> float:
    """Max |shipped speed - replayed GPS speed| at tc0 + 1000 t (must be 0: same converter, same rounding)."""
    conv = xrk.converted(0)
    diffs = []
    for t, v in zip(lap["t"], lap["speed"]):
        j = conv.index(tc0 + int(round(1000 * t)))
        if j is None:
            raise SystemExit("lap mapping: sample outside the XRK grid")
        diffs.append(abs(round(conv.values["speed"][j], 3) - v))
    return float(max(diffs))


# --------------------------------------------------------------------------- lag estimation

def best_shift_series(t, a, tb, b, lo=-1.0, hi=0.5, step=0.002):
    """Like audit.best_shift, but b is a native series (times tb, linear interpolation)."""
    from scipy.interpolate import CubicSpline

    tt = np.arange(t[0] + 2, t[-1] - 2, 0.01)
    A = CubicSpline(t, a)(tt)
    best = (float("nan"), -2.0)
    for s in np.arange(lo, hi, step):
        r = corr(A, np.interp(tt + s, tb, b))
        if r > best[1]:
            best = (float(s), r)
    return best


def pair_lags(lap: dict, origin: dict, gyro: tuple[np.ndarray, np.ndarray] | None) -> dict:
    """Lags of the five channel pairs (ref-0729 estimator). gyro = (lap seconds, deg/s) or None."""
    t, _, along, lateral, course_rate, _ = audit.gps_motion(lap, origin)
    accx, accy = np.asarray(lap["accx"]) * G, np.asarray(lap["accy"]) * G
    if corr(lateral, accy) < 0:
        lateral = -lateral
    out = {}
    s, r = audit.best_shift(t, along, accx)
    out["accxVsGpsAlong"] = {"lagS": round(s, 3), "r": round(r, 3)}
    s, r = audit.best_shift(t, lateral, accy)
    out["accyVsGpsLateral"] = {"lagS": round(s, 3), "r": round(r, 3)}
    decel = -np.gradient(np.asarray(lap["speed"], float) / 3.6, t)
    s, r = audit.best_shift(t, decel, np.asarray(lap["brake"], float))
    out["brakeVsGpsDecel"] = {"lagS": round(s, 3), "r": round(r, 3)}
    steer = np.asarray(lap["steer"], float)
    rate = course_rate if corr(course_rate, steer) >= 0 else -course_rate
    s, r = audit.best_shift(t, rate, steer, lo=-1.5, hi=1.0)
    out["steerVsGpsCourseRate"] = {"lagS": round(s, 3), "r": round(r, 3)}
    if gyro is not None:
        tg, wg = gyro
        rate_deg = np.degrees(course_rate)
        sign = 1.0 if corr(rate_deg, np.interp(t, tg, wg)) >= 0 else -1.0
        s, r = best_shift_series(t, rate_deg, tg, sign * wg)
        scale = float(np.polyfit(np.interp(t[20:-20] + s, tg, sign * wg), rate_deg[20:-20], 1)[0])
        out["yawGyroVsGpsCourseRate"] = {"lagS": round(s, 3), "r": round(r, 3), "gyroSign": int(sign),
                                         "gpsPerGyroScale": round(scale, 3)}
    return out


def gyro_for_lap(xrk: XrkSession, tc0: int, shift: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Raw YawRate on lap seconds, optionally delayed like a re-timed channel (t = logger t + shift)."""
    tc, w = xrk.native("YawRate")
    ok = np.isfinite(w)
    return (tc[ok] - tc0) / 1000.0 + shift, w[ok]


def reference_medians(ref_lags: dict[int, dict]) -> dict[str, float]:
    return {p: round(statistics.median(lags[p]["lagS"] for lags in ref_lags.values()), 3) for p in PAIRS}


def ref0729_shift(lags: dict, ref: dict[str, float]) -> dict:
    """Per-lap estimate: shift = -median_p(lag_p - ref_p), rounded to 1 ms."""
    pairs = {}
    for p in PAIRS:
        pairs[p] = {**lags[p], "ref0729MedianS": ref[p], "deltaS": round(lags[p]["lagS"] - ref[p], 3)}
    deltas = [pairs[p]["deltaS"] for p in PAIRS]
    median_delta = round(statistics.median(deltas), 3)
    return {"shiftSeconds": round(-median_delta, 3), "medianDeltaS": median_delta,
            "deltaRangeS": [min(deltas), max(deltas)], "pairs": pairs}


def residuals(lags: dict, ref: dict[str, float]) -> dict:
    res = {p: round(lags[p]["lagS"] - ref[p], 3) for p in PAIRS if p in lags}
    vals = list(res.values())
    return {"byPair": res, "medianS": round(statistics.median(vals), 3), "maxAbsS": round(max(map(abs, vals)), 3)}


def imu_lags_v1(lap: dict, origin: dict) -> dict:
    """Iteration-1 estimator: IMU-vs-GPS lags (used for the shift) and ECU steer vs course rate (diagnostic)."""
    t, _, along, lateral, course_rate, _ = audit.gps_motion(lap, origin)
    accx, accy = np.asarray(lap["accx"]) * G, np.asarray(lap["accy"]) * G
    if corr(lateral, accy) < 0:
        lateral = -lateral
    sx, rx = audit.best_shift(t, along, accx)
    sy, ry = audit.best_shift(t, lateral, accy)
    steer = np.asarray(lap["steer"], float)
    if corr(course_rate, steer) < 0:
        course_rate = -course_rate
    ss, rs = audit.best_shift(t, course_rate, steer, lo=-1.5, hi=1.0)
    return {"imuAccxLagS": round(sx, 3), "rAccx": round(rx, 3), "imuAccyLagS": round(sy, 3), "rAccy": round(ry, 3),
            "steerVsGpsCourseRateLagS": round(ss, 3), "rSteer": round(rs, 3)}


def plan_v1(mode: str, estimates: dict[int, dict]) -> dict[int, dict]:
    session = [x for e in estimates.values() for x in (e["imuAccxLagS"], e["imuAccyLagS"])]
    session_median = round(statistics.median(session), 3)
    out = {}
    for lap, e in estimates.items():
        lap_lag = round((e["imuAccxLagS"] + e["imuAccyLagS"]) / 2, 3)
        lag = lap_lag if mode == "per-lap" else session_median
        out[lap] = {"shiftSeconds": round(-lag, 3), "estimate": {
            "method": "imu-vs-gps-xcorr-v1",
            "basis": "per-lap mean of accx and accy lags" if mode == "per-lap" else "session median of accx/accy lags",
            "lagS": lag, "imuAccxLagS": e["imuAccxLagS"], "rAccx": e["rAccx"], "imuAccyLagS": e["imuAccyLagS"],
            "rAccy": e["rAccy"], "sessionMedianLagS": session_median, "tool": TOOL}}
    return out


def plan_v2(mode: str, per_lap: dict[int, dict], ref: dict[str, float]) -> dict[int, dict]:
    session_shift = round(statistics.median(e["shiftSeconds"] for e in per_lap.values()), 3)
    out = {}
    for lap, e in per_lap.items():
        shift = e["shiftSeconds"] if mode == "per-lap" else session_shift
        out[lap] = {"shiftSeconds": shift, "estimate": {
            "method": "ref-0729-multipair-v2",
            "basis": ("per-lap median over channel pairs of the lag relative to the 7/29 median of the same pair"
                      if mode == "per-lap" else "session median of the per-lap multi-pair estimates"),
            "referenceRace": REFERENCE_RACE,
            "lagDefinition": LAG_DEFINITION,
            "lapShiftSeconds": e["shiftSeconds"], "sessionMedianShiftSeconds": session_shift,
            "medianDeltaS": e["medianDeltaS"], "deltaRangeS": e["deltaRangeS"],
            "pairs": e["pairs"], "tool": TOOL}}
    return out


def peaks(lap: dict) -> dict:
    ax, ay = np.abs(np.asarray(lap["accx"], float)), np.abs(np.asarray(lap["accy"], float))
    return {"maxAbsAccxG": round(float(ax.max()), 4), "maxAbsAccyG": round(float(ay.max()), 4),
            "rmsAccxG": round(float(np.sqrt(np.mean(ax ** 2))), 4), "rmsAccyG": round(float(np.sqrt(np.mean(ay ** 2))), 4),
            "p95AbsAccyG": round(float(np.percentile(ay, 95)), 4)}


def native_peaks(xrk: XrkSession, tc0: int, t_end: float, shift: float) -> dict:
    """Max |InlineAcc|, |LateralAcc| of the raw 50 Hz IMU over the lap window (delayed by shift)."""
    out = {}
    lo, hi = tc0 - 1000 * shift, tc0 + 1000 * (t_end - shift)
    for name, key in (("InlineAcc", "maxAbsAccxG"), ("LateralAcc", "maxAbsAccyG")):
        tc, v = xrk.native(name)
        m = (tc >= lo) & (tc <= hi) & np.isfinite(v)
        out[key] = round(float(np.abs(v[m]).max()), 4)
    return out


# --------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="converted 7/30 telemetry CSV (+ _laps.csv beside it)")
    parser.add_argument("--dry-xrk", type=Path, default=None, help="7/30 AiM XRK (default: $FUJI_DRY_XRK)")
    parser.add_argument("--wet-xrk", type=Path, default=None, help="7/29 AiM XRK, reference yaw gyro (default: $FUJI_WET_XRK)")
    parser.add_argument("--sampler", choices=("xrk-native", "csv-linear"), default="xrk-native")
    parser.add_argument("--estimate", choices=("ref-0729", "imu-v1"), default="ref-0729")
    parser.add_argument("--mode", choices=("per-lap", "session-median"), default="per-lap")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="candidates, alignment config and report")
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE,
                        help="snapshot of the unshifted 7/30 laps; the unaligned rebuild is compared to it when present")
    parser.add_argument("--apply", action="store_true", help="write the aligned laps into public/data/races")
    args = parser.parse_args()

    dry_path = args.dry_xrk or (Path(os.environ["FUJI_DRY_XRK"]) if os.environ.get("FUJI_DRY_XRK") else None)
    wet_path = args.wet_xrk or (Path(os.environ["FUJI_WET_XRK"]) if os.environ.get("FUJI_WET_XRK") else None)
    need_dry = args.sampler == "xrk-native" or args.estimate == "ref-0729"
    if need_dry and (dry_path is None or not dry_path.is_file()):
        raise SystemExit("--dry-xrk (or FUJI_DRY_XRK) must name the 7/30 XRK; "
                         "iteration 1 without XRK: --sampler csv-linear --estimate imu-v1")
    if args.estimate == "ref-0729" and (wet_path is None or not wet_path.is_file()):
        raise SystemExit("--estimate ref-0729 needs --wet-xrk (or FUJI_WET_XRK): the 7/29 yaw gyro reference")

    csv_path = args.csv if args.csv.is_absolute() else ROOT / args.csv
    lap_csv = csv_path.with_name(csv_path.stem + "_laps.csv")
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    race_dir = ROOT / "public/data/races" / RACE
    ref_dir = ROOT / "public/data/races" / REFERENCE_RACE
    origin = json.loads((ROOT / "public/data/tracks/fuji/track.json").read_text(encoding="utf-8"))["origin"]
    source_hashes = {csv_path: sha(csv_path.read_bytes()), lap_csv: sha(lap_csv.read_bytes())}

    index_bytes = (race_dir / "laps.json").read_bytes()
    index = json.loads(index_bytes)
    assert (json.dumps(index, indent=2) + "\n").encode() == index_bytes, "laps.json would be reformatted"
    targets = [(r["vehicle_id"], r["lap"]) for r in index["selected"]]
    shipped_bytes = {lap: (race_dir / f"{br.vehicle_slug(v)}_lap_{lap:03d}.json").read_bytes() for v, lap in targets}
    shipped = {lap: json.loads(b) for lap, b in shipped_bytes.items()}
    for lap, old in shipped.items():
        assert old["meta"]["gps_processing"]["source_csv_sha256"] == source_hashes[csv_path], f"L{lap}: CSV differs from shipped provenance"

    dry = wet = None
    if need_dry or (dry_path is not None and dry_path.is_file()):  # also verifies an XRK-native shipped state
        dry = XrkSession(dry_path)
        for lap, old in shipped.items():
            assert old["meta"]["gps_processing"]["source_xrk_sha256"] == dry.sha256, f"L{lap}: 7/30 XRK differs from lap provenance"
    keys = converter_keys()

    telemetry = br.stream_telemetry(csv_path, targets, "timestamp", br.alignment_context_channels({"channels": list(ALIGNED)}))
    records = {(r["vehicle_id"], r["lap"]): r for r in br.read_lap_index(lap_csv)}

    def build_args(lap: int) -> argparse.Namespace:
        return argparse.Namespace(
            track_id="fuji", race_id=RACE, lap_times=lap_csv, time_column="timestamp", smooth_window=5, max_gps_speed_mps=90,
            # keep the provenance string exactly as first published (Windows separators)
            telemetry=PureWindowsPath(shipped[lap]["meta"]["source_file"]))

    def context(vehicle: str, lap: int) -> dict:
        return {ts: ch for n in (lap - 1, lap + 1) for ts, ch in telemetry.get((vehicle, n), {}).items()}

    def finish(new: dict, lap: int) -> dict:
        new["meta"]["gps_processing"].update({k: shipped[lap]["meta"]["gps_processing"][k]
                                              for k in ("source_csv_sha256", "source_xrk_sha256")})
        return new

    def build_csv(vehicle: str, lap: int, alignment: dict | None) -> dict:
        new = br.compact_from_timeseries(vehicle, lap, telemetry[(vehicle, lap)], records[(vehicle, lap)], build_args(lap),
                                         context(vehicle, lap), br.lap_channel_alignment(alignment, vehicle, lap))
        return finish(new, lap)

    tc0s: dict[int, int] = {}

    def build_native(vehicle: str, lap: int, shift: float, estimate: dict | None, channels=ALIGNED) -> dict:
        shift_ms = shift_ms_of(shift)
        rows = telemetry[(vehicle, lap)]
        logger_keys = tuple(k for c in channels for k in br.ALIGNABLE_CHANNELS[c])
        new_rows, held = native_rows(rows, dry.converted(shift_ms), dry.timecode, shift_ms, keys, logger_keys)
        if held:
            raise SystemExit(f"L{lap}: {held} rows fall outside the native replay (shift {shift} s)")
        new = finish(br.compact_from_timeseries(vehicle, lap, new_rows, records[(vehicle, lap)], build_args(lap),
                                                context(vehicle, lap), None), lap)
        span_end = max((br.parse_iso(ts) for ts in rows), default=None)
        start = br.parse_iso(new["meta"]["first_sample_time"])
        lap_span = (min((br.parse_iso(ts) - start).total_seconds() for ts in rows), (span_end - start).total_seconds())
        neighbour = sum(not lap_span[0] <= t - shift <= lap_span[1] for t in new["t"])
        new["meta"]["channel_time_alignment"] = {
            "method": NATIVE_METHOD,
            "definition": (
                "channel(t) = logger channel at (t - shiftSeconds); positive shift = logger channel delayed. Values are "
                "re-sampled from the native XRK series (IMU 50 Hz, ECU 20 Hz) by replaying pipeline/convert_aim.py on its "
                "10 Hz grid delayed by shiftSeconds (libxrk resample_to_timecodes: linear for interpolated channels, "
                "previous sample for gear; GPS-fix guard; non-finite repair; units and CSV rounding), then "
                "pipeline/build_race.py rounding (brake = max(front, rear)). Continuous session series, so lap "
                "boundaries read the neighbouring laps. GPS t/lat/lng/speed/dist unchanged."),
            "shiftSeconds": shift,
            "estimate": estimate,
            "channels": list(channels),
            "previousSampleChannels": [c for c in channels if c == "gear"],
            "source": {"kind": "aim-xrk", "sha256": dry.sha256, "replay": "pipeline/convert_aim.py",
                       "lapStartTimecodeMs": tc0s[lap]},
            "samplesFromNeighbourLaps": neighbour,
            "heldEdgeSamples": 0,
        }
        return new

    def rebuild_from_record(vehicle: str, lap: int, record: dict) -> dict:
        if record.get("method") == NATIVE_METHOD:
            if dry is None:
                raise SystemExit(f"L{lap}: shipped lap was re-timed from the XRK; pass --dry-xrk to verify it")
            return build_native(vehicle, lap, record["shiftSeconds"], record.get("estimate"), tuple(record["channels"]))
        if record.get("method") == br.CHANNEL_ALIGNMENT_METHOD:
            return build_csv(vehicle, lap, {"channels": record["channels"],
                                            "laps": {str(lap): {"shiftSeconds": record["shiftSeconds"],
                                                                "estimate": record.get("estimate")}}})
        raise SystemExit(f"L{lap}: unknown channel_time_alignment method {record.get('method')!r}")

    # ---- 1. unaligned rebuild, native self-check, shipped state
    unaligned, self_check, state_by_lap, baseline_check = {}, {}, {}, {}
    for vehicle, lap in targets:
        unaligned[lap] = build_csv(vehicle, lap, None)
        zero = build_csv(vehicle, lap, {"channels": list(ALIGNED), "shiftSeconds": 0.0})
        for key in br.COMPACT_KEYS:
            assert zero[key] == unaligned[lap][key], f"L{lap}: zero shift changed {key}"
        if dry is not None:
            tc0s[lap] = lap_start_timecode(unaligned[lap], dry)
            speed_err = check_lap_mapping(unaligned[lap], tc0s[lap], dry)
            assert speed_err == 0.0, f"L{lap}: lap t does not map onto XRK tc0 + 1000 t (speed error {speed_err})"
            row_diff = replay_matches_rows(telemetry[(vehicle, lap)], dry.converted(0), dry.timecode, keys)
            native_zero = build_native(vehicle, lap, 0.0, None)
            channel_diff = {key: float(np.max(np.abs(np.asarray(native_zero[key], float) - np.asarray(unaligned[lap][key], float))))
                            for key in ALIGNED}
            native_zero["meta"].pop("channel_time_alignment")
            identical = payload(native_zero) == payload(unaligned[lap])
            self_check[lap] = {"lapStartTimecodeMs": tc0s[lap], "speedMappingMaxAbsErrKmh": speed_err,
                               "replayVsCsvRowsMaxAbsDiff": {k: row_diff[k] for k in sorted(row_diff)},
                               "shift0VsUnalignedMaxAbsDiff": channel_diff, "shift0PayloadIdentical": identical}
            assert identical and max(row_diff.values()) == 0.0, f"L{lap}: native shift-0 replay differs: {self_check[lap]}"
        before = payload(unaligned[lap])
        baseline_dir = args.baseline_dir if args.baseline_dir.is_absolute() else ROOT / args.baseline_dir
        baseline_file = baseline_dir / f"{br.vehicle_slug(vehicle)}_lap_{lap:03d}.json"
        if baseline_file.is_file():
            base_lap = json.loads(baseline_file.read_bytes())
            baseline_check[lap] = {
                "identicalBytes": baseline_file.read_bytes() == before,
                "maxAbsDiff": {key: float(np.max(np.abs(np.asarray(base_lap[key], float) - np.asarray(unaligned[lap][key], float))))
                               for key in br.COMPACT_KEYS}}
            assert baseline_check[lap]["identicalBytes"], f"L{lap}: unaligned rebuild differs from the baseline snapshot"
        record = shipped[lap]["meta"].get("channel_time_alignment")
        if before == shipped_bytes[lap]:
            state_by_lap[lap] = "unaligned"
        elif record and payload(rebuild_from_record(vehicle, lap, record)) == shipped_bytes[lap]:
            state_by_lap[lap] = f"aligned:{record['method']}"
        else:
            raise SystemExit(f"L{lap}: shipped file matches neither the unaligned build nor its recorded alignment")
    states = sorted(set(state_by_lap.values()))
    assert len(states) == 1, f"shipped laps are in mixed alignment states: {state_by_lap}"
    state = states[0]
    print(f"shipped state: {state}; native self-check: {'pass' if self_check else 'n/a'}", flush=True)

    # ---- 2. estimate
    ref_info = None
    if args.estimate == "ref-0729":
        wet = XrkSession(wet_path)
        ref_index = json.loads((ref_dir / "laps.json").read_text(encoding="utf-8"))
        ref_lags, ref_map = {}, {}
        for r in ref_index["selected"]:
            ref_lap = json.loads((ref_dir / r["data_file"]).read_text(encoding="utf-8"))
            assert ref_lap["meta"]["gps_processing"]["source_xrk_sha256"] == wet.sha256, "7/29 XRK differs from lap provenance"
            assert "channel_time_alignment" not in ref_lap["meta"], "the 7/29 reference must be unshifted"
            tc0 = lap_start_timecode(ref_lap, wet)
            err = check_lap_mapping(ref_lap, tc0, wet)
            assert err == 0.0, f"7/29 L{r['lap']}: lap t does not map onto XRK tc0 + 1000 t (speed error {err})"
            ref_map[r["lap"]] = {"lapStartTimecodeMs": tc0, "speedMappingMaxAbsErrKmh": err}
            ref_lags[r["lap"]] = pair_lags(ref_lap, origin, gyro_for_lap(wet, tc0))
            print(f"7/29 L{r['lap']}: {ref_lags[r['lap']]}", flush=True)
        ref = reference_medians(ref_lags)
        ref_info = {"race": REFERENCE_RACE, "xrkSha256": wet.sha256, "medianLagS": ref, "lapMapping": ref_map,
                    "laps": {str(k): v for k, v in ref_lags.items()}}
        before_lags = {lap: pair_lags(unaligned[lap], origin, gyro_for_lap(dry, tc0s[lap])) for _, lap in targets}
        per_lap = {lap: ref0729_shift(before_lags[lap], ref) for lap in before_lags}
        plans = {mode: plan_v2(mode, per_lap, ref) for mode in ("per-lap", "session-median")}
        for lap in before_lags:
            print(f"L{lap} before: {per_lap[lap]}", flush=True)
    else:
        before_lags = {lap: imu_lags_v1(unaligned[lap], origin) for _, lap in targets}
        plans = {mode: plan_v1(mode, before_lags) for mode in ("per-lap", "session-median")}
    plan = plans[args.mode]
    sampler_id = NATIVE_SAMPLER if args.sampler == "xrk-native" else "csv-linear-v1"
    config = {"race_id": RACE, "channels": list(ALIGNED), "sampler": sampler_id,
              "estimator": ESTIMATOR_V2 if args.estimate == "ref-0729" else ESTIMATOR_V1, "mode": args.mode,
              "laps": {str(lap): p for lap, p in plan.items()}}

    # ---- 3. aligned build and residuals
    aligned = {}
    for vehicle, lap in targets:
        if args.sampler == "xrk-native":
            aligned[lap] = build_native(vehicle, lap, plan[lap]["shiftSeconds"], plan[lap]["estimate"])
        else:
            aligned[lap] = build_csv(vehicle, lap, config)

    def lags_of(data: dict[int, dict], shift_of) -> dict[int, dict]:
        if args.estimate == "ref-0729":
            return {lap: pair_lags(d, origin, gyro_for_lap(dry, tc0s[lap], shift_of(lap))) for lap, d in data.items()}
        return {lap: imu_lags_v1(d, origin) for lap, d in data.items()}

    after_lags = lags_of(aligned, lambda lap: plan[lap]["shiftSeconds"])
    previous_lags = None
    if state != "unaligned" and any(payload(aligned[lap]) != shipped_bytes[lap] for lap in aligned):
        previous_lags = lags_of(shipped, lambda lap: shipped[lap]["meta"]["channel_time_alignment"]["shiftSeconds"])

    laps_report, candidates = [], []
    for vehicle, lap in targets:
        old, before_build, new = shipped[lap], unaligned[lap], aligned[lap]
        new_payload = payload(new)
        for key in GPS_KEYS:
            assert new[key] == old[key] == before_build[key], f"L{lap}: GPS array {key} changed"
        assert set(new) == set(old) and len(new["t"]) == len(old["t"])
        meta_new = {k: v for k, v in new["meta"].items() if k != "channel_time_alignment"}
        meta_old = {k: v for k, v in old["meta"].items() if k != "channel_time_alignment"}
        assert meta_new == meta_old, f"L{lap}: meta outside channel_time_alignment changed"
        record = new["meta"]["channel_time_alignment"]
        assert record["heldEdgeSamples"] == 0, f"L{lap}: alignment ran past the session data"
        changed = {key: sum(a != b for a, b in zip(new[key], before_build[key])) for key in ALIGNED}
        if args.estimate == "ref-0729":
            res = residuals(after_lags[lap], ref)
            assert abs(res["medianS"]) <= PAIR_MEDIAN_LIMIT_S, f"L{lap}: residual median vs 7/29 above {PAIR_MEDIAN_LIMIT_S}s: {res}"
            assert res["maxAbsS"] <= PAIR_RESIDUAL_LIMIT_S, f"L{lap}: a pair residual vs 7/29 is above {PAIR_RESIDUAL_LIMIT_S}s: {res}"
        else:
            res = after_lags[lap]
            assert abs(res["imuAccxLagS"]) < RESIDUAL_LIMIT_S and abs(res["imuAccyLagS"]) < RESIDUAL_LIMIT_S, \
                f"L{lap}: residual IMU-vs-GPS lag above {RESIDUAL_LIMIT_S}s: {res}"
        filename = f"{br.vehicle_slug(vehicle)}_lap_{lap:03d}.json"
        candidates.append((filename, new_payload))
        for entry in index["selected"] + index["laps"]:
            if entry.get("data_file") == filename:
                entry["file_size"] = len(new_payload)
        entry = {
            "lap": lap, "dataFile": filename, "pointCount": len(new["t"]),
            "shiftSeconds": record["shiftSeconds"], "samplesFromNeighbourLaps": record["samplesFromNeighbourLaps"],
            "unalignedSha256": sha(payload(before_build)), "alignedSha256": sha(new_payload),
            "shippedSha256Before": sha(shipped_bytes[lap]),
            "gpsArraysIdentical": True, "otherMetaIdentical": True, "changedSamples": changed,
            "lagBefore": before_lags[lap], "lagAfter": after_lags[lap],
            "peaks": {"unaligned": peaks(before_build), "shippedBefore": peaks(old), "aligned": peaks(new)},
        }
        if args.estimate == "ref-0729":
            entry["residualVs0729"] = {"unaligned": residuals(before_lags[lap], ref), "aligned": res}
            if previous_lags is not None:
                entry["lagShippedBefore"] = previous_lags[lap]
                entry["residualVs0729"]["shippedBefore"] = residuals(previous_lags[lap], ref)
        if dry is not None:
            entry["peaks"]["native50HzLapWindow"] = native_peaks(dry, tc0s[lap], new["t"][-1], record["shiftSeconds"])
        laps_report.append(entry)

    def session_summary(table: dict[int, dict]) -> dict:
        if args.estimate == "ref-0729":
            return {p: {"medianLagS": round(statistics.median(e[p]["lagS"] for e in table.values()), 3),
                        "medianVs0729S": round(statistics.median(e[p]["lagS"] for e in table.values()) - ref[p], 3)}
                    for p in PAIRS}
        x = [e["imuAccxLagS"] for e in table.values()]
        y = [e["imuAccyLagS"] for e in table.values()]
        s = [e["steerVsGpsCourseRateLagS"] for e in table.values()]
        both = x + y
        return {"imuLagMedianS": round(statistics.median(both), 3), "imuLagMaxAbsS": round(max(map(abs, both)), 3),
                "imuLagRangeS": [min(both), max(both)], "steerVsGpsCourseRateMedianS": round(statistics.median(s), 3)}

    index_payload = (json.dumps(index, indent=2) + "\n").encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates").mkdir(exist_ok=True)
    for filename, data in candidates:
        (out_dir / "candidates" / filename).write_bytes(data)
    (out_dir / "candidates" / "laps.json").write_bytes(index_payload)
    (out_dir / "alignment.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        for filename, data in candidates:
            (race_dir / filename).write_bytes(data)
        (race_dir / "laps.json").write_bytes(index_payload)
        for filename, data in candidates:
            assert (race_dir / filename).read_bytes() == data
    for path, digest in source_hashes.items():
        assert sha(path.read_bytes()) == digest, f"source modified: {path.name}"

    summary = {"before": session_summary(before_lags), "after": session_summary(after_lags),
               "shiftSecondsByLap": {m: {str(lap): p["shiftSeconds"] for lap, p in plans[m].items()} for m in plans}}
    if previous_lags is not None:
        summary["shippedBefore"] = session_summary(previous_lags)
        summary["shippedBeforeShiftSecondsByLap"] = {
            str(lap): shipped[lap]["meta"]["channel_time_alignment"]["shiftSeconds"] for lap in shipped}
    report = {
        "version": 2,
        "scope": "7/30 logger-clock channel re-timing onto the GPS time base (GPS-REG-08). GPS arrays unchanged.",
        "raceId": RACE,
        "sampler": sampler_id,
        "estimate": args.estimate,
        "mode": args.mode,
        "convention": "shiftSeconds > 0: channel(t) = logger(t - shiftSeconds). " + LAG_DEFINITION + ".",
        "estimator": config["estimator"],
        "channels": list(ALIGNED),
        "sourceCsv": DEFAULT_CSV.as_posix() if csv_path == ROOT / DEFAULT_CSV else csv_path.name,
        "sourceCsvSha256": source_hashes[csv_path],
        "sourceXrkSha256": dry.sha256 if dry else None,
        "reference": ref_info,
        "shippedStateBeforeRun": state,
        "applied": args.apply,
        "checks": {"gpsArraysIdentical": True, "otherMetaIdentical": True, "zeroShiftReproducesUnaligned": True,
                   "noHeldEdgeSamples": True, "sourceFilesUnchanged": True,
                   "nativeShift0ReproducesCsvAndUnalignedLap": bool(self_check) or None,
                   **({f"residualMedianVs0729Within{PAIR_MEDIAN_LIMIT_S}s": True,
                       f"everyPairResidualVs0729Within{PAIR_RESIDUAL_LIMIT_S}s": True} if args.estimate == "ref-0729"
                      else {f"residualImuLagBelow{RESIDUAL_LIMIT_S}s": True})},
        "nativeSelfCheck": {str(k): v for k, v in self_check.items()} or None,
        "unalignedVsBaselineSnapshot": ({"source": DEFAULT_BASELINE.as_posix() if args.baseline_dir == DEFAULT_BASELINE
                                         else args.baseline_dir.name, "laps": {str(k): v for k, v in baseline_check.items()}}
                                        if baseline_check else None),
        "summary": summary,
        "laps": laps_report,
    }
    (out_dir / "align-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sampler": sampler_id, "estimate": args.estimate, "mode": args.mode, "applied": args.apply,
                      "shippedStateBeforeRun": state, "summary": summary}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
