#!/usr/bin/env python3
"""Rebuild all ten Fuji laps; fail closed on any unrelated source change.

Requires the original logs locally. Recreate the missing July 29 CSV first:
python pipeline/convert_aim.py --input <original July 29 .xrk> --out
artifacts/gps-accuracy-2026-09-22/preprocessing/fuji_aim_converted.csv

The baseline snapshot is retained unchanged. --apply publishes only after all
laps pass old-build reproduction and exact telemetry/interior comparisons.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/gps-accuracy-2026-09-22/preprocessing"
BASELINE = OUT.parent / "baseline"
sys.path.insert(0, str(ROOT / "pipeline"))
import build_race as current


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def distance(a, b, index):
    return current.haversine_m(a["lat"][index], a["lng"][index], b["lat"][index], b["lng"][index])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--wet-xrk", type=Path, required=True, help="local July 29 source XRK (never published)")
    parser.add_argument("--dry-xrk", type=Path, required=True, help="local July 30 source XRK (never published)")
    parser.add_argument("--wet-csv", type=Path, default=OUT / "fuji_aim_converted.csv")
    parser.add_argument("--dry-csv", type=Path, default=ROOT / "pipeline/cache/osaki_hmr_demio_101_fuji_generic_testing_a_1741_converted.csv")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("legacy_build_race", BASELINE / "pipeline/build_race.py")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    sessions = [
        ("fuji_aim_01", args.wet_csv.resolve(), args.wet_xrk.resolve()),
        ("fuji_aim_2020_07_30", args.dry_csv.resolve(), args.dry_xrk.resolve()),
    ]
    report = {"version": 1, "scope": "Deterministic lap-cut smoothing error; not surveyed position accuracy", "sessions": [], "laps": [], "checks": {"legacyReconstructionWithinOneCoordinateRoundingUnit": True, "allInteriorCoordinatesExactlyUnchanged": True, "allTimeAndSensorArraysExactlyUnchanged": True}}
    candidates = []
    originals = {}
    for race, csv_path, raw_path in sessions:
        originals[raw_path] = sha(raw_path)
        originals[csv_path] = sha(csv_path)
        lap_csv = csv_path.with_name(csv_path.stem + "_laps.csv")
        originals[lap_csv] = sha(lap_csv)
        baseline_dir = BASELINE / "public/data/races" / race
        index = load(baseline_dir / "laps.json")
        targets = [(record["vehicle_id"], record["lap"]) for record in index["selected"]]
        telemetry = current.stream_telemetry(csv_path, targets, "timestamp")
        records = current.read_lap_index(lap_csv)
        records_by_key = {(r["vehicle_id"], r["lap"]): r for r in records}
        csv_relative = relative(csv_path)
        build_args = argparse.Namespace(track_id="fuji", race_id=race, telemetry=Path(csv_relative), lap_times=lap_csv, time_column="timestamp", smooth_window=5, max_gps_speed_mps=90)
        session_report = {"raceId": race, "rawXrkSha256": originals[raw_path], "convertedCsv": csv_relative, "convertedCsvSha256": originals[csv_path], "lapTimesSha256": originals[lap_csv]}
        report["sessions"].append(session_report)
        for vehicle, lap in targets:
            filename = f"{current.vehicle_slug(vehicle)}_lap_{lap:03d}.json"
            old_path = baseline_dir / filename
            old = load(old_path)
            rows = telemetry[(vehicle, lap)]
            record = records_by_key[(vehicle, lap)]
            reproduced = legacy.compact_from_timeseries(vehicle, lap, rows, record, build_args)
            rounding_differences = []
            for key in current.COMPACT_KEYS:
                assert len(reproduced[key]) == len(old[key])
                if reproduced[key] != old[key]:
                    assert key in ("lat", "lng"), f"{race} L{lap}: legacy reconstruction differs in {key}"
                    for i, (a, b) in enumerate(zip(reproduced[key], old[key])):
                        if a != b:
                            assert i in (0, 1, len(old[key]) - 2, len(old[key]) - 1)
                            assert abs(a - b) <= 1.000001e-8, f"{race} L{lap}: source coordinate mismatch"
                            rounding_differences.append({"channel": key, "index": i, "published": b, "reconstructed": a})
            context = {ts: channels for neighbour in [lap - 1, lap + 1] for ts, channels in telemetry.get((vehicle, neighbour), {}).items()}
            new = current.compact_from_timeseries(vehicle, lap, rows, record, build_args, context)
            new["meta"]["gps_processing"].update({"source_csv_sha256": originals[csv_path], "source_xrk_sha256": originals[raw_path]})
            for key in current.COMPACT_KEYS:
                if key not in ["lat", "lng", "dist"]:
                    assert new[key] == old[key], f"{race} L{lap}: unexpected {key} change"
            for key in ["lat", "lng"]:
                assert new[key][2:-2] == old[key][2:-2], f"{race} L{lap}: interior coordinate changed"
            assert new["meta"]["gps_processing"]["complete_boundary_windows"], f"{race} L{lap}: missing neighbouring samples"
            assert new["meta"]["gps_outliers_removed"] == old["meta"]["gps_outliers_removed"]
            first = current.parse_iso(old["meta"]["first_sample_time"])
            raw = {"lat": [], "lng": []}
            for ts in sorted(rows, key=current.parse_iso):
                row = rows[ts]
                if "lat" in row and "lng" in row:
                    for key in raw:
                        raw[key].append(row[key])
            assert len(raw["lat"]) == len(old["t"])
            endpoint_indices = [0, 1, len(old["t"]) - 2, len(old["t"]) - 1]
            endpoint_report = [{"time": old["t"][i], "beforeVsRawGridMeters": distance(old, raw, i), "afterVsRawGridMeters": distance(new, raw, i), "appliedChangeMeters": distance(new, old, i)} for i in endpoint_indices]
            payload = (json.dumps(new, separators=(",", ":")) + "\n").encode()
            candidate_path = OUT / race / filename
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_bytes(payload)
            candidates.append((ROOT / "public/data/races" / race / filename, payload))
            for entry in index["selected"] + index["laps"]:
                if entry.get("data_file") == filename:
                    entry.update(file_size=len(payload), total_distance_m=new["meta"]["total_distance_m"])
            report["laps"].append({"raceId": race, "lap": lap, "pointCount": len(old["t"]), "dataFile": filename, "beforeSha256": sha(old_path), "afterSha256": hashlib.sha256(payload).hexdigest(), "firstSampleAfterLapStartSeconds": new["meta"]["first_sample_after_lap_start_seconds"], "legacyEndpointRoundingDifferences": rounding_differences, "interiorUnchanged": True, "timeAndSensorsUnchanged": True, "changedCoordinateCount": sum(a != b or c != d for a, b, c, d in zip(old["lat"], new["lat"], old["lng"], new["lng"])), "distanceChangeMeters": round(new["meta"]["total_distance_m"] - old["meta"]["total_distance_m"], 3), "endpoints": endpoint_report})
        index_payload = (json.dumps(index, indent=2) + "\n").encode()
        (OUT / race / "laps.json").write_bytes(index_payload)
        candidates.append((ROOT / "public/data/races" / race / "laps.json", index_payload))
    for path, original_hash in originals.items():
        assert sha(path) == original_hash, f"source modified: {path}"
    report["checks"]["sourceFilesUnchanged"] = True
    report["summary"] = {
        "lapCount": len(report["laps"]),
        "changedCoordinateCount": sum(lap["changedCoordinateCount"] for lap in report["laps"]),
        "maxEndpointDisplacementBeforeMeters": max(ep["beforeVsRawGridMeters"] for lap in report["laps"] for ep in lap["endpoints"]),
        "maxEndpointDisplacementAfterMeters": max(ep["afterVsRawGridMeters"] for lap in report["laps"] for ep in lap["endpoints"]),
        "timeOriginOffsetsSeconds": [lap["firstSampleAfterLapStartSeconds"] for lap in report["laps"]],
    }
    if args.apply:
        for path, payload in candidates:
            path.write_bytes(payload)
        for path, payload in candidates:
            assert path.read_bytes() == payload
    report["applied"] = args.apply
    (OUT / "rebuild-audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "checks": report["checks"], "applied": args.apply}, indent=2))


if __name__ == "__main__":
    main()
