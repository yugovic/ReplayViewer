#!/usr/bin/env python3
"""Clean the Barber track elevation profile.

The processing is intentionally stdlib-only so it can run in the repo without
extra Python dependencies.
"""

from __future__ import annotations

import argparse
import bisect
import http.client
import json
import math
import signal
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACK = PROJECT_ROOT / "public/data/tracks/barber/track.json"
CACHE_PATH = Path(__file__).resolve().parent / "cache/epqs_barber.json"
EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
USER_AGENT = "replay-viewer-v2 elevation smoother/1.0"
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_CONSECUTIVE_LIVE_FAILURES = 10
MAX_INITIAL_LIVE_FAILURES = 3


class RequestTimeout(Exception):
    """Raised when an EPQS request exceeds the hard wall-clock timeout."""


def timeout_handler(_signum: int, _frame: object) -> None:
    raise RequestTimeout()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clamp and smooth the Barber centerline elevation profile."
    )
    parser.add_argument(
        "--track",
        default=str(DEFAULT_TRACK),
        help="Track JSON file to update.",
    )
    parser.add_argument(
        "--fetch-3dep",
        action="store_true",
        help="Fetch elevations from the USGS 3DEP Elevation Point Query Service.",
    )
    parser.add_argument(
        "--max-grade",
        type=float,
        default=0.12,
        help="Maximum allowed absolute grade as a ratio.",
    )
    parser.add_argument(
        "--smooth-window-m",
        type=float,
        default=40.0,
        help="Full moving-average smoothing window in metres.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    text = json.dumps(data, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def is_closing_duplicate(points: list[dict]) -> bool:
    if len(points) < 2:
        return False
    first = points[0]
    last = points[-1]
    keys = ("lat", "lng", "x", "z")
    return all(abs(float(first[key]) - float(last[key])) < 1e-9 for key in keys)


def cache_key(point: dict) -> str:
    return f"{point['lat']},{point['lng']}"


def load_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        raw = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read 3DEP cache {path}: {exc}", file=sys.stderr)
        return {}

    cache: dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            cache[key] = number
    return cache


def save_cache(path: Path, cache: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, dict(sorted(cache.items())))


def fetch_epqs_value(lat: float, lng: float) -> float | None:
    params = urllib.parse.urlencode(
        {"x": lng, "y": lat, "units": "Meters", "wkid": 4326}
    )
    url = f"{EPQS_URL}?{params}"

    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        old_handler = signal.getsignal(signal.SIGALRM)
        try:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, REQUEST_TIMEOUT_SECONDS)
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            value = float(payload["value"])
            if math.isfinite(value):
                return value
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
            http.client.HTTPException,
            RequestTimeout,
        ):
            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, old_handler)

        if attempt < 3:
            time.sleep(0.5 * (2**attempt))

    return None


def fetch_3dep_alts(points: list[dict], existing_alts: list[float]) -> list[float]:
    cache = load_cache(CACHE_PATH)
    fetched: list[float | None] = []
    successes = 0
    network_successes = 0
    consecutive_live_failures = 0
    network_requests = 0

    for index, point in enumerate(points, start=1):
        key = cache_key(point)
        cached = cache.get(key)
        if cached is not None:
            fetched.append(cached)
            successes += 1
        else:
            if network_requests:
                time.sleep(0.15)
            network_requests += 1
            value = fetch_epqs_value(float(point["lat"]), float(point["lng"]))
            if value is None:
                fetched.append(None)
                consecutive_live_failures += 1
            else:
                cache[key] = value
                fetched.append(value)
                successes += 1
                network_successes += 1
                consecutive_live_failures = 0

            if (
                network_successes == 0
                and consecutive_live_failures >= MAX_INITIAL_LIVE_FAILURES
            ) or consecutive_live_failures >= MAX_CONSECUTIVE_LIVE_FAILURES:
                remaining = len(points) - index
                if remaining:
                    fetched.extend([None] * remaining)
                print(
                    "Warning: repeated live 3DEP requests failed; "
                    "stopping fetch early and using cache/existing elevations.",
                    file=sys.stderr,
                )
                break

        if index % 50 == 0:
            print(f"3DEP fetch: {index}/{len(points)} points, {successes} available")
            save_cache(CACHE_PATH, cache)

    save_cache(CACHE_PATH, cache)

    success_ratio = successes / len(points) if points else 0.0
    if success_ratio < 0.90:
        print(
            "Warning: only "
            f"{successes}/{len(points)} 3DEP elevations available "
            "(<90%); using existing track elevations for this run.",
            file=sys.stderr,
        )
        return existing_alts[:]

    failed = len(points) - successes
    if failed:
        print(
            f"3DEP fetch: using fetched elevations for {successes}/{len(points)} "
            f"points; {failed} failed points keep existing alt."
        )
    else:
        print(f"3DEP fetch: using fetched elevations for all {len(points)} points.")

    return [
        existing_alts[index] if value is None else value
        for index, value in enumerate(fetched)
    ]


