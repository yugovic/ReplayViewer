#!/usr/bin/env python3
"""Stream Barber telemetry CSV into compact lap JSON payloads.

The telemetry source is long-form and large. This script only keeps rows for
selected vehicle/lap pairs in memory, while the 1.5GB CSV is read sequentially.

Optional --channel-time-alignment / --channel-shift re-times logger-clock
channels (aps, brake, steer, gear, accx, accy) onto the GPS time base; GPS
t/lat/lng/speed/dist are never changed. See ALIGNABLE_CHANNELS.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TELEMETRY = PROJECT_ROOT / "Datasets" / "barber" / "R1_barber_telemetry_data.csv"
DEFAULT_LAP_TIMES = PROJECT_ROOT / "Datasets" / "barber" / "R1_barber_lap_time.csv"
DEFAULT_OUTPUT_DIR = V2_ROOT / "public" / "data" / "races" / "barber_r1"
EARTH_RADIUS_M = 6_378_137.0

CHANNEL_ALIASES = {
    "VBOX_Lat_Min": "lat",
    "VBOX_Long_Minutes": "lng",
    "speed": "speed",
    "aps": "aps",
    "pbrake_f": "brake_f",
    "pbrake_r": "brake_r",
    "Steering_Angle": "steer",
    "gear": "gear",
    "accx_can": "accx",
    "accy_can": "accy",
    "Laptrigger_lapdist_dls": "lapdist_raw",
    "nmot": "rpm",
}

COMPACT_KEYS = ("t", "lat", "lng", "speed", "aps", "brake", "steer", "gear", "accx", "accy", "dist")

# Optional channel time alignment (off unless --channel-time-alignment or
# --channel-shift is given). Some loggers stamp their own channels (ECU via CAN,
# internal IMU) on a clock that is offset from the GPS fixes. The lap timebase
# t, the lap boundaries and lat/lng/speed/dist all follow GPS time, so only the
# logger-clock channels below are re-timed:
#   aligned(t) = logger channel at (t - shiftSeconds)
# A positive shift delays the channel (it was recorded early). Values come from
# linear interpolation over the continuous session series of that channel,
# including neighbouring-lap rows at the lap boundaries; discrete channels use
# the nearest sample (ties resolve to the earlier one). Each re-timed lap records
# meta.channel_time_alignment. Keys are compact output channels; values are the
# source keys stream_telemetry() stores (brake = max(front, rear) as unaligned).
ALIGNABLE_CHANNELS: dict[str, tuple[str, ...]] = {
    "aps": ("aps",),
    "brake": ("brake_f", "brake_r"),
    "steer": ("steer",),
    "gear": ("gear",),
    "accx": ("accx",),
    "accy": ("accy",),
}
NEAREST_SAMPLE_CHANNELS = frozenset({"gear"})
CHANNEL_ALIGNMENT_METHOD = "logger_channel_delay_v1"
MAX_CHANNEL_SHIFT_SECONDS = 5.0
# A config may name the sampler that produced it. build_race only implements the
# linear CSV re-timing; configs written for the native XRK re-timing
# (scripts/quality/align-fuji-channel-timing.py --sampler xrk-native) are refused
# instead of being re-applied with different values.
CSV_LINEAR_SAMPLER = "csv-linear-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build compact replay data from long-form telemetry CSV")
    parser.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY, help="Input telemetry CSV")
    parser.add_argument("--lap-times", type=Path, default=DEFAULT_LAP_TIMES, help="Lap boundary CSV")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output race data directory")
    parser.add_argument("--race-id", default="barber_r1", help="Race/session id for laps.json")
    parser.add_argument("--track-id", default="barber", help="Track id for metadata")
    parser.add_argument("--vehicle-id", action="append", default=[], help="Vehicle id to extract; repeatable")
    parser.add_argument("--lap", action="append", type=int, default=[], help="Lap number to extract; repeatable")
    parser.add_argument("--lap-start", type=int, default=None, help="Inclusive lap range start")
    parser.add_argument("--lap-end", type=int, default=None, help="Inclusive lap range end")
    parser.add_argument("--pair", action="append", default=[], help="Explicit VEHICLE_ID:LAP pair; repeatable")
    parser.add_argument("--auto-best", type=int, default=2, help="Auto-select fastest unique vehicles from lap index")
    parser.add_argument(
        "--time-column",
        choices=["timestamp", "meta_time"],
        default="timestamp",
        help="Telemetry time column used for alignment",
    )
    parser.add_argument("--smooth-window", type=int, default=5, help="Odd GPS moving-average window")
    parser.add_argument("--max-gps-speed-mps", type=float, default=90.0, help="GPS outlier threshold")
    parser.add_argument(
        "--channel-time-alignment",
        type=Path,
        default=None,
        help="JSON with logger-channel delays per lap (see lap_channel_alignment); default: no re-timing",
    )
    parser.add_argument(
        "--channel-shift",
        type=float,
        default=None,
        help="Delay in seconds for the logger-clock channels of every selected lap "
        "(positive = channel was recorded early); overridden per lap by --channel-time-alignment",
    )
    return parser.parse_args()


def parse_iso(ts: str) -> datetime:
    if not ts:
        raise ValueError("empty timestamp")
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def format_lap_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}:{rem:06.3f}"


def vehicle_slug(vehicle_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", vehicle_id)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def read_lap_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Lap time CSV not found: {path}")

    by_vehicle: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("vehicle_id") or not row.get("timestamp") or not row.get("lap", "").isdigit():
                continue
            by_vehicle[row["vehicle_id"]].append(row)

    records: list[dict[str, Any]] = []
    for vehicle_id, rows in by_vehicle.items():
        rows.sort(key=lambda r: int(r["lap"]))
        for start, end in zip(rows, rows[1:]):
            try:
                lap = int(start["lap"])
                duration = (parse_iso(end["timestamp"]) - parse_iso(start["timestamp"])).total_seconds()
            except (KeyError, ValueError):
                continue
            if not 70.0 <= duration <= 240.0:
                continue
            records.append(
                {
                    "vehicle_id": vehicle_id,
                    "vehicle_number": start.get("vehicle_number", ""),
                    "lap": lap,
                    "lap_time_seconds": round(duration, 3),
                    "lap_time": format_lap_time(duration),
                    "start_time": start.get("timestamp", ""),
                    "end_time": end.get("timestamp", ""),
                    "outing": start.get("outing", ""),
                    "is_best_vehicle": False,
                    "is_overall_best": False,
                }
            )

    best_by_vehicle: dict[str, dict[str, Any]] = {}
    for rec in records:
        current = best_by_vehicle.get(rec["vehicle_id"])
        if current is None or rec["lap_time_seconds"] < current["lap_time_seconds"]:
            best_by_vehicle[rec["vehicle_id"]] = rec
    for rec in best_by_vehicle.values():
        rec["is_best_vehicle"] = True

    if records:
        min(records, key=lambda r: r["lap_time_seconds"])["is_overall_best"] = True

    records.sort(key=lambda r: (r["vehicle_id"], r["lap"]))
    return records


def parse_pair(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise SystemExit(f"--pair must be VEHICLE_ID:LAP, got {value!r}")
    vehicle_id, lap_s = value.rsplit(":", 1)
    try:
        return vehicle_id, int(lap_s)
    except ValueError as exc:
        raise SystemExit(f"Invalid lap in --pair {value!r}") from exc


def select_targets(args: argparse.Namespace, lap_records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    targets.extend(parse_pair(value) for value in args.pair)

    if args.vehicle_id:
        if args.lap:
            laps = args.lap
        elif args.lap_start is not None and args.lap_end is not None:
            laps = list(range(args.lap_start, args.lap_end + 1))
        else:
            by_vehicle_best = {
                rec["vehicle_id"]: rec
                for rec in lap_records
                if rec["is_best_vehicle"]
            }
            laps = []
            for vehicle_id in args.vehicle_id:
                best = by_vehicle_best.get(vehicle_id)
                if best:
                    targets.append((vehicle_id, int(best["lap"])))
            if not targets:
                raise SystemExit("No laps specified and no best-lap records matched --vehicle-id")
        for vehicle_id in args.vehicle_id:
            for lap in laps:
                targets.append((vehicle_id, int(lap)))

    if not targets and args.auto_best > 0:
        best_records = [rec for rec in lap_records if rec["is_best_vehicle"]]
        best_records.sort(key=lambda rec: rec["lap_time_seconds"])
        for rec in best_records[: args.auto_best]:
            targets.append((rec["vehicle_id"], int(rec["lap"])))

    unique_targets = list(dict.fromkeys(targets))
    if not unique_targets:
        raise SystemExit("No target vehicle/lap pairs selected")
    return unique_targets


def stream_telemetry(
    path: Path,
    targets: list[tuple[str, int]],
    time_column: str,
    context_channels: Iterable[str] = ("lat", "lng"),
) -> dict[tuple[str, int], dict[str, dict[str, float]]]:
    """Read the selected laps; neighbour laps keep only ``context_channels``.

    The default keeps GPS for boundary smoothing. Channel time alignment also
    needs the re-timed logger channels of the neighbour laps (see
    alignment_context_channels).
    """
    if not path.exists():
        raise SystemExit(f"Telemetry CSV not found: {path}")
    context_channels = frozenset(context_channels)

    target_laps_by_vehicle: dict[str, set[int]] = defaultdict(set)
    for vehicle_id, lap in targets:
        target_laps_by_vehicle[vehicle_id].add(lap)
    # Keep GPS from immediate neighbour laps even when only one lap is selected.
    # Cutting before smoothing otherwise moves the first/last point along the
    # trajectory by one sample interval (about 5 m at Fuji's start/finish line).
    context_laps_by_vehicle = {
        vehicle_id: {nearby for lap in laps for nearby in (lap - 1, lap + 1)}
        for vehicle_id, laps in target_laps_by_vehicle.items()
    }

    samples: dict[tuple[str, int], dict[str, dict[str, float]]] = {
        target: defaultdict(dict) for target in targets
    }
    rows_seen = 0
    rows_kept = 0

    print(f"Streaming telemetry: {path}")
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_seen += 1
            if rows_seen % 5_000_000 == 0:
                print(f"  rows read: {rows_seen:,}; rows kept: {rows_kept:,}")

            vehicle_id = row.get("vehicle_id", "")
            if vehicle_id not in target_laps_by_vehicle:
                continue
            lap_s = row.get("lap", "")
            if not lap_s.isdigit():
                continue
            lap = int(lap_s)
            is_target = lap in target_laps_by_vehicle[vehicle_id]
            if not is_target and lap not in context_laps_by_vehicle[vehicle_id]:
                continue

            channel = CHANNEL_ALIASES.get(row.get("telemetry_name", ""))
            if channel is None:
                continue
            if not is_target and channel not in context_channels:
                continue
            ts = row.get(time_column) or row.get("timestamp") or row.get("meta_time")
            if not ts:
                continue
            try:
                value = float(row.get("telemetry_value", ""))
            except ValueError:
                continue
            if not math.isfinite(value):
                # Guard against inf/nan telemetry (e.g. sensor dropouts encoded
                # as inf): json.dump would emit bare Infinity/NaN tokens that
                # browsers' JSON.parse rejects. Skip like any unparseable row.
                continue

            samples.setdefault((vehicle_id, lap), defaultdict(dict))[ts][channel] = value
            rows_kept += 1

    print(f"Telemetry rows read: {rows_seen:,}")
    print(f"Telemetry rows kept: {rows_kept:,}")
    return samples


def moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1 or len(values) < 3:
        return values[:]
    if window % 2 == 0:
        window += 1
    radius = window // 2
    smoothed: list[float] = []
    for i in range(len(values)):
        start = max(0, i - radius)
        end = min(len(values), i + radius + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def filter_gps_outliers(samples: list[dict[str, float]], max_speed_mps: float) -> tuple[list[dict[str, float]], int]:
    if len(samples) < 2:
        return samples, 0
    kept = [samples[0]]
    removed = 0
    for sample in samples[1:]:
        prev = kept[-1]
        dt = sample["t"] - prev["t"]
        if dt <= 0:
            removed += 1
            continue
        jump = haversine_m(prev["lat"], prev["lng"], sample["lat"], sample["lng"])
        if jump <= max(30.0, max_speed_mps * dt):
            kept.append(sample)
        else:
            removed += 1
    return kept, removed


def smooth_with_lap_context(
    samples: list[dict[str, float]],
    context: dict[str, dict[str, float]],
    start_time: datetime,
    window: int,
    max_speed_mps: float,
) -> tuple[list[float], list[float], dict[str, Any]]:
    """Use continuous neighbouring GPS without changing this lap's timebase.

    Context only supplies up to half a window on either side. Do not average
    across a missing-sample gap, non-finite fix or implausible jump. At a real
    session endpoint the available-window mean is retained and documented.
    """
    window = max(1, window + (window % 2 == 0))
    radius = window // 2 if len(samples) >= 3 else 0
    times = [sample["t"] for sample in samples]
    cadence = statistics.median(b - a for a, b in zip(times, times[1:]))
    candidates = []
    for ts, channels in context.items():
        if not all(key in channels and math.isfinite(channels[key]) for key in ("lat", "lng")):
            continue
        try:
            t = (parse_iso(ts) - start_time).total_seconds()
        except ValueError:
            continue
        if t < times[0] or t > times[-1]:
            candidates.append({"t": t, "lat": channels["lat"], "lng": channels["lng"]})
    candidates.sort(key=lambda sample: sample["t"])

    def continuous_neighbours(pool: list[dict[str, float]], anchor: dict[str, float]) -> list[dict[str, float]]:
        result = []
        for sample in pool[:radius]:
            dt = abs(sample["t"] - anchor["t"])
            jump = haversine_m(anchor["lat"], anchor["lng"], sample["lat"], sample["lng"])
            if not 0 < dt <= cadence * 1.5 or jump > max(30.0, max_speed_mps * dt):
                break
            result.append(sample)
            anchor = sample
        return result

    before = continuous_neighbours([s for s in candidates if s["t"] < times[0]][::-1], samples[0])[::-1]
    after = continuous_neighbours([s for s in candidates if s["t"] > times[-1]], samples[-1])
    padded = before + samples + after
    offset = len(before)
    lats = moving_average([s["lat"] for s in padded], window)[offset:offset + len(samples)]
    lngs = moving_average([s["lng"] for s in padded], window)[offset:offset + len(samples)]
    return lats, lngs, {
        "method": "centered_moving_average",
        "window_points": window,
        "boundary_policy": "continuous_adjacent_lap_gps",
        "context_points_before": len(before),
        "context_points_after": len(after),
        "complete_boundary_windows": len(before) == radius and len(after) == radius,
    }


def lap_channel_alignment(config: dict[str, Any] | None, vehicle_id: str, lap: int) -> dict[str, Any] | None:
    """Resolve the channel time alignment for one lap, or None to leave it untouched.

    ``config`` (the --channel-time-alignment JSON) may hold a session default
    and per-lap entries keyed "VEHICLE:LAP" or "LAP":
      {"channels": ["aps", ...],          # optional; default: all ALIGNABLE_CHANNELS
       "sampler": "csv-linear-v1",        # optional; any other sampler is refused
       "shiftSeconds": 0.5,               # optional session default
       "estimate": {...},                 # optional provenance for the default
       "laps": {"3": {"shiftSeconds": 0.49, "estimate": {...}}}}
    """
    if not config:
        return None
    sampler = config.get("sampler")
    if sampler not in (None, CSV_LINEAR_SAMPLER):
        raise SystemExit(
            f"Channel time alignment sampler {sampler!r} is not build_race's linear CSV re-timing ({CSV_LINEAR_SAMPLER}); "
            "re-run scripts/quality/align-fuji-channel-timing.py with the source XRK instead"
        )
    laps = config.get("laps") or {}
    entry = laps.get(f"{vehicle_id}:{lap}", laps.get(str(lap)))
    if entry is None:
        if config.get("shiftSeconds") is None:
            return None
        entry = {"shiftSeconds": config["shiftSeconds"], "estimate": config.get("estimate")}
    shift = float(entry["shiftSeconds"])
    if not math.isfinite(shift) or abs(shift) > MAX_CHANNEL_SHIFT_SECONDS:
        raise SystemExit(f"Channel shift for {vehicle_id} lap {lap} must be finite and <= {MAX_CHANNEL_SHIFT_SECONDS} s: {shift}")
    channels = list(entry.get("channels") or config.get("channels") or ALIGNABLE_CHANNELS)
    unknown = [channel for channel in channels if channel not in ALIGNABLE_CHANNELS]
    if unknown:
        raise SystemExit(f"Channel time alignment cannot re-time {unknown}; allowed: {list(ALIGNABLE_CHANNELS)}")
    return {"shiftSeconds": shift, "estimate": entry.get("estimate"), "channels": channels}


def alignment_context_channels(config: dict[str, Any] | None) -> tuple[str, ...]:
    """Source keys stream_telemetry() must keep for neighbour laps (GPS always)."""
    keys = ["lat", "lng"]
    if config:
        channels = set(config.get("channels") or ALIGNABLE_CHANNELS)
        for entry in (config.get("laps") or {}).values():
            channels.update(entry.get("channels") or [])
        for channel in ALIGNABLE_CHANNELS:
            if channel in channels:
                keys.extend(ALIGNABLE_CHANNELS[channel])
    return tuple(keys)


def logger_channel_series(
    row_sets: Iterable[dict[str, dict[str, float]]],
    start_time: datetime,
    keys: Iterable[str],
) -> dict[str, tuple[list[float], list[float]]]:
    """Per source key, (times, values) in seconds after ``start_time``, sorted.

    Later row sets win on a duplicate timestamp, so pass neighbour-lap context
    first and the lap's own rows last.
    """
    points: dict[str, dict[float, float]] = {key: {} for key in keys}
    for rows in row_sets:
        for ts, channels in rows.items():
            present = [key for key in points if key in channels]
            if not present:
                continue
            try:
                t = (parse_iso(ts) - start_time).total_seconds()
            except ValueError:
                continue
            for key in present:
                if math.isfinite(channels[key]):
                    points[key][t] = channels[key]
    series = {}
    for key, by_time in points.items():
        if by_time:
            times = sorted(by_time)
            series[key] = (times, [by_time[t] for t in times])
    return series


def sample_channel_at(times: list[float], values: list[float], t: float, nearest: bool = False) -> tuple[float, bool]:
    """Channel value at time ``t`` and whether ``t`` lay outside the samples.

    Linear interpolation between the bracketing samples (nearest sample when
    ``nearest``; ties resolve to the earlier sample). An exact sample time
    returns that sample unchanged. Outside the sampled span the edge value is
    held and the flag is True.
    """
    i = bisect_right(times, t)
    if i > 0 and times[i - 1] == t:
        return values[i - 1], False
    if i == 0:
        return values[0], True
    if i == len(times):
        return values[-1], True
    t0, t1 = times[i - 1], times[i]
    if nearest:
        return (values[i - 1] if t - t0 <= t1 - t else values[i]), False
    return values[i - 1] + (values[i] - values[i - 1]) * (t - t0) / (t1 - t0), False


def align_logger_channels(
    samples: list[dict[str, float]],
    series: dict[str, tuple[list[float], list[float]]],
    alignment: dict[str, Any],
    lap_span: tuple[float, float],
) -> dict[str, Any]:
    """Re-time the logger-clock channels of ``samples`` in place (see ALIGNABLE_CHANNELS).

    Only the listed channels are written; t/lat/lng/speed are never touched.
    Returns the meta.channel_time_alignment record.
    """
    shift = alignment["shiftSeconds"]
    channels = alignment["channels"]
    for channel in channels:
        if not any(key in series for key in ALIGNABLE_CHANNELS[channel]):
            raise SystemExit(f"Channel time alignment: no source samples for {channel}")
    neighbour = held = 0
    for sample in samples:
        source_t = sample["t"] - shift
        neighbour += not lap_span[0] <= source_t <= lap_span[1]
        sample_held = False
        for channel in channels:
            values = []
            for key in ALIGNABLE_CHANNELS[channel]:
                if key not in series:
                    values.append(0.0)  # same default as the unaligned forward-fill
                    continue
                value, was_held = sample_channel_at(*series[key], source_t, channel in NEAREST_SAMPLE_CHANNELS)
                sample_held = sample_held or was_held
                values.append(value)
            sample[channel] = max(values)
        held += sample_held
    return {
        "method": CHANNEL_ALIGNMENT_METHOD,
        "definition": (
            "channel(t) = logger channel at (t - shiftSeconds); positive shift = logger channel delayed. "
            "Linear interpolation over the continuous session series incl. neighbouring laps; "
            "nearest sample for discrete channels. GPS t/lat/lng/speed/dist unchanged."
        ),
        "shiftSeconds": shift,
        "estimate": alignment.get("estimate"),
        "channels": list(channels),
        "nearestSampleChannels": [channel for channel in channels if channel in NEAREST_SAMPLE_CHANNELS],
        "samplesFromNeighbourLaps": neighbour,
        "heldEdgeSamples": held,
    }


def compact_from_timeseries(
    vehicle_id: str,
    lap: int,
    by_timestamp: dict[str, dict[str, float]],
    lap_record: dict[str, Any] | None,
    args: argparse.Namespace,
    gps_context: dict[str, dict[str, float]] | None = None,
    channel_alignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact lap.

    ``gps_context`` holds neighbour-lap rows: their GPS supplies the boundary
    smoothing window and, when ``channel_alignment`` (from
    lap_channel_alignment) is given, their logger channels supply the re-timed
    values near the lap boundaries.
    """
    rows = []
    for ts, channels in by_timestamp.items():
        try:
            parsed = parse_iso(ts)
        except ValueError:
            continue
        rows.append((parsed, ts, channels))
    rows.sort(key=lambda item: item[0])

    last = {
        "speed": 0.0,
        "aps": 0.0,
        "brake_f": 0.0,
        "brake_r": 0.0,
        "steer": 0.0,
        "gear": 0.0,
        "accx": 0.0,
        "accy": 0.0,
    }
    raw_samples: list[dict[str, float]] = []
    start_time: datetime | None = None
    first_ts = ""
    last_ts = ""

    for parsed, ts, channels in rows:
        for key, value in channels.items():
            if key in last:
                last[key] = value

        if "lat" not in channels or "lng" not in channels:
            continue
        if start_time is None:
            start_time = parsed
            first_ts = ts
        last_ts = ts
        brake = max(last["brake_f"], last["brake_r"])
        raw_samples.append(
            {
                "t": (parsed - start_time).total_seconds(),
                "lat": channels["lat"],
                "lng": channels["lng"],
                "speed": last["speed"],
                "aps": last["aps"],
                "brake": brake,
                "steer": last["steer"],
                "gear": last["gear"],
                "accx": last["accx"],
                "accy": last["accy"],
            }
        )

    filtered_samples, removed = filter_gps_outliers(raw_samples, args.max_gps_speed_mps)
    if len(filtered_samples) < 2:
        raise SystemExit(f"Not enough GPS samples for {vehicle_id} lap {lap}: {len(filtered_samples)}")

    lats, lngs, gps_processing = smooth_with_lap_context(
        filtered_samples, gps_context or {}, start_time,
        args.smooth_window, args.max_gps_speed_mps,
    )
    start_t = filtered_samples[0]["t"]

    channel_time_alignment = None
    if channel_alignment and channel_alignment["channels"]:
        keys = [key for channel in channel_alignment["channels"] for key in ALIGNABLE_CHANNELS[channel]]
        series = logger_channel_series([gps_context or {}, by_timestamp], start_time, keys)
        lap_span = ((rows[0][0] - start_time).total_seconds(), (rows[-1][0] - start_time).total_seconds())
        channel_time_alignment = align_logger_channels(filtered_samples, series, channel_alignment, lap_span)

    dist = [0.0]
    for i in range(1, len(filtered_samples)):
        dist.append(dist[-1] + haversine_m(lats[i - 1], lngs[i - 1], lats[i], lngs[i]))

    compact: dict[str, Any] = {
        "meta": {
            "version": 1,
            "track_id": args.track_id,
            "race_id": args.race_id,
            "vehicle_id": vehicle_id,
            "vehicle_number": (lap_record or {}).get("vehicle_number", ""),
            "lap": lap,
            "lap_time_seconds": (lap_record or {}).get("lap_time_seconds"),
            "lap_time": (lap_record or {}).get("lap_time"),
            "point_count": len(filtered_samples),
            "raw_gps_points": len(raw_samples),
            "gps_outliers_removed": removed,
            "total_distance_m": round(dist[-1], 2),
            "source_file": str(args.telemetry),
            "time_column": args.time_column,
            "first_sample_time": first_ts,
            "last_sample_time": last_ts,
            "gps_processing": gps_processing,
            "lap_start_time": (lap_record or {}).get("start_time"),
            "first_sample_after_lap_start_seconds": (
                round((start_time - parse_iso(lap_record["start_time"])).total_seconds(), 6)
                if lap_record and lap_record.get("start_time") else None
            ),
        }
    }
    if channel_time_alignment is not None:
        compact["meta"]["channel_time_alignment"] = channel_time_alignment
    for key in COMPACT_KEYS:
        compact[key] = []

    for i, sample in enumerate(filtered_samples):
        compact["t"].append(round(sample["t"] - start_t, 3))
        compact["lat"].append(round(lats[i], 8))
        compact["lng"].append(round(lngs[i], 8))
        compact["speed"].append(round(sample["speed"], 3))
        compact["aps"].append(round(sample["aps"], 3))
        compact["brake"].append(round(sample["brake"], 3))
        compact["steer"].append(round(sample["steer"], 3))
        compact["gear"].append(int(round(sample["gear"])))
        compact["accx"].append(round(sample["accx"], 4))
        compact["accy"].append(round(sample["accy"], 4))
        compact["dist"].append(round(dist[i], 3))

    return compact


