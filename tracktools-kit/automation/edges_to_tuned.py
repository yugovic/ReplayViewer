#!/usr/bin/env python3
"""Bridge the track-creator editor's edge data into the kit's tuned.json.

track-creator/tracks/<track>/track.json carries road.edges: per-side lateral
offsets every `step` metres, authored in the web editor (image analysis +
hand fixes). The headless Blender build consumes tuned.json "widths"
(dist-keyed, linearly interpolated) for both the Track Tools visual road
(via per-point curve radius) and the native collision ribbon.

Per station this writes width = left + right AND center = (left - right) / 2
(signed lateral displacement of the real road's midline from the survey
centerline, positive toward the driver's LEFT). The build shifts each
centerline point by `center` before extruding symmetrically, so the built
road reproduces the asymmetric edges the editor shows over the photo.
Measured on fuji: |center| > 2 m over 66% of the lap (pit straight ~ -5 m),
so dropping it visibly misplaces the road against the satellite imagery.

Usage:
    python edges_to_tuned.py --track fuji
    (paths can be overridden with --track-json / --kit)
"""

from __future__ import annotations

import argparse
import json
import os

AUTOMATION_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(AUTOMATION_DIR, "..", ".."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", default="fuji")
    ap.add_argument("--track-json", default=None,
                    help="path to track-creator's track.json (default: track-creator/tracks/<track>/track.json)")
    ap.add_argument("--kit", default=None,
                    help="kit directory holding tuned.json (default: tracktools-kit/<track>)")
    ap.add_argument("--base-width", type=float, default=13.0,
                    help="ROAD base width the per-point radii scale from")
    args = ap.parse_args()

    track_json = args.track_json or os.path.join(
        REPO, "track-creator", "tracks", args.track, "track.json")
    kit = args.kit or os.path.join(REPO, "tracktools-kit", args.track)
    tuned_path = os.path.join(kit, "tuned.json")

    tj = json.load(open(track_json, encoding="utf-8"))
    edges = tj.get("road", {}).get("edges")
    if not edges:
        print(f"[edges->tuned] {track_json} has no road.edges — nothing to bridge")
        return 1
    step = float(edges["step"])
    left = edges["left"]
    right = edges["right"]
    n = min(len(left), len(right))

    widths = [
        {"dist": round(i * step, 2), "width": round(float(left[i]) + float(right[i]), 2)}
        for i in range(n)
    ]
    centers = [
        {"dist": round(i * step, 2), "center": round((float(left[i]) - float(right[i])) / 2.0, 2)}
        for i in range(n)
    ]

    tuned = {}
    if os.path.exists(tuned_path):
        tuned = json.load(open(tuned_path, encoding="utf-8"))
    tuned.setdefault("road", {})["width_m"] = args.base_width
    tuned["widths"] = widths
    tuned["centers"] = centers
    tuned["widths_source"] = {
        "from": os.path.relpath(track_json, REPO).replace(os.sep, "/"),
        "step_m": step,
        "stations": n,
    }

    with open(tuned_path, "w", encoding="utf-8") as f:
        json.dump(tuned, f, ensure_ascii=False, indent=1)

    ws = sorted(w["width"] for w in widths)
    cs = sorted(c["center"] for c in centers)
    print(f"[edges->tuned] {n} stations @ {step} m -> {tuned_path}")
    print(f"[edges->tuned] width  min {ws[0]:.1f} / median {ws[n // 2]:.1f} / max {ws[-1]:.1f} m"
          f" (base {args.base_width} m)")
    print(f"[edges->tuned] center min {cs[0]:+.1f} / median {cs[n // 2]:+.1f} / max {cs[-1]:+.1f} m"
          f" (+ = driver's left)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
