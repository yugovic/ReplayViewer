#!/usr/bin/env python3
"""Does a fresh clone still pass the Fuji GPS hash checks?

The viewer and verify-fuji-gps-handoff.py compare SHA-256 of the shipped files
with values recorded at fit time, so the bytes Git hands out must equal the
bytes that were hashed. This exports a commit the way a fresh checkout would
produce it, once with core.autocrlf=true (Git for Windows default) and once
with core.autocrlf=false (Linux/macOS/CI), and runs the handoff verifier in
each. Standard library only; nothing in the working tree is modified.

    python scripts/quality/check-fuji-hash-portability.py [--rev HEAD] [--out result.json]
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = [
    "scripts/quality/verify-fuji-gps-handoff.py",
    "artifacts/gps-accuracy-2026-09-22/verification-manifest.json",
    "public/data/races/fuji_aim_01",
    "public/data/races/fuji_aim_2020_07_30",
    "public/data/tracks/fuji/track.json",
    "public/data/tracks/fuji/cg_study",
    "public/data/vehicles",
]


def run_mode(rev: str, autocrlf: str) -> dict:
    archive = subprocess.run(["git", "-c", f"core.autocrlf={autocrlf}", "archive", rev, *PATHS],
                             cwd=ROOT, capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory(prefix="fuji-hash-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp)
        done = subprocess.run([sys.executable, "scripts/quality/verify-fuji-gps-handoff.py"],
                              cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
    failures = [line for line in done.stderr.splitlines() if line.startswith("FAIL")]
    return {"autocrlf": autocrlf, "passed": done.returncode == 0, "failureCount": len(failures), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    commit = subprocess.run(["git", "rev-parse", args.rev], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    result = {"rev": args.rev, "commit": commit,
              "modes": {"windowsDefaultClone": run_mode(commit, "true"), "lfClone": run_mode(commit, "false")}}
    for name, mode in result["modes"].items():
        print(f"{name} (core.autocrlf={mode['autocrlf']}): {'PASS' if mode['passed'] else 'FAIL'} ({mode['failureCount']} failures)")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(m["passed"] for m in result["modes"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
