#!/usr/bin/env python3
"""Check the shipped Fuji GPS handoff inputs without XRK, video, or Python packages."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "artifacts/gps-accuracy-2026-09-22/verification-manifest.json"
RACES = {"fuji_aim_01": 4, "fuji_aim_2020_07_30": 6}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check() -> list[str]:
    errors: list[str] = []
    manifest = load(MANIFEST)
    if set(manifest["races"]) != set(RACES):
        errors.append("manifest race set differs from expected two sessions")
    for race, count in RACES.items():
        race_dir = ROOT / "public/data/races" / race
        reg_path = race_dir / "gps_registration.json"
        reg = load(reg_path)
        saved = manifest["races"][race]
        if digest(reg_path) != saved["registrationSha256"]:
            errors.append(f"{race}: registration SHA differs")
        if reg.get("raceId") != race or len(reg["laps"]) != count:
            errors.append(f"{race}: race ID or lap count differs")
        index = load(race_dir / "laps.json")
        selected = index["selected"]
        if len(selected) != count:
            errors.append(f"{race}: lap index count differs")
        reg_files = {lap["dataFile"] for lap in reg["laps"]}
        index_files = {lap["data_file"] for lap in selected}
        if reg_files != index_files:
            errors.append(f"{race}: registration and lap index differ")
        for entry in saved["inputs"]:
            source = entry["source"]
            path = ROOT / source
            if not path.is_file() or digest(path) != entry["sha256"]:
                errors.append(f"{race}: input SHA differs or file missing: {source}")
        for key in ("limits", "referenceTrack"):
            entry = reg[key]
            path = ROOT / entry["source"]
            if not path.is_file() or digest(path) != entry["sha256"]:
                errors.append(f"{race}: {key} SHA differs or file missing")
        for lap in reg["laps"]:
            path = race_dir / lap["dataFile"]
            if not path.is_file() or digest(path) != lap["dataSha256"]:
                errors.append(f"{race}: lap SHA differs or file missing: {lap['dataFile']}")
        print(f"{race}: {count} laps, registration and input hashes checked")
    return errors


if __name__ == "__main__":
    try:
        failures = check()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"handoff check could not complete: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: shipped Fuji GPS handoff files match the saved hashes")
