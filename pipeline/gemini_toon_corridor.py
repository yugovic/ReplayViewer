"""Toon-convert the track corridor with Gemini and mount it as trial tiles.

Extends the 12-tile pilot (gemini_toon_pilot.py, judged PASS on 2026-07-22)
to a continuous corridor: 512 px (102.4 m) windows every 76.8 m along the
centerline (128 px overlap), each converted by gemini-3.1-flash-image with
the logo-free style reference, gated by edge-overlap, feathered, and written
into the imagegen_trials override manifest (visualization only).

Usage:
  python gemini_toon_corridor.py prepare          # cut source windows
  python gemini_toon_corridor.py run              # convert + gate + retry
  python gemini_toon_corridor.py finalize         # feather + manifest
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from imagegen_pilot import (  # noqa: E402
    DEFAULT_MOSAIC,
    DEFAULT_MOSAIC_META,
    DEFAULT_TRACK_DEF,
    load_track,
    local_to_pixel,
    point_at_dist,
)
from gemini_toon_pilot import PROMPT, api_key, call_gemini  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TRIALS_DIR = REPO_ROOT / "public" / "data" / "tracks" / "fuji" / "imagegen_trials"
OUT_DIR = TRIALS_DIR / "toon_corridor"
CROP = 512
STEP_M = 76.8          # 512px*0.2m = 102.4m footprint -> 128px overlap
FEATHER_PX = 96        # in 1024px output space (= 48px source px = 9.6m)
GATE_MIN_OVERLAP = 0.90
MAX_RETRIES = 1


def edge_overlap(src_path: Path, gen_png: bytes) -> float:
    src = cv2.imread(str(src_path), cv2.IMREAD_GRAYSCALE)
    gen = cv2.imdecode(np.frombuffer(gen_png, np.uint8), cv2.IMREAD_GRAYSCALE)
    s = 512
    src = cv2.resize(src, (s, s))
    gen = cv2.resize(gen, (s, s))
    es = cv2.Canny(src, 60, 140)
    eg = cv2.Canny(gen, 60, 140)
    return float((eg & cv2.dilate(es, np.ones((5, 5), np.uint8))).sum() / max(eg.sum(), 1))


def cmd_prepare() -> None:
    meta = json.loads(DEFAULT_MOSAIC_META.read_text(encoding="utf-8"))
    track, points = load_track(DEFAULT_TRACK_DEF)
    total = points[-1][2]
    image = np.asarray(Image.open(DEFAULT_MOSAIC).convert("RGB"))
    height, width = image.shape[:2]

    (OUT_DIR / "src").mkdir(parents=True, exist_ok=True)
    windows = []
    n = int(math.floor(total / STEP_M))
    for i in range(n):
        dist = i * STEP_M
        x, z = point_at_dist(points, dist)
        px, py = local_to_pixel(x, z, track["origin"], meta)
        x0 = max(0, min(width - CROP, int(round(px)) - CROP // 2))
        y0 = max(0, min(height - CROP, int(round(py)) - CROP // 2))
        name = f"corridor_{i:03d}"
        Image.fromarray(image[y0:y0 + CROP, x0:x0 + CROP]).save(OUT_DIR / "src" / f"{name}_src.png")
        windows.append({
            "id": name,
            "distM": round(dist, 1),
            "rectPixel": {"x": x0, "y": y0, "w": CROP, "h": CROP},
            "uv": {"u0": x0 / width, "v0": y0 / height,
                   "u1": (x0 + CROP) / width, "v1": (y0 + CROP) / height},
        })
    (OUT_DIR / "windows.json").write_text(json.dumps({
        "stepM": STEP_M, "cropPx": CROP, "count": len(windows),
        "source": meta.get("source"), "license": meta.get("license"),
        "windows": windows,
    }, indent=1), encoding="utf-8")
    print(f"prepared {len(windows)} windows (lap {total:.0f} m)")


def cmd_run(model: str, limit: int | None) -> None:
    key = api_key()
    windows = json.loads((OUT_DIR / "windows.json").read_text())["windows"]
    if limit:
        windows = windows[:limit]
    (OUT_DIR / "gen").mkdir(exist_ok=True)
    log_path = OUT_DIR / "run_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"calls": []}
    done = 0
    for w in windows:
        out = OUT_DIR / "gen" / f"{w['id']}_gen.png"
        if out.exists():
            continue
        src = OUT_DIR / "src" / f"{w['id']}_src.png"
        best: tuple[float, bytes] | None = None
        for attempt in range(1 + MAX_RETRIES):
            png, info = call_gemini(key, model, src)
            info.update({"id": w["id"], "attempt": attempt})
            if png is not None:
                ov = edge_overlap(src, png)
                info["edgeOverlap"] = round(ov, 3)
                if best is None or ov > best[0]:
                    best = (ov, png)
            log["calls"].append(info)
            log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
            if png is not None and info["edgeOverlap"] >= GATE_MIN_OVERLAP:
                break
            time.sleep(1.0)
        if best is None:
            print(f"{w['id']} FAILED entirely")
            continue
        out.write_bytes(best[1])
        done += 1
        print(f"{w['id']} ok overlap={best[0]:.2f} ({done} done)")


def cmd_finalize() -> None:
    windows = json.loads((OUT_DIR / "windows.json").read_text())["windows"]
    log = json.loads((OUT_DIR / "run_log.json").read_text())
    overlap_by_id: dict[str, float] = {}
    for c in log["calls"]:
        if "edgeOverlap" in c:
            overlap_by_id[c["id"]] = max(overlap_by_id.get(c["id"], 0.0), c["edgeOverlap"])

    (OUT_DIR / "tiles").mkdir(exist_ok=True)
    tiles = []
    for w in windows:
        gen = OUT_DIR / "gen" / f"{w['id']}_gen.png"
        if not gen.exists():
            continue
        im = cv2.imread(str(gen), cv2.IMREAD_COLOR)
        h, ww = im.shape[:2]
        # Linear border feather so neighbours (and the photo ground beyond
        # the corridor ends) cross-fade instead of hard-seaming.
        f = FEATHER_PX
        ramp = np.ones((h, ww), np.float32)
        lin = np.linspace(0.0, 1.0, f, dtype=np.float32)
        ramp[:f, :] = np.minimum(ramp[:f, :], lin[:, None])
        ramp[-f:, :] = np.minimum(ramp[-f:, :], lin[::-1][:, None])
        ramp[:, :f] = np.minimum(ramp[:, :f], lin[None, :])
        ramp[:, -f:] = np.minimum(ramp[:, -f:], lin[::-1][None, :])
        rgba = cv2.cvtColor(im, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = (ramp * 255).astype(np.uint8)
        out_name = f"{w['id']}.png"
        cv2.imwrite(str(OUT_DIR / "tiles" / out_name), rgba)
        tiles.append({
            "file": f"toon_corridor/tiles/{out_name}",
            "outputPixel": {"width": ww, "height": h},
            "uv": w["uv"],
            "priority": 10,
            "provenance": {
                "kind": "generative-edit toon style (Gemini gemini-3.1-flash-image API)",
                "sourceWindow": f"src/{w['id']}_src.png",
                "styleRef": "ai_illustrated_tile_nologo.png",
                "edgeOverlap": overlap_by_id.get(w["id"]),
                "featherPx": FEATHER_PX,
                "visualizationOnly": True,
            },
        })

    manifest_path = TRIALS_DIR / "manifest.json"
    old = json.loads(manifest_path.read_text())
    manifest = {
        "schemaVersion": 1,
        "kind": "track-corridor-enhancement",
        "visualizationOnly": True,
        "note": (f"Gemini toon corridor: {len(tiles)} windows every {STEP_M} m, "
                 f"edge-overlap gated >= {GATE_MIN_OVERLAP}. Visualization only."),
        "bbox": old["bbox"],
        "mercator": old.get("mercator", True),
        "tileCount": len(tiles),
        "tiles": tiles,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    lows = sorted((v, k) for k, v in overlap_by_id.items())[:5]
    print(f"manifest: {len(tiles)} tiles. lowest overlaps: {lows}")


GRASS_BGR = np.array([37, 207, 188], np.float32)  # #BCCF25 (median of window 034)


# Windows where even olive-green (hue 35-50) gets unified — the Coca-Cola
# apron area, per user review 2026-07-22. Not global: elsewhere that range
# includes real green-painted runoff and trees that should keep their tone.
EXTENDED_SNAP_IDS = {"corridor_018", "corridor_019", "corridor_020", "corridor_021", "corridor_100", "corridor_101", "corridor_027", "corridor_028", "corridor_029", "corridor_200", "corridor_201", "corridor_202"}


def snap_grass_palette(im: np.ndarray, extended: bool = False) -> np.ndarray:
    """Deterministic color unification: any saturated green (hue 50-85) —
    stray dark-green grass, curb-back turf strips — is remapped to the
    standard yellow-green #BCCF25, scaled by relative lightness so shading
    survives. Prompt-level color pinning proved only ~90% reliable per
    window (2026-07-22); this guarantees the rest."""
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    # >=45 (not >50): window 028's apron came out at hue EXACTLY 50 and
    # slipped between this mask and the olive branch below for several
    # rebuilds. Keep the ranges overlapping, never touching.
    hue_lo = 35 if extended else 45
    mask = (hsv[:, :, 0] >= hue_lo) & (hsv[:, :, 0] < 90) & (hsv[:, :, 1] > 80)
    out = im.copy()
    if mask.any():
        v = hsv[:, :, 2].astype(np.float32) / 255.0
        ref_v = float(GRASS_BGR.max()) / 255.0
        scaled = np.clip(GRASS_BGR[None, :] * (v[mask][:, None] / ref_v), 0, 255)
        out[mask] = scaled.astype(np.uint8)
    # Dull-olive shadow patches Gemini paints on grass (same hue family as
    # the standard grass but darker/desaturated) read as "green" next to the
    # bright yellow-green and were the user-visible artifact at t=52. They
    # are large FLAT shapes, so a local-flatness guard keeps textured things
    # (tree canopies) untouched. These are flattened to the exact grass
    # color — value-preserving mapping kept them visibly dark.
    olive = (hsv[:, :, 0] > 25) & (hsv[:, :, 0] < 50) & \
            (hsv[:, :, 1] > 95) & (hsv[:, :, 1] < 200) & \
            (hsv[:, :, 2] > 70) & (hsv[:, :, 2] < 205)
    if olive.any():
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
        flat = np.abs(gray - cv2.blur(gray, (9, 9))) < 6.0
        olive &= flat
        out[olive] = GRASS_BGR.astype(np.uint8)
    return out


def cmd_stitch() -> None:
    """Blend all gen windows into one corridor layer offline, then cut
    non-overlapping opaque tiles. Fixes the dark seams: the runtime feather
    let the photo ground bleed through wherever tiles overlapped; here the
    cross-fade happens toon-to-toon before anything reaches the GPU, and the
    only remaining alpha ramp is the corridor's outer boundary."""
    meta = json.loads(DEFAULT_MOSAIC_META.read_text(encoding="utf-8"))
    windows = json.loads((OUT_DIR / "windows.json").read_text())["windows"]
    scale = 2  # gen tiles are 1024 px for 512 source px
    W, H = meta["imageWidth"] * scale, meta["imageHeight"] * scale

    # Bounding box of the corridor in scaled mosaic pixels.
    xs0 = min(w["rectPixel"]["x"] for w in windows) * scale
    ys0 = min(w["rectPixel"]["y"] for w in windows) * scale
    xs1 = max(w["rectPixel"]["x"] + CROP for w in windows) * scale
    ys1 = max(w["rectPixel"]["y"] + CROP for w in windows) * scale
    cw, ch = xs1 - xs0, ys1 - ys0
    print(f"corridor canvas {cw}x{ch} @2x")

    acc = np.zeros((ch, cw, 3), np.float32)
    wsum = np.zeros((ch, cw), np.float32)
    size = CROP * scale
    f = FEATHER_PX
    ramp1d = np.ones(size, np.float32)
    ramp1d[:f] = np.linspace(0.0, 1.0, f)
    ramp1d[-f:] = np.linspace(1.0, 0.0, f)
    wtile = np.minimum(ramp1d[:, None], ramp1d[None, :])
    for w in windows:
        gen = OUT_DIR / "gen" / f"{w['id']}_gen.png"
        if not gen.exists():
            continue
        im = cv2.imread(str(gen), cv2.IMREAD_COLOR)
        if im.shape[:2] != (size, size):
            im = cv2.resize(im, (size, size), interpolation=cv2.INTER_AREA)
        im = snap_grass_palette(im, extended=w["id"] in EXTENDED_SNAP_IDS)
        x0 = w["rectPixel"]["x"] * scale - xs0
        y0 = w["rectPixel"]["y"] * scale - ys0
        acc[y0:y0 + size, x0:x0 + size] += im.astype(np.float32) * wtile[:, :, None]
        wsum[y0:y0 + size, x0:x0 + size] += wtile

    rgb = (acc / np.maximum(wsum, 1e-6)[:, :, None]).clip(0, 255).astype(np.uint8)
    # Steepened alpha: weight>=1/3 renders fully opaque. Apex areas covered
    # only by window-edge ramps otherwise stay translucent and let the photo
    # ground (real green turf etc.) bleed through mid-corridor.
    alpha = (np.clip(wsum * 8.0, 0.0, 1.0) * 255).astype(np.uint8)
    del acc

    # Cut an opaque, non-overlapping 1024 px grid over the corridor coverage.
    (OUT_DIR / "tiles2").mkdir(exist_ok=True)
    tiles = []
    cell = 1024
    for gy in range(0, ch, cell):
        for gx in range(0, cw, cell):
            a = alpha[gy:gy + cell, gx:gx + cell]
            if a.max() == 0:
                continue
            rgba = cv2.cvtColor(rgb[gy:gy + cell, gx:gx + cell], cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = a
            name = f"grid_{gy // cell:02d}_{gx // cell:02d}.png"
            cv2.imwrite(str(OUT_DIR / "tiles2" / name), rgba)
            u0 = (xs0 + gx) / W
            v0 = (ys0 + gy) / H
            tiles.append({
                "file": f"toon_corridor/tiles2/{name}",
                "outputPixel": {"width": rgba.shape[1], "height": rgba.shape[0]},
                "uv": {"u0": u0, "v0": v0,
                       "u1": u0 + rgba.shape[1] / W, "v1": v0 + rgba.shape[0] / H},
                "priority": 10,
                "provenance": {
                    "kind": "generative-edit toon style (Gemini gemini-3.1-flash-image API), offline-stitched",
                    "styleRef": "ai_illustrated_tile_nologo.png",
                    "featherPx": FEATHER_PX,
                    "visualizationOnly": True,
                },
            })

    manifest_path = TRIALS_DIR / "manifest.json"
    old = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({
        "schemaVersion": 1,
        "kind": "track-corridor-enhancement",
        "visualizationOnly": True,
        "note": f"Gemini toon corridor, offline-stitched into {len(tiles)} opaque grid tiles. Visualization only.",
        "bbox": old["bbox"],
        "mercator": old.get("mercator", True),
        "tileCount": len(tiles),
        "tiles": tiles,
    }, indent=2), encoding="utf-8")
    print(f"stitched manifest: {len(tiles)} grid tiles")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    run = sub.add_parser("run")
    run.add_argument("--model", default="gemini-3.1-flash-image")
    run.add_argument("--limit", type=int, default=None)
    sub.add_parser("finalize")
    sub.add_parser("stitch")
    args = ap.parse_args()
    if args.cmd == "prepare":
        cmd_prepare()
    elif args.cmd == "run":
        cmd_run(args.model, args.limit)
    elif args.cmd == "stitch":
        cmd_stitch()
    else:
        cmd_finalize()


if __name__ == "__main__":
    main()