def write_outputs(
    output_dir: Path,
    race_id: str,
    targets: list[tuple[str, int]],
    lap_records: list[dict[str, Any]],
    telemetry: dict[tuple[str, int], dict[str, dict[str, float]]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records_by_key = {(rec["vehicle_id"], int(rec["lap"])): rec for rec in lap_records}
    generated: list[dict[str, Any]] = []

    alignment_config = getattr(args, "channel_alignment_config", None)
    for vehicle_id, lap in targets:
        lap_record = records_by_key.get((vehicle_id, lap))
        gps_context = {
            ts: channels
            for neighbour in (lap - 1, lap + 1)
            for ts, channels in telemetry.get((vehicle_id, neighbour), {}).items()
        }
        compact = compact_from_timeseries(
            vehicle_id, lap, telemetry.get((vehicle_id, lap), {}), lap_record, args, gps_context,
            lap_channel_alignment(alignment_config, vehicle_id, lap),
        )
        filename = f"{vehicle_slug(vehicle_id)}_lap_{lap:03d}.json"
        path = output_dir / filename
        with path.open("w") as f:
            json.dump(compact, f, separators=(",", ":"))
            f.write("\n")
        generated.append(
            {
                "vehicle_id": vehicle_id,
                "lap": lap,
                "data_file": filename,
                "point_count": compact["meta"]["point_count"],
                "file_size": path.stat().st_size,
                "total_distance_m": compact["meta"]["total_distance_m"],
            }
        )
        print(
            f"Saved {filename}: {compact['meta']['point_count']} points, "
            f"{path.stat().st_size / 1024:.1f} KB"
        )

    generated_by_key = {(item["vehicle_id"], item["lap"]): item for item in generated}
    index_records = []
    for rec in lap_records:
        item = dict(rec)
        generated_item = generated_by_key.get((rec["vehicle_id"], int(rec["lap"])))
        if generated_item:
            item["data_file"] = generated_item["data_file"]
            item["point_count"] = generated_item["point_count"]
            item["file_size"] = generated_item["file_size"]
            item["total_distance_m"] = generated_item["total_distance_m"]
        index_records.append(item)

    laps_index = {
        "version": 1,
        "race_id": race_id,
        "track_id": args.track_id,
        "source_lap_times": args.lap_times.name,
        "selected": generated,
        "laps": index_records,
    }
    index_path = output_dir / "laps.json"
    with index_path.open("w") as f:
        json.dump(laps_index, f, indent=2)
        f.write("\n")
    print(f"Saved {index_path}")
    return generated


def load_channel_alignment_config(args: argparse.Namespace) -> dict[str, Any] | None:
    config: dict[str, Any] | None = None
    if args.channel_time_alignment is not None:
        config = json.loads(args.channel_time_alignment.read_text(encoding="utf-8"))
    if args.channel_shift is not None:
        config = dict(config or {})
        config["shiftSeconds"] = args.channel_shift
        config["estimate"] = {"method": "manual --channel-shift"}
    return config


def main() -> None:
    args = parse_args()
    args.channel_alignment_config = load_channel_alignment_config(args)
    lap_records = read_lap_index(args.lap_times)
    targets = select_targets(args, lap_records)
    print("Selected targets:")
    for vehicle_id, lap in targets:
        rec = next((r for r in lap_records if r["vehicle_id"] == vehicle_id and int(r["lap"]) == lap), None)
        if rec:
            print(f"  {vehicle_id} lap {lap} ({rec['lap_time']})")
        else:
            print(f"  {vehicle_id} lap {lap}")

    telemetry = stream_telemetry(
        args.telemetry, targets, args.time_column, alignment_context_channels(args.channel_alignment_config),
    )
    generated = write_outputs(args.output_dir, args.race_id, targets, lap_records, telemetry, args)
    print("Generated laps:")
    for item in generated:
        print(
            f"  {item['vehicle_id']} lap {item['lap']}: "
            f"{item['point_count']} points, {item['file_size'] / 1024:.1f} KB"
        )


if __name__ == "__main__":
    main()
