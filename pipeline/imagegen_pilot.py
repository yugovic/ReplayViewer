#!/usr/bin/env python3
"""ImageGen 12-tile pilot kit: prepare source crops, evaluate returned images.

The pilot tests whether a generative editor (ChatGPT / Gemini app, manual
upload) can enhance our CC BY 4.0 orthophoto crops WITHOUT moving or
inventing anything — judged by the same fidelity gates the SR pipeline uses.
Google Map Tiles are display-only and are never part of this workflow.

Usage:

  # 1) cut the 12 source crops (graded native mosaic, 0.20 m/px, 512 px = 102.4 m)
  python pipeline/imagegen_pilot.py prepare

  # 2) generate manually: upload pilot_XX_src.png + PROMPT.txt text to the
  #    editor, save the result as pilot_XX_gen.png in the same folder

  # 3) evaluate everything that has a _gen image (venv python for IQA):
  pipeline/.venv-sr/Scripts/python.exe pipeline/imagegen_pilot.py evaluate
  #    (add --no-iqa to run gates only, without torch)

Pass gates (fixed BEFORE generation, per docs/evaluation-imagegen-fidelity):
  LR-consistency >= 30 dB / block shift p95 <= 0.5 px (=10 cm) /
  mean colour delta <= 8 (RGB norm) / contrast ratio 0.8..1.2.
Adoption policy stays: on-track spots are fidelity probes; anything applied
to the Replay goes off-corridor first, with provenance recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
PILOT_DIR = REPO_ROOT / "public" / "data" / "tracks" / "fuji" / "imagegen_trials" / "pilot"
DEFAULT_MOSAIC = REPO_ROOT / "pipeline" / "cache" / "shizuoka_ortho" / "native_mosaic_graded.png"
DEFAULT_MOSAIC_META = REPO_ROOT / "pipeline" / "cache" / "shizuoka_ortho" / "native_mosaic_graded_meta.json"
DEFAULT_TRACK_DEF = REPO_ROOT / "track-creator" / "tracks" / "fuji" / "track.json"
CROP = 512  # px @0.20 m/px = 102.4 m — same footprint as one SR corridor cell

# On-track probe fractions of lap length (plus the 1519.2 m spot already used
# by the earlier trial, kept for comparability) and off-track offsets.
ON_TRACK_FRACTIONS = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]
FIXED_DIST_M = [1519.2]
OFF_TRACK = [(0.05, 70.0), (0.45, 70.0), (0.80, -70.0)]  # (fraction, lateral m; +=right)

PROMPT_TEXT = """\
[JA] この航空写真を、写っている内容を一切変えずに高解像度化・鮮明化してください。
厳守事項:
- 道路・白線・縁石・路面標示・建物・フェンスの位置と形状を1ピクセルも動かさない
- 新しい物体・模様・文字・車両・影を追加しない/既存物を消さない
- 構図・縮尺・回転を変えない（出力は入力と同じ範囲の正方形）
- 色調・明るさは入力に厳密に合わせる（グレーディング済みのため変更不要）
- 質感（アスファルトの粒、芝、砂利）のみを自然に精細化する
出力はできるだけ高解像度（2048px以上推奨）でお願いします。