def segment_lengths(dists: list[float], total_length: float) -> list[float]:
    lengths: list[float] = []
    for index, dist in enumerate(dists):
        if index + 1 < len(dists):
            length = dists[index + 1] - dist
        else:
            length = total_length - dist + dists[0]
        if length <= 0:
            raise ValueError(
                f"Non-positive segment length at open-loop index {index}: {length}"
            )
        lengths.append(length)
    return lengths


def max_abs_grade(alts: list[float], dists: list[float], total_length: float) -> float:
    lengths = segment_lengths(dists, total_length)
    maximum = 0.0
    for index, length in enumerate(lengths):
        next_index = (index + 1) % len(alts)
        grade = abs(alts[next_index] - alts[index]) / length
        maximum = max(maximum, grade)
    return maximum


def stats(
    alts: list[float], dists: list[float], total_length: float
) -> tuple[float, float, float]:
    return min(alts), max(alts), max_abs_grade(alts, dists, total_length)


def clamp_gradients(
    alts: list[float],
    dists: list[float],
    total_length: float,
    max_grade: float,
    max_iterations: int = 1000,
) -> list[float]:
    values = alts[:]
    lengths = segment_lengths(dists, total_length)
    count = len(values)

    for _ in range(max_iterations):
        max_delta = 0.0

        for index in range(count):
            next_index = (index + 1) % count
            limit = max_grade * lengths[index]
            lower = values[index] - limit
            upper = values[index] + limit
            old = values[next_index]
            values[next_index] = min(max(old, lower), upper)
            max_delta = max(max_delta, abs(values[next_index] - old))

        for index in range(count - 1, -1, -1):
            prev_index = (index - 1) % count
            limit = max_grade * lengths[prev_index]
            lower = values[index] - limit
            upper = values[index] + limit
            old = values[prev_index]
            values[prev_index] = min(max(old, lower), upper)
            max_delta = max(max_delta, abs(values[prev_index] - old))

        if max_delta < 1e-9:
            return values

    print(
        f"Warning: gradient clamp did not fully converge after {max_iterations} passes.",
        file=sys.stderr,
    )
    return values


def integrate_non_wrapped(
    values: list[float],
    closed_values: list[float],
    knots: list[float],
    start: float,
    end: float,
) -> float:
    if end <= start:
        return 0.0

    area = 0.0
    index = bisect.bisect_right(knots, start) - 1
    index = min(max(index, 0), len(values) - 1)
    cursor = start

    while cursor < end - 1e-12:
        segment_end = min(end, knots[index + 1])
        if segment_end <= cursor + 1e-12:
            index += 1
            if index >= len(values):
                break
            continue

        x0 = knots[index]
        x1 = knots[index + 1]
        y0 = closed_values[index]
        y1 = closed_values[index + 1]
        span = x1 - x0
        if span <= 0:
            y_start = y0
            y_end = y1
        else:
            t_start = (cursor - x0) / span
            t_end = (segment_end - x0) / span
            y_start = y0 + (y1 - y0) * t_start
            y_end = y0 + (y1 - y0) * t_end

        area += 0.5 * (y_start + y_end) * (segment_end - cursor)
        cursor = segment_end

    return area


