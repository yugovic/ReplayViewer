#!/usr/bin/env python3
"""Remove the global/low-frequency colour drift that Real-ESRGAN introduces
(satellite_bing_sr.jpg comes out slightly darker/tinted vs satellite_bing.jpg;
measured ~-4/-2.6/-0.5 RGB mean drift on barber, spec requires <2/255).

Method: low-frequency colour transfer. The corrected image keeps ONLY the
high-frequency detail of the SR output and takes its low-frequency
colour/tone field from the original:

    corrected = clip( sr + upsample( lowpass(orig) - lowpass(sr) ) )

where lowpass() is a 1/16 Lanczos downsample (plus a small Gaussian at the
reduced scale, effective sigma ~30-50 full-res px). Because the correction
term's mean equals mean(orig)-mean(sr) by construction (up to clipping),
the corrected image's per-channel means land on the original's to within
a few hundredths of a level -- no tint, no structural change (the
correction field contains no detail above the ~30px scale, so it cannot
add or remove track markings).

Memory-safe: the full-resolution correction field is never materialised;
it is generated strip-by-strip with PIL's box-resize directly from the
small difference image.

Usage:
    pipeline/.venv-aim/bin/python pipeline/fix_sr_color.py --track barber
    pipeline/.venv-aim/bin/python pipeline/fix_sr_color.py \
        --orig a.jpg --sr b.jpg --output b_fixed.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKS_ROOT = REPO_ROOT / "public" / "data" / "tracks"

DOWN_FACTOR = 16
SMALL_BLUR_RADIUS = 2.0  # at 1/16 scale -> effective ~32px sigma at full res
STRIP_H = 512


def lowpass_small(img: Image.Image, down: int, blur_radius: float) -> np.ndarray:
    w, h = img.size
    sw, sh = max(1, round(w / down)), max(1, round(h / down))
    small = img.resize((sw, sh), Image.LANCZOS)
    if blur_radius > 0:
        small = small.filter(ImageFilter.GaussianBlur(blur_radius))
    return np.asarray(small, dtype=np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--track", choices=["barber", "fuji"], default=None,
                     help="Shortcut: orig=satellite_bing.jpg sr=satellite_bing_sr.jpg output=satellite_bing_sr.jpg (in-place)")
    ap.add_argument("--orig", type=Path, default=None)
    ap.add_argument("--sr", type=Path, default=None)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--quality", type=int, default=90)
    args = ap.parse_args()

    if args.track:
        orig_path = TRACKS_ROOT / args.track / "satellite_bing.jpg"
        sr_path = TRACKS_ROOT / args.track / "satellite_bing_sr.jpg"
        out_path = sr_path
    else:
        if not (args.orig and args.sr and args.output):
            print("ERROR: either --track or all of --orig/--sr/--output required", file=sys.stderr)
            return 1
        orig_path, sr_path, out_path = args.orig, args.sr, args.output

    orig_img = Image.open(orig_path).convert("RGB")
    sr_img = Image.open(sr_path).convert("RGB")
    if orig_img.size != sr_img.size:
        print(f"ERROR: size mismatch orig {orig_img.size} vs sr {sr_img.size}", file=sys.stderr)
        return 1
    W, H = orig_img.size
    print(f"Images: {W}x{H}")

    print("Computing low-frequency colour fields (1/16 scale)...")
    lp_orig = lowpass_small(orig_img, DOWN_FACTOR, SMALL_BLUR_RADIUS)
    lp_sr = lowpass_small(sr_img, DOWN_FACTOR, SMALL_BLUR_RADIUS)
    d_small = lp_orig - lp_sr  # correction field at 1/16 scale
    sh, sw, _ = d_small.shape
    print(f"Correction field {sw}x{sh}: mean RGB {d_small.mean(axis=(0,1)).round(3).tolist()}, "
          f"min {d_small.min():.2f}, max {d_small.max():.2f}")

    # Per-channel small PIL 'F' images for strip-wise box-resize upsampling
    d_chan_imgs = [Image.fromarray(d_small[:, :, c], mode="F") for c in range(3)]

    sr_arr = np.asarray(sr_img, dtype=np.uint8)
    del sr_img
    out_arr = np.empty_like(sr_arr)

    print(f"Applying correction in {STRIP_H}px strips...")
    for y0 in range(0, H, STRIP_H):
        y1 = min(y0 + STRIP_H, H)
        box = (0.0, y0 * sh / H, float(sw), y1 * sh / H)
        strip_f32 = np.empty((y1 - y0, W, 3), dtype=np.float32)
        for c in range(3):
            d_strip = d_chan_imgs[c].resize((W, y1 - y0), Image.BICUBIC, box=box)
            strip_f32[:, :, c] = np.asarray(d_strip, dtype=np.float32)
        corrected = sr_arr[y0:y1].astype(np.float32) + strip_f32
        out_arr[y0:y1] = np.clip(corrected + 0.5, 0, 255).astype(np.uint8)
        del strip_f32, corrected

    orig_arr_mean = np.asarray(orig_img, dtype=np.float64).mean(axis=(0, 1))
    del orig_img
    before_mean = sr_arr.mean(axis=(0, 1))
    after_mean = out_arr.astype(np.float64).mean(axis=(0, 1))
    print(f"orig  mean RGB: {orig_arr_mean.round(3).tolist()}")
    print(f"sr    mean RGB (before): {before_mean.round(3).tolist()}  drift {(before_mean-orig_arr_mean).round(3).tolist()}")
    print(f"fixed mean RGB (after):  {after_mean.round(3).tolist()}  drift {(after_mean-orig_arr_mean).round(3).tolist()}")
    max_after = float(np.abs(after_mean - orig_arr_mean).max())
    print(f"max abs drift after: {max_after:.4f} ({'PASS' if max_after < 2.0 else 'FAIL'} vs <2.0 requirement)")

    out_img = Image.fromarray(out_arr, mode="RGB")
    out_img.save(out_path, format="JPEG", quality=args.quality)
    print(f"Saved: {out_path} ({out_path.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