[EN] Upscale and sharpen this aerial photo WITHOUT changing its content.
Strict rules: do not move or reshape any road, line, curb, marking, building
or fence by even one pixel; do not add or remove any object, pattern, text,
vehicle or shadow; keep composition, scale and rotation identical (square
output, same extent); match the input's colour and brightness exactly; only
refine the natural textures (asphalt grain, grass, gravel). Highest possible
resolution (2048px+) please.
"""


def load_track(track_def: Path) -> tuple[dict, list[tuple[float, float, float]]]:
    """Returns (track, points) where points = (x, z, cumulative_dist)."""
    track = json.loads(track_def.read_text(encoding="utf-8"))
    pts = [(p["x"], p["z"]) for p in track["controlPoints"]]
    if track.get("closed", True) and pts:
        pts.append(pts[0])
    out: list[tuple[float, float, float]] = []
    dist = 0.0
    for i, (x, z) in enumerate(pts):
        if i > 0:
            dist += math.hypot(x - pts[i - 1][0], z - pts[i - 1][1])
        out.append((x, z, dist))
    return track, out


def point_at_dist(points: list[tuple[float, float, float]], dist: float,
                  lateral: float = 0.0) -> tuple[float, float]:
    """(x, z) at arc length `dist`, offset `lateral` metres (+ = driver's right)."""
    total = points[-1][2]
    dist = dist % total if total > 0 else 0.0
    for i in range(len(points) - 1):
        x0, z0, d0 = points[i]
        x1, z1, d1 = points[i + 1]
        if d0 <= dist <= d1:
            t = (dist - d0) / (d1 - d0) if d1 > d0 else 0.0
            x = x0 + (x1 - x0) * t
            z = z0 + (z1 - z0) * t
            seg = math.hypot(x1 - x0, z1 - z0)
            if lateral != 0.0 and seg > 0:
                tx, tz = (x1 - x0) / seg, (z1 - z0) / seg
                # driver's right = (-tz, tx) under the scene's T×O convention
                x += -tz * lateral
                z += tx * lateral
            return x, z
    return points[-1][0], points[-1][1]


def local_to_pixel(x: float, z: float, origin: dict, meta: dict) -> tuple[float, float]:
    """Match src/replay/projection.ts (same maths as enhance_track_corridor)."""
    metres_per_degree = 111_320.0
    lng_scale = metres_per_degree * math.cos(math.radians(origin["lat"]))
    lat = origin["lat"] - z / metres_per_degree
    lng = origin["lng"] + x / lng_scale
    bbox = meta["bbox"]
    u = (lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"])
    y_north = math.asinh(math.tan(math.radians(bbox["maxLat"])))
    y_south = math.asinh(math.tan(math.radians(bbox["minLat"])))
    v = (y_north - math.asinh(math.tan(math.radians(lat)))) / (y_north - y_south)
    return u * (meta["imageWidth"] - 1), v * (meta["imageHeight"] - 1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def cmd_prepare(args: argparse.Namespace) -> int:
    meta = json.loads(args.mosaic_meta.read_text(encoding="utf-8"))
    track, points = load_track(args.track_def)
    total = points[-1][2]
    image = np.asarray(Image.open(args.mosaic).convert("RGB"))
    height, width = image.shape[:2]
    if (width, height) != (meta["imageWidth"], meta["imageHeight"]):
        raise SystemExit("mosaic/meta size mismatch")

    spots: list[dict] = []
    for frac in ON_TRACK_FRACTIONS:
        spots.append({"kind": "on-track", "distM": round(frac * total, 1), "lateralM": 0.0})
    for dist in FIXED_DIST_M:
        spots.append({"kind": "on-track (既存トライアル地点)", "distM": dist, "lateralM": 0.0})
    for frac, lateral in OFF_TRACK:
        spots.append({"kind": "off-track", "distM": round(frac * total, 1), "lateralM": lateral})

    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    thumbs = []
    for i, spot in enumerate(spots, start=1):
        x, z = point_at_dist(points, spot["distM"], spot["lateralM"])
        px, py = local_to_pixel(x, z, track["origin"], meta)
        x0 = int(round(px)) - CROP // 2
        y0 = int(round(py)) - CROP // 2
        x0 = max(0, min(width - CROP, x0))
        y0 = max(0, min(height - CROP, y0))
        crop = image[y0:y0 + CROP, x0:x0 + CROP]
        name = f"pilot_{i:02d}_src.png"
        Image.fromarray(crop).save(PILOT_DIR / name, "PNG")
        index.append({
            "id": f"{i:02d}",
            "source": name,
            "kind": spot["kind"],
            "distM": spot["distM"],
            "lateralM": spot["lateralM"],
            "rectPixel": {"x": x0, "y": y0, "w": CROP, "h": CROP},
            "uv": {"u0": x0 / width, "v0": y0 / height,
                   "u1": (x0 + CROP) / width, "v1": (y0 + CROP) / height},
            "groundMetersPerSide": round(CROP * meta["effectiveResolutionMetersPerPixel"], 1),
            "sha256": sha256_of(PILOT_DIR / name),
        })
        thumb = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA)
        thumbs.append((f"{i:02d} {spot['kind']} d={spot['distM']:.0f}m", thumb))

    (PILOT_DIR / "PROMPT.txt").write_text(PROMPT_TEXT, encoding="utf-8")
    (PILOT_DIR / "index.json").write_text(json.dumps({
        "purpose": "ImageGen fidelity pilot (12 crops). Visualization research only.",
        "source": meta.get("source"),
        "license": meta.get("license"),
        "sourceImage": args.mosaic.name,
        "toneEnhanced": meta.get("toneEnhanced"),
        "metersPerPixel": meta.get("effectiveResolutionMetersPerPixel"),
        "gates": {"lrConsistencyMinPsnrDb": 30.0, "blockShiftMaxP95Px": 0.5,
                  "meanColorDeltaMax": 8.0, "contrastRatioRange": [0.8, 1.2]},
        "spots": index,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Contact sheet 4x3 with labels.
    cols, rows, cell, pad = 4, 3, 256, 28
    sheet = Image.new("RGB", (cols * cell, rows * (cell + pad)), "#101418")
    draw = ImageDraw.Draw(sheet)
    for i, (label, thumb) in enumerate(thumbs):
        cx, cy = (i % cols) * cell, (i // cols) * (cell + pad)
        sheet.paste(Image.fromarray(thumb), (cx, cy + pad))
        draw.text((cx + 6, cy + 7), label, fill="white")
    sheet.save(PILOT_DIR / "contact_sheet.jpg", quality=90)

    # Minimal picker page for the manual upload workflow.
    rows_html = "\n".join(
        f'<figure><img src="{s["source"]}"><figcaption>#{s["id"]} {s["kind"]}'
        f' / d={s["distM"]}m / 保存名: pilot_{s["id"]}_gen.png</figcaption></figure>'
        for s in index)
    (PILOT_DIR / "index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>ImageGen pilot sources</title>
<style>body{{background:#111;color:#eee;font:14px sans-serif;margin:20px}}
figure{{display:inline-block;margin:8px;width:264px}}img{{width:256px;image-rendering:pixelated}}
figcaption{{font-size:12px;color:#bbb}}pre{{white-space:pre-wrap;background:#1c2228;padding:12px}}</style>
<h1>ImageGen 12枚パイロット — ソース画像</h1>
<p>各画像を生成ツールへアップロードし、下のプロンプトで編集。結果はこのフォルダへ
<b>pilot_XX_gen.png</b>（XXは番号）として保存 → <code>pipeline/imagegen_pilot.py evaluate</code>。</p>
<pre>{PROMPT_TEXT}</pre>
{rows_html}
""", encoding="utf-8")

    print(f"prepared {len(index)} crops -> {PILOT_DIR}")
    print("contact sheet:", PILOT_DIR / "contact_sheet.jpg")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from enhance_track_corridor import corridor_shift_stats, lr_consistency_psnr, shift_probe_points

    index = json.loads((PILOT_DIR / "index.json").read_text(encoding="utf-8"))
    gates = index["gates"]
    metrics = None
    if not args.no_iqa:
        from enhance_track_corridor import PerceptualMetrics
        metrics = PerceptualMetrics("cuda")

    results = []
    for spot in index["spots"]:
        gen_path = None
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = PILOT_DIR / f"pilot_{spot['id']}_gen{ext}"
            if candidate.exists():
                gen_path = candidate
                break
        if gen_path is None:
            continue
        src = np.asarray(Image.open(PILOT_DIR / spot["source"]).convert("RGB"))
        gen = np.asarray(Image.open(gen_path).convert("RGB"))
        if gen.shape[0] != gen.shape[1]:  # centre-crop to square, warn
            side = min(gen.shape[:2])
            oy, ox = (gen.shape[0] - side) // 2, (gen.shape[1] - side) // 2
            gen = gen[oy:oy + side, ox:ox + side]
            print(f"  WARN {gen_path.name}: non-square output, centre-cropped")
        gen512 = cv2.resize(gen, (CROP, CROP), interpolation=cv2.INTER_AREA)

        lr_db = lr_consistency_psnr(cv2.resize(gen, (CROP * 2, CROP * 2),
                                               interpolation=cv2.INTER_AREA), src) \
            if gen.shape[0] >= CROP * 2 else lr_consistency_psnr(
                cv2.resize(gen512, (CROP * 2, CROP * 2), interpolation=cv2.INTER_LANCZOS4), src)
        shifts = corridor_shift_stats(src, gen512, shift_probe_points(CROP, 128))
        d_mean = gen512.reshape(-1, 3).mean(axis=0) - src.reshape(-1, 3).mean(axis=0)
        contrast_ratio = float(gen512.std() / max(src.std(), 1e-6))

        checks = {
            "lrConsistencyDb": round(float(lr_db), 2),
            "blockShiftP95Px": shifts["p95Px"],
            "meanColorDelta": round(float(np.linalg.norm(d_mean)), 2),
            "contrastRatio": round(contrast_ratio, 3),
        }
        passed = (checks["lrConsistencyDb"] >= gates["lrConsistencyMinPsnrDb"]
                  and checks["blockShiftP95Px"] is not None
                  and checks["blockShiftP95Px"] <= gates["blockShiftMaxP95Px"]
                  and checks["meanColorDelta"] <= gates["meanColorDeltaMax"]
                  and gates["contrastRatioRange"][0] <= checks["contrastRatio"]
                  <= gates["contrastRatioRange"][1])
        iqa = {}
        if metrics is not None:
            up = cv2.resize(src, (CROP * 2, CROP * 2), interpolation=cv2.INTER_LANCZOS4)
            gen1024 = cv2.resize(gen, (CROP * 2, CROP * 2), interpolation=cv2.INTER_AREA) \
                if gen.shape[0] >= CROP * 2 else cv2.resize(gen512, (CROP * 2, CROP * 2),
                                                            interpolation=cv2.INTER_LANCZOS4)
            iqa = {"lanczos2x": {k: round(v, 3) for k, v in metrics.no_reference(up).items()},
                   "generated": {k: round(v, 3) for k, v in metrics.no_reference(gen1024).items()}}

        result = {"id": spot["id"], "generated": gen_path.name, "genSize": list(gen.shape[:2]),
                  "checks": checks, "gates": gates, "passed": passed, "iqa": iqa}
        (PILOT_DIR / f"pilot_{spot['id']}_eval.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        panel = np.concatenate([cv2.resize(src, (512, 512)), gen512], axis=1)
        Image.fromarray(panel).save(PILOT_DIR / f"pilot_{spot['id']}_panel.jpg", quality=90)
        results.append(result)
        print(f"  #{spot['id']} {'PASS' if passed else 'FAIL'}  {checks}"
              + (f"  MUSIQ {iqa['lanczos2x']['musiq']}->{iqa['generated']['musiq']}" if iqa else ""))

    if not results:
        print("no pilot_XX_gen.* images found in", PILOT_DIR)
        return 1
    passed = sum(1 for r in results if r["passed"])
    print(f"evaluated {len(results)} images: {passed} PASS / {len(results) - passed} FAIL")
    return 0


def color_match(gen: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Per-channel mean/std transfer onto the source statistics (deterministic,
    position-preserving). Fixes the systematic re-grading most generators apply
    so the tile blends with the surrounding graded imagery."""
    out = gen.astype(np.float32)
    for c in range(3):
        gm, gs = float(out[..., c].mean()), float(out[..., c].std())
        sm, ss = float(src[..., c].mean()), float(src[..., c].std())
        if gs > 1e-6:
            out[..., c] = (out[..., c] - gm) / gs * ss + sm
    return np.clip(out, 0, 255).astype(np.uint8)


def edge_feather_alpha(size: int, feather: int) -> np.ndarray:
    """Linear alpha ramp on all four edges (255 core)."""
    ramp = np.minimum(np.arange(size) + 1, feather) / feather
    alpha = np.minimum.outer(ramp, ramp)
    alpha = np.minimum(alpha, np.flip(alpha, axis=0))
    alpha = np.minimum(alpha, np.flip(alpha, axis=1))
    return np.round(alpha * 255).astype(np.uint8)


def cmd_apply(args: argparse.Namespace) -> int:
    """Build colour-matched, edge-feathered RGBA tiles from pilot_XX_gen.* and
    write them into the Replay override manifest (imagegen_trials/manifest.json,
    original backed up as manifest.pre-pilot.json)."""
    from enhance_track_corridor import corridor_shift_stats, lr_consistency_psnr, shift_probe_points

    import time

    trials_dir = PILOT_DIR.parent
    index = json.loads((PILOT_DIR / "index.json").read_text(encoding="utf-8"))
    # Cache-busting stamp — see enhance_track_corridor.run_tiles for why.
    stamp = time.strftime("%Y%m%dT%H%M%S")
    entries = []
    for spot in index["spots"]:
        gen_path = None
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = PILOT_DIR / f"pilot_{spot['id']}_gen{ext}"
            if candidate.exists():
                gen_path = candidate
                break
        if gen_path is None:
            continue
        src = np.asarray(Image.open(PILOT_DIR / spot["source"]).convert("RGB"))
        gen = np.asarray(Image.open(gen_path).convert("RGB"))
        if gen.shape[0] != gen.shape[1]:
            side = min(gen.shape[:2])
            oy, ox = (gen.shape[0] - side) // 2, (gen.shape[1] - side) // 2
            gen = gen[oy:oy + side, ox:ox + side]
        matched = color_match(gen, src) if not args.no_color_match else gen

        # Post colour-match gate numbers, recorded for honesty in the manifest.
        m512 = cv2.resize(matched, (CROP, CROP), interpolation=cv2.INTER_AREA)
        lr_db = lr_consistency_psnr(cv2.resize(matched, (CROP * 2, CROP * 2),
                                               interpolation=cv2.INTER_AREA), src)
        shifts = corridor_shift_stats(src, m512, shift_probe_points(CROP, 128))
        d_mean = float(np.linalg.norm(m512.reshape(-1, 3).mean(axis=0)
                                      - src.reshape(-1, 3).mean(axis=0)))

        alpha = edge_feather_alpha(matched.shape[0], args.feather)
        tile = np.dstack([matched, alpha])
        tile_name = f"pilot/pilot_{spot['id']}_tile.png"
        Image.fromarray(tile, "RGBA").save(trials_dir / tile_name, "PNG")
        entries.append({
            "file": f"{tile_name}?v={stamp}",
            "outputPixel": {"width": matched.shape[1], "height": matched.shape[0]},
            "uv": spot["uv"],
            "priority": 10,
            "provenance": {
                "kind": "generative-edit pilot (manual app upload by user)",
                "sourceCrop": spot["source"],
                "sourceSha256": spot["sha256"],
                "generatedFile": gen_path.name,
                "colorMatched": not args.no_color_match,
                "featherPx": args.feather,
                "postMatchChecks": {"lrConsistencyDb": round(float(lr_db), 2),
                                    "blockShiftP95Px": shifts["p95Px"],
                                    "meanColorDelta": round(d_mean, 2)},
                "visualizationOnly": True,
            },
        })
        print(f"  #{spot['id']} tile built  post-match: LR {lr_db:.1f}dB "
              f"shiftP95 {shifts['p95Px']}px dColor {d_mean:.1f}")

    if not entries:
        print("no pilot_XX_gen.* images found")
        return 1
    manifest_path = trials_dir / "manifest.json"
    backup = trials_dir / "manifest.pre-pilot.json"
    if manifest_path.exists() and not backup.exists():
        backup.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"backed up previous manifest -> {backup.name}")
    manifest = {
        "schemaVersion": 1,
        "kind": "track-corridor-enhancement",
        "visualizationOnly": True,
        "note": "ImageGen pilot comparison tiles. FAILED the strict fidelity "
                "gates pre colour-match (see pilot/*_eval.json); for visual "
                "comparison only, not for adoption near the racing line.",
        "bbox": index_bbox(),
        "mercator": True,
        "tileCount": len(entries),
        "tiles": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"manifest written: {manifest_path} ({len(entries)} tiles)")
    print("restore previous state:  copy manifest.pre-pilot.json -> manifest.json")
    return 0


def index_bbox() -> dict:
    meta = json.loads(DEFAULT_MOSAIC_META.read_text(encoding="utf-8"))
    return meta["bbox"]


def cmd_compare(args: argparse.Namespace) -> int:
    """Rebuild the comparison artifacts against the CURRENT BEST baseline —
    the shipped 静岡20cm SR look (RealPLKSR x4 -> 2x blend 0.5) — instead of
    the raw source. Writes pilot_XX_panel.jpg (SR | generated, display parity),
    compare.html and a summary with no-reference IQA for both."""
    import argparse as _argparse

    from enhance_track_corridor import (DEFAULT_MODEL_LICENSE, DEFAULT_MODEL_NAME,
                                        DEFAULT_MODEL_PATH, DEFAULT_MODEL_URL,
                                        PerceptualMetrics, spandrel_upscale_x4)

    sr_args = _argparse.Namespace(model_path=DEFAULT_MODEL_PATH, model_url=DEFAULT_MODEL_URL,
                                  model_name=DEFAULT_MODEL_NAME, model_license=DEFAULT_MODEL_LICENSE,
                                  engine="spandrel", device="cuda")
    metrics = None if args.no_iqa else PerceptualMetrics("cuda")
    index = json.loads((PILOT_DIR / "index.json").read_text(encoding="utf-8"))
    rows = []
    for spot in index["spots"]:
        tile_path = PILOT_DIR / f"pilot_{spot['id']}_tile.png"
        if not tile_path.exists():
            continue
        src = np.asarray(Image.open(PILOT_DIR / spot["source"]).convert("RGB"))
        gen = np.asarray(Image.open(tile_path).convert("RGB"))  # colour-matched tile
        display = gen.shape[0]
        # Shipped 静岡20cm SR equivalent at the same display size.
        sr4 = spandrel_upscale_x4(src, sr_args)
        lanczos2 = cv2.resize(src, (CROP * 2, CROP * 2), interpolation=cv2.INTER_LANCZOS4)
        sr2 = cv2.resize(sr4, (CROP * 2, CROP * 2), interpolation=cv2.INTER_LANCZOS4)
        shipped = cv2.addWeighted(sr2, 0.5, lanczos2, 0.5, 0)
        shipped_disp = cv2.resize(shipped, (display, display), interpolation=cv2.INTER_CUBIC)

        iqa = {}
        if metrics is not None:
            def center(img, size=1024):
                cy, cx = img.shape[0] // 2, img.shape[1] // 2
                return img[cy - size // 2:cy + size // 2, cx - size // 2:cx + size // 2]
            iqa = {"shizuokaSR": {k: round(v, 2) for k, v in metrics.no_reference(center(shipped_disp)).items()},
                   "generated": {k: round(v, 2) for k, v in metrics.no_reference(center(gen)).items()}}

        panel = np.concatenate([shipped_disp, gen], axis=1)
        panel_img = Image.fromarray(panel)
        draw = ImageDraw.Draw(panel_img)
        draw.rectangle([0, 0, 320, 34], fill="#101418")
        draw.rectangle([display, 0, display + 380, 34], fill="#101418")
        draw.text((10, 9), "SHIZUOKA 20cm SR (current best)", fill="white")
        draw.text((display + 10, 9), "GENERATED (color-matched)", fill="white")
        panel_img.save(PILOT_DIR / f"pilot_{spot['id']}_panel.jpg", quality=90)
        rows.append({"id": spot["id"], "kind": spot["kind"], "distM": spot["distM"], "iqa": iqa})
        msg = f"  #{spot['id']} panel rebuilt vs 静岡20cm SR"
        if iqa:
            msg += (f"  MUSIQ SR {iqa['shizuokaSR']['musiq']} vs gen {iqa['generated']['musiq']}")
        print(msg)

    (PILOT_DIR / "compare_summary.json").write_text(
        json.dumps({"baseline": "shizuoka 20cm SR (RealPLKSR x4->2x s=0.5, graded)",
                    "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    figures = "\n".join(
        f'<figure><img src="pilot_{r["id"]}_panel.jpg">'
        f'<figcaption>#{r["id"]} {r["kind"]} d={r["distM"]}m'
        + (f' — MUSIQ: SR {r["iqa"]["shizuokaSR"]["musiq"]} / 生成 {r["iqa"]["generated"]["musiq"]}'
           if r["iqa"] else "") + "</figcaption></figure>"
        for r in rows)
    (PILOT_DIR / "compare.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>pilot: 静岡20cm SR vs 生成</title>
<style>body{{background:#111;color:#eee;font:14px sans-serif;margin:20px}}
figure{{margin:14px 0}}img{{max-width:100%}}figcaption{{color:#bbb;margin-top:4px}}</style>
<h1>左=静岡20cm SR（現行ベスト） / 右=生成（色補正後）</h1>
{figures}
""", encoding="utf-8")
    print("gallery:", PILOT_DIR / "compare.html")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="cut the 12 source crops")
    p.add_argument("--mosaic", type=Path, default=DEFAULT_MOSAIC)
    p.add_argument("--mosaic-meta", type=Path, default=DEFAULT_MOSAIC_META)
    p.add_argument("--track-def", type=Path, default=DEFAULT_TRACK_DEF)
    p.set_defaults(func=cmd_prepare)
    e = sub.add_parser("evaluate", help="gate-check pilot_XX_gen.* images")
    e.add_argument("--no-iqa", action="store_true", help="skip MUSIQ/MANIQA (no torch needed)")
    e.set_defaults(func=cmd_evaluate)
    a = sub.add_parser("apply", help="build comparison tiles + override manifest")
    a.add_argument("--feather", type=int, default=24)
    a.add_argument("--no-color-match", action="store_true")
    a.set_defaults(func=cmd_apply)
    c = sub.add_parser("compare", help="panels/IQA vs the shipped 静岡20cm SR baseline")
    c.add_argument("--no-iqa", action="store_true")
    c.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