def integrate_periodic(
    values: list[float],
    closed_values: list[float],
    knots: list[float],
    total_length: float,
    start: float,
    end: float,
) -> float:
    if end <= start:
        return 0.0

    area = 0.0
    cursor = start
    while cursor < end - 1e-12:
        wrapped = cursor % total_length
        if math.isclose(wrapped, total_length):
            wrapped = 0.0
        span = min(end - cursor, total_length - wrapped)
        if span <= 1e-12:
            wrapped = 0.0
            span = min(end - cursor, total_length)

        area += integrate_non_wrapped(
            values, closed_values, knots, wrapped, wrapped + span
        )
        cursor += span

    return area


def moving_average(
    alts: list[float],
    dists: list[float],
    total_length: float,
    window_m: float,
) -> list[float]:
    if window_m <= 0:
        return alts[:]

    knots = dists[:] + [total_length]
    closed_values = alts[:] + [alts[0]]

    if window_m >= total_length:
        area = integrate_non_wrapped(alts, closed_values, knots, 0.0, total_length)
        return [area / total_length for _ in alts]

    half_window = window_m / 2.0
    return [
        integrate_periodic(
            alts,
            closed_values,
            knots,
            total_length,
            dist - half_window,
            dist + half_window,
        )
        / window_m
        for dist in dists
    ]


def print_stats(label: str, values: tuple[float, float, float]) -> None:
    min_alt, max_alt, grade = values
    print(
        f"{label}: alt min={min_alt:.2f} m, max={max_alt:.2f} m, "
        f"max |grade|={grade:.4f} ({grade * 100:.1f}%)"
    )


def main() -> int:
    args = parse_args()
    track_path = Path(args.track)
    if args.max_grade <= 0:
        print("--max-grade must be positive.", file=sys.stderr)
        return 2
    if args.smooth_window_m < 0:
        print("--smooth-window-m must be non-negative.", file=sys.stderr)
        return 2

    data = read_json(track_path)
    centerline = data.get("centerline")
    if not isinstance(centerline, list) or len(centerline) < 3:
        print(
            "Track JSON must contain a centerline with at least 3 points.",
            file=sys.stderr,
        )
        return 2
    if not is_closing_duplicate(centerline):
        print("Centerline does not appear to end with a duplicate closing point.", file=sys.stderr)
        return 2

    open_points = centerline[:-1]
    total_length = float(data.get("totalLength", centerline[-1]["dist"]))
    origin_alt = float(data["origin"]["alt"])
    dists = [float(point["dist"]) for point in open_points]
    original_alts = [float(point["alt"]) for point in open_points]

    before = stats(original_alts, dists, total_length)
    working_alts = original_alts[:]
    if args.fetch_3dep:
        all_original_alts = [float(point["alt"]) for point in centerline]
        working_alts = fetch_3dep_alts(centerline, all_original_alts)[:-1]

    processed = clamp_gradients(working_alts, dists, total_length, args.max_grade)
    for _ in range(2):
        processed = moving_average(processed, dists, total_length, args.smooth_window_m)

    rounded_alts = [round(alt, 2) for alt in processed]
    after = stats(rounded_alts, dists, total_length)
    changed = sum(
        1
        for old_alt, new_alt in zip(original_alts, rounded_alts)
        if abs(old_alt - new_alt) > 0.5
    )

    print_stats("Before", before)
    print_stats("After ", after)
    print(f"Changed >0.5 m: {changed}/{len(open_points)} open-loop points")

    backup_path = track_path.with_name("track.elevation-orig.bak.json")
    if backup_path.exists():
        print(f"Backup already exists: {backup_path}")
    else:
        shutil.copy2(track_path, backup_path)
        print(f"Created backup: {backup_path}")

    for point, alt in zip(open_points, rounded_alts):
        point["alt"] = alt
        point["y"] = round(alt - origin_alt, 2)

    centerline[-1]["alt"] = centerline[0]["alt"]
    centerline[-1]["y"] = centerline[0]["y"]
    data["elevationRange"] = [round(min(rounded_alts), 2), round(max(rounded_alts), 2)]

    write_json(track_path, data)
    print(f"Wrote: {track_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
