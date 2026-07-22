#!/usr/bin/env python3
"""Self-contained tone/clarity grade for licence-clean orthophotos.

Much of the perceived quality gap between the 2019 VIRTUAL SHIZUOKA ortho and
commercial map imagery is GRADING, not resolution: the survey product is
flat, slightly hazy and colour-muted, while map providers ship dehazed,
locally-contrasted, saturated composites.  This script applies the same class
of finishing to our CC BY 4.0 imagery:

  1. gray-world white balance (per-channel gains, hard-capped)
  2. black/white point stretch on luminance (percentile-based, hue-preserving)
  3. CLAHE local contrast on LAB L (the "clarity"/dehaze feel)
  4. mild LAB saturation boost

Every operation is pixel-local and position-preserving — no resampling, no
geometry change, so the imagery-safety rules hold (visualisation layer only).
Google Map Tiles are display-only and must never be used as input NOR as a
calibration reference for these parameters.

Typical use (native mosaic from fetch_shizuoka_ortho.py):

    python pipeline/grade_ortho.py \
        --input pipeline/cache/shizuoka_ortho/native_mosaic.png \
        --meta pipeline/cache/shizuoka_ortho/native_mosaic_meta.json \
        --out-mosaic pipeline/cache/shizuoka_ortho/native_mosaic_graded.png \
        --out-meta pipeline/cache/shizuoka_ortho/native_mosaic_graded_meta.json \
        --base-jpeg public/data/tracks/fuji/satellite_shizuoka.jpg \
        --base-meta public/data/tracks/fuji/satellite_shizuoka_meta.json

The graded mosaic then feeds enhance_track_corridor.py so the SR corridor
tiles and the 8K base texture share one consistent grade (no colour seam at
the corridor fade ring).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class GradeParams:
    """Conservative defaults tuned for aerial survey imagery."""

    wb_gain_limit: float = 0.08       # max ±8% per-channel white-balance gain
    black_percentile: float = 0.4     # luminance mapped to 0
    white_percentile: float = 99.6    # luminance mapped to 255
    stretch_strength: float = 0.85    # 0=no stretch, 1=full percentile stretch
    clahe_clip: float = 1.8           # CLAHE clip limit on L
    clahe_tile_px: int = 128          # CLAHE tile size in pixels (~25 m at 20 cm)
    saturation: float = 1.12          # LAB a/b scale around neutral


def gray_world_gains(rgb: np.ndarray, limit: float) -> tuple[float, float, float]:
    """Per-channel gains toward a neutral mean, clamped to ±limit."""
    means = rgb.reshape(-1, 3).mean(axis=0).astype(np.float64)
    target = float(means.mean())
    gains = []
    for mean in means:
        gain = target / mean if mean > 1e-6 else 1.0
        gains.append(float(np.clip(gain, 1.0 - limit, 1.0 + limit)))
    return gains[0], gains[1], gains[2]


def apply_white_balance(rgb: np.ndarray, params: GradeParams) -> np.ndarray:
    r, g, b = gray_world_gains(rgb, params.wb_gain_limit)
    gains = np.array([r, g, b], dtype=np.float32)
    return np.clip(rgb.astype(np.float32) * gains, 0, 255).astype(np.uint8)


def stretch_levels(rgb: np.ndarray, params: GradeParams) -> np.ndarray:
    """Hue-preserving black/white point stretch: scale every channel by the
    luminance ratio so colours do not shift, only brightness/contrast."""
    luminance = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    # Percentiles from a decimated copy — plenty accurate for a global stretch.
    sample = luminance[::8, ::8]
    lo = float(np.percentile(sample, params.black_percentile))
    hi = float(np.percentile(sample, params.white_percentile))
    if hi - lo < 16:  # degenerate/flat image: leave untouched
        return rgb
    stretched = np.clip((luminance - lo) * (255.0 / (hi - lo)), 0, 255)
    target = luminance + (stretched - luminance) * params.stretch_strength
    ratio = target / np.maximum(luminance, 1.0)
    out = rgb.astype(np.float32) * ratio[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def clahe_clarity(rgb: np.ndarray, params: GradeParams) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    tiles_x = max(2, round(rgb.shape[1] / params.clahe_tile_px))
    tiles_y = max(2, round(rgb.shape[0] / params.clahe_tile_px))
    clahe = cv2.createCLAHE(clipLimit=params.clahe_clip, tileGridSize=(tiles_x, tiles_y))
    lab[..., 0] = clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def boost_saturation(rgb: np.ndarray, params: GradeParams) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 1] = (lab[..., 1] - 128.0) * params.saturation + 128.0
    lab[..., 2] = (lab[..., 2] - 128.0) * params.saturation + 128.0
    lab = np.clip(lab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def grade_image(rgb: np.ndarray, params: GradeParams | None = None) -> np.ndarray:
    """Full grade. Input/output: RGB uint8, same shape (position-preserving)."""
    p = params or GradeParams()
    out = apply_white_balance(rgb, p)
    out = stretch_levels(out, p)
    out = clahe_clarity(out, p)
    out = boost_saturation(out, p)
    return out


def comparison_panel(before: np.ndarray, after: np.ndarray, out_path: Path,
                     crop: int = 768) -> None:
    """Center-crop before/after side-by-side for the record."""
    cy, cx = before.shape[0] // 2, before.shape[1] // 2
    half = crop // 2
    b = before[cy - half:cy + half, cx - half:cx + half]
    a = after[cy - half:cy + half, cx - half:cx + half]
    panel = np.concatenate([b, a], axis=1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(panel).save(out_path, quality=92)


def params_from_args(args: argparse.Namespace) -> GradeParams:
    """Build GradeParams from CLI overrides. Any flag left at its sentinel
    (None) keeps the dataclass default, so omitting all of them reproduces
    the historical fuji/shizuoka grade exactly (additive, non-breaking)."""
    base = GradeParams()
    return GradeParams(
        wb_gain_limit=base.wb_gain_limit if args.wb_gain_limit is None else args.wb_gain_limit,
        black_percentile=base.black_percentile if args.black_percentile is None else args.black_percentile,
        white_percentile=base.white_percentile if args.white_percentile is None else args.white_percentile,
        stretch_strength=base.stretch_strength if args.stretch_strength is None else args.stretch_strength,
        clahe_clip=base.clahe_clip if args.clahe_clip is None else args.clahe_clip,
        clahe_tile_px=base.clahe_tile_px if args.clahe_tile_px is None else args.clahe_tile_px,
        saturation=base.saturation if args.saturation is None else args.saturation,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--out-mosaic", type=Path, required=True)
    parser.add_argument("--out-meta", type=Path, required=True)
    # Optional grade overrides (default None -> keep GradeParams defaults).
    parser.add_argument("--wb-gain-limit", type=float, default=None)
    parser.add_argument("--black-percentile", type=float, default=None)
    parser.add_argument("--white-percentile", type=float, default=None)
    parser.add_argument("--stretch-strength", type=float, default=None)
    parser.add_argument("--clahe-clip", type=float, default=None)
    parser.add_argument("--clahe-tile-px", type=int, default=None)
    parser.add_argument("--saturation", type=float, default=None)
    parser.add_argument("--base-jpeg", type=Path, default=None,
                        help="Also write the graded 8K base texture (long edge "
                             "--base-long-edge, JPEG) + updates --base-meta")
    parser.add_argument("--base-meta", type=Path, default=None)
    parser.add_argument("--base-long-edge", type=int, default=8192)
    parser.add_argument("--jpeg-quality", type=int, default=94)
    parser.add_argument("--panel", type=Path, default=None,
                        help="Optional before/after comparison JPEG")
    args = parser.parse_args()

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    image = np.asarray(Image.open(args.input).convert("RGB"))
    if (image.shape[1], image.shape[0]) != (meta["imageWidth"], meta["imageHeight"]):
        raise SystemExit("image/meta size mismatch")

    params = params_from_args(args)
    graded = grade_image(image, params)

    args.out_mosaic.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(graded).save(args.out_mosaic, "PNG", compress_level=3)
    graded_meta = dict(meta)
    graded_meta["imageFile"] = args.out_mosaic.name
    graded_meta["toneEnhanced"] = ("gray-world WB + luminance stretch + CLAHE "
                                   "clarity + LAB saturation (grade_ortho.py); "
                                   "pixel-local only, no geometry change")
    graded_meta["gradeParams"] = {
        "wbGainLimit": params.wb_gain_limit, "blackPercentile": params.black_percentile,
        "whitePercentile": params.white_percentile, "stretchStrength": params.stretch_strength,
        "claheClip": params.clahe_clip, "claheTilePx": params.clahe_tile_px,
        "saturation": params.saturation,
    }
    args.out_meta.write_text(json.dumps(graded_meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"graded mosaic: {args.out_mosaic}")

    if args.panel:
        comparison_panel(image, graded, args.panel)
        print(f"comparison panel: {args.panel}")

    if args.base_jpeg:
        if not args.base_meta:
            raise SystemExit("--base-jpeg requires --base-meta")
        height, width = graded.shape[:2]
        scale = args.base_long_edge / max(width, height)
        base_w = round(width * min(1.0, scale))
        base_h = round(height * min(1.0, scale))
        base = cv2.resize(graded, (base_w, base_h), interpolation=cv2.INTER_AREA)
        args.base_jpeg.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(base).save(args.base_jpeg, "JPEG", quality=args.jpeg_quality,
                                   optimize=True, progressive=True)
        base_meta = dict(meta)
        base_meta["imageFile"] = args.base_jpeg.name
        base_meta["imageWidth"] = base_w
        base_meta["imageHeight"] = base_h
        base_meta["effectiveResolutionMetersPerPixel"] = round(
            meta.get("effectiveResolutionMetersPerPixel", 0.2) * (width / base_w), 4)
        base_meta["toneEnhanced"] = graded_meta["toneEnhanced"]
        base_meta["gradeParams"] = graded_meta["gradeParams"]
        args.base_meta.write_text(json.dumps(base_meta, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"graded base texture: {args.base_jpeg} ({base_w}x{base_h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
