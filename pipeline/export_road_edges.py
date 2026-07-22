"""Export a track-creator edge profile for the Replay vector line layer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_profile(source: Path, output: Path) -> dict:
    raw = source.read_bytes()
    document = json.loads(raw)
    edges = (document.get("road") or {}).get("edges") or {}
    left, right, step = edges.get("left"), edges.get("right"), edges.get("step")
    if not isinstance(step, (int, float)) or step <= 0:
        raise ValueError("road.edges.step must be positive")
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right) or len(left) < 2:
        raise ValueError("road.edges.left/right must be equal non-trivial arrays")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in left + right):
        raise ValueError("road edge offsets must be positive numbers")
    profile = {
        "version": 1,
        "stepMeters": step,
        "totalLength": (
            json.loads((source.parent / "elevation.json").read_text(encoding="utf-8")).get("totalLength")
            if (source.parent / "elevation.json").exists()
            else step * len(left)
        ),
        "left": left,
        "right": right,
        "interpretation": "track-line-center",
        "lineInsetMeters": 0,
        "source": source.relative_to(ROOT).as_posix(),
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "purpose": "visual vector overlay; not surveyed truth",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", default="fuji")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source or ROOT / "track-creator" / "tracks" / args.track / "track.json"
    output = args.output or ROOT / "public" / "data" / "tracks" / args.track / "road_edges.json"
    profile = export_profile(source.resolve(), output.resolve())
    print(f"Exported {len(profile['left'])} edge stations to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
