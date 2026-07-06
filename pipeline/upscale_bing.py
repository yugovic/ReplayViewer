#!/usr/bin/env python3
"""
Same-resolution Real-ESRGAN enhancement for the Bing satellite variants
(satellite_bing.jpg -> satellite_bing_sr.jpg), net 1:1 scale (x4 SR per tile
immediately followed by a 1/4 Lanczos downsample, so output dimensions equal
input dimensions exactly).

Reuses the RRDBNet architecture, model loader, feathered-blend tile math and
MPS-fallback inference helper from upscale_satellite.py (imported as a
module -- upscale_satellite.py itself is untouched and its own CLI default
behaviour (final-scale=2, tile=512) is unaffected).

Difference from upscale_satellite.py's run_tiled_sr: this adds a resumable,
row-checkpointed outer loop. A ~1.5h single-shot run on a 16GB machine is a
crash/OOM risk (this repo has already seen a tile=512 swap death), so after
each full row of tiles is accumulated (and thus can never be written to
again by a later tile, since overlap only reaches into the immediately
adjacent row), the running (acc, wsum) buffers are checkpointed to disk
before continuing. If the process is killed or restarted, it picks up at
the start of the next un-finished row instead of from scratch.

Usage:
    pipeline/.venv-aim/bin/python pipeline/upscale_bing.py --track barber
    pipeline/.venv-aim/bin/python pipeline/upscale_bing.py --track fuji
    pipeline/.venv-aim/bin/python pipeline/upscale_bing.py \
        --input in.jpg --output out.jpg --tile 256 --overlap 32 --final-scale 1.0
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upscale_satellite as base  # noqa: E402  (RRDBNet / load_model / feathering / MPS-fallback inference)

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKS_ROOT = REPO_ROOT / "public" / "data" / "tracks"
DEFAULT_CHECKPOINT_ROOT = Path(__file__).resolve().parent / "cache" / "sr_checkpoints"


def peak_rss_mb() -> float:
    """ru_maxrss is bytes on macOS, KB on Linux -- this pipeline only runs on
    macOS (darwin) per the environment, so treat as bytes."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return ru / (1024 * 1024)
    return ru / 1024


def save_checkpoint(ckpt_dir: Path, acc: np.ndarray, wsum: np.ndarray, next_row_idx: int, meta: dict):
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    # np.save silently appends ".npy" if the given name doesn't already end
    # with it -- name the temp files so the auto-appended result is still
    # the ".tmp.npy" path we expect, then atomically replace.
    acc_tmp = ckpt_dir / "acc.tmp.npy"
    wsum_tmp = ckpt_dir / "wsum.tmp.npy"
    np.save(acc_tmp, acc)
    np.save(wsum_tmp, wsum)
    os.replace(acc_tmp, ckpt_dir / "acc.npy")
    os.replace(wsum_tmp, ckpt_dir / "wsum.npy")
    state = dict(meta)
    state["next_row_idx"] = next_row_idx
    state_tmp = ckpt_dir / "state.json.tmp"
    state_tmp.write_text(json.dumps(state))
    os.replace(state_tmp, ckpt_dir / "state.json")


def load_checkpoint(ckpt_dir: Path, meta: dict):
    state_path = ckpt_dir / "state.json"
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text())
    # Sanity: only resume if params match (tile/overlap/final_scale/shape)
    for k in ("h", "w", "tile", "overlap", "final_scale"):
        if state.get(k) != meta.get(k):
            print(f"  [checkpoint] param mismatch on '{k}' (ckpt={state.get(k)} vs run={meta.get(k)}) -- ignoring stale checkpoint")
            return None
    acc = np.load(ckpt_dir / "acc.npy")
    wsum = np.load(ckpt_dir / "wsum.npy")
    return acc, wsum, state["next_row_idx"]


def run_tiled_sr_resumable(model, img_np, device, tile, overlap, final_scale,
                            checkpoint_dir: Path, checkpoint_every_rows=1, progress=True):
    fs = final_scale
    h, w, c = img_np.shape
    out_h, out_w = round(h * fs), round(w * fs)

    xs = list(range(0, w, tile))
    ys = list(range(0, h, tile))
    n_rows = len(ys)
    n_cols = len(xs)
    total = n_rows * n_cols

    meta = {"h": h, "w": w, "tile": tile, "overlap": overlap, "final_scale": fs}
    resumed = load_checkpoint(checkpoint_dir, meta)
    if resumed is not None:
        acc, wsum, start_row_idx = resumed
        print(f"  [checkpoint] RESUMED at row {start_row_idx}/{n_rows} from {checkpoint_dir}")
    else:
        acc = np.zeros((out_h, out_w, c), dtype=np.float32)
        wsum = np.zeros((out_h, out_w, 1), dtype=np.float32)
        start_row_idx = 0
        print(f"  [checkpoint] starting fresh ({n_rows} rows x {n_cols} cols = {total} tiles)")

    idx_done = start_row_idx * n_cols
    t_start = time.time()

    for row_idx in range(start_row_idx, n_rows):
        ty = ys[row_idx]
        core_h = min(tile, h - ty)
        ctx_y0 = max(0, ty - overlap)
        ctx_y1 = min(h, ty + core_h + overlap)

        for col_idx, tx in enumerate(xs):
            core_w = min(tile, w - tx)
            ctx_x0 = max(0, tx - overlap)
            ctx_x1 = min(w, tx + core_w + overlap)

            patch = img_np[ctx_y0:ctx_y1, ctx_x0:ctx_x1, :]
            tens = torch.from_numpy(patch).permute(2, 0, 1).unsqueeze(0).float()

            sr_tens = base.infer_with_fallback(model, tens, device)
            sr_x4 = sr_tens.squeeze(0).permute(1, 2, 0).cpu().numpy()
            sr_x4 = np.clip(sr_x4, 0.0, 1.0)
            del sr_tens, tens
            if device.type == "mps":
                torch.mps.empty_cache()

            ctx_w_src = ctx_x1 - ctx_x0
            ctx_h_src = ctx_y1 - ctx_y0
            fin_w = round(ctx_w_src * fs)
            fin_h = round(ctx_h_src * fs)
            tile_img = Image.fromarray((sr_x4 * 255.0 + 0.5).astype(np.uint8), mode="RGB")
            del sr_x4
            tile_img = tile_img.resize((fin_w, fin_h), resample=Image.LANCZOS)
            sr_patch = np.asarray(tile_img).astype(np.float32) / 255.0
            del tile_img

            oy0 = round(ctx_y0 * fs)
            ox0 = round(ctx_x0 * fs)
            ph, pw = sr_patch.shape[0], sr_patch.shape[1]

            top_pad = round((ty - ctx_y0) * fs)
            bot_pad = round((ctx_y1 - (ty + core_h)) * fs)
            left_pad = round((tx - ctx_x0) * fs)
            right_pad = round((ctx_x1 - (tx + core_w)) * fs)

            wy = base.make_feather_ramp(ph, 0)
            wx = base.make_feather_ramp(pw, 0)
            if top_pad > 0:
                ramp = (np.arange(1, top_pad + 1, dtype=np.float32)) / (top_pad + 1)
                wy[:top_pad] = np.minimum(wy[:top_pad], ramp)
            if bot_pad > 0:
                ramp = (np.arange(1, bot_pad + 1, dtype=np.float32)) / (bot_pad + 1)
                wy[-bot_pad:] = np.minimum(wy[-bot_pad:], ramp[::-1])
            if left_pad > 0:
                ramp = (np.arange(1, left_pad + 1, dtype=np.float32)) / (left_pad + 1)
                wx[:left_pad] = np.minimum(wx[:left_pad], ramp)
            if right_pad > 0:
                ramp = (np.arange(1, right_pad + 1, dtype=np.float32)) / (right_pad + 1)
                wx[-right_pad:] = np.minimum(wx[-right_pad:], ramp[::-1])

            weight2d = (wy[:, None] * wx[None, :]).astype(np.float32)[:, :, None]

            acc[oy0:oy0 + ph, ox0:ox0 + pw, :] += sr_patch * weight2d
            wsum[oy0:oy0 + ph, ox0:ox0 + pw, :] += weight2d
            del sr_patch, weight2d

            idx_done += 1
            if progress:
                elapsed = time.time() - t_start
                rate = (idx_done - start_row_idx * n_cols) / max(elapsed, 1e-6)
                remaining = (total - idx_done) / max(rate, 1e-6)
                if idx_done % 10 == 0 or idx_done == total or col_idx == n_cols - 1:
                    print(f"  tile {idx_done}/{total} (row {row_idx+1}/{n_rows}, col {col_idx+1}/{n_cols}) "
                          f"elapsed={elapsed:6.1f}s eta={remaining:6.1f}s rss={peak_rss_mb():6.0f}MB",
                          flush=True)

        # Row fully accumulated -- safe checkpoint boundary (overlap from the
        # NEXT row hasn't been applied yet, so resuming at row_idx+1 replays
        # nothing that's already "final").
        if (row_idx + 1) % checkpoint_every_rows == 0 or row_idx == n_rows - 1:
            t_ckpt = time.time()
            save_checkpoint(checkpoint_dir, acc, wsum, row_idx + 1, meta)
            print(f"  [checkpoint] saved after row {row_idx+1}/{n_rows} ({time.time()-t_ckpt:.1f}s to write)", flush=True)

    wsum = np.maximum(wsum, 1e-8)
    result = acc / wsum
    return np.clip(result, 0.0, 1.0), total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--track", choices=["barber", "fuji"], default=None,
                     help="Shortcut: use public/data/tracks/{track}/satellite_bing.jpg -> satellite_bing_sr.jpg")
    ap.add_argument("--input", type=Path, default=None)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--model", default=str(Path(__file__).parent / "models" / "RealESRGAN_x4plus.pth"))
    ap.add_argument("--final-scale", type=float, default=1.0,
                     help="Net scale relative to input; 1.0 = same-resolution enhancement")
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--tile", type=int, default=256)
    ap.add_argument("--overlap", type=int, default=32)
    ap.add_argument("--checkpoint-dir", type=Path, default=None)
    ap.add_argument("--checkpoint-every-rows", type=int, default=1)
    args = ap.parse_args()

    if args.track:
        in_path = TRACKS_ROOT / args.track / "satellite_bing.jpg"
        out_path = TRACKS_ROOT / args.track / "satellite_bing_sr.jpg"
        ckpt_dir = args.checkpoint_dir or (DEFAULT_CHECKPOINT_ROOT / args.track)
    else:
        if not args.input or not args.output:
            print("ERROR: either --track or both --input/--output are required", file=sys.stderr)
            return 1
        in_path = args.input.resolve()
        out_path = args.output.resolve()
        ckpt_dir = args.checkpoint_dir or (DEFAULT_CHECKPOINT_ROOT / in_path.stem)

    if in_path == out_path:
        print("ERROR: output must not equal input", file=sys.stderr)
        return 1
    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}", file=sys.stderr)
        return 1
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: model weights not found: {model_path}", file=sys.stderr)
        return 1
    expected_size = 67_040_989
    actual_size = model_path.stat().st_size
    if actual_size != expected_size:
        print(f"ERROR: model weights size mismatch: {actual_size} != expected {expected_size}", file=sys.stderr)
        return 1

    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
    print(f"Device: {device}  Torch: {torch.__version__}")
    print(f"Input: {in_path}")
    print(f"Output: {out_path}")
    print(f"Checkpoint dir: {ckpt_dir}")

    t0 = time.time()
    print(f"Loading model from {model_path} ...")
    model = base.load_model(model_path, device)
    print(f"Model loaded in {time.time() - t0:.1f}s")

    img = Image.open(in_path).convert("RGB")
    w0, h0 = img.size
    print(f"Input size: {w0}x{h0}")
    img_np = np.asarray(img).astype(np.float32) / 255.0
    del img

    n_tiles_x = (w0 + args.tile - 1) // args.tile
    n_tiles_y = (h0 + args.tile - 1) // args.tile
    total_tiles = n_tiles_x * n_tiles_y
    print(f"Tiling: {n_tiles_x}x{n_tiles_y} = {total_tiles} tiles (tile={args.tile}, overlap={args.overlap}, final_scale={args.final_scale})")

    t1 = time.time()
    sr_np, total = run_tiled_sr_resumable(
        model, img_np, device, tile=args.tile, overlap=args.overlap,
        final_scale=args.final_scale, checkpoint_dir=ckpt_dir,
        checkpoint_every_rows=args.checkpoint_every_rows,
    )
    sr_elapsed = time.time() - t1
    print(f"SR inference complete in {sr_elapsed:.1f}s ({sr_elapsed/60:.2f} min)")

    sr_h, sr_w = sr_np.shape[0], sr_np.shape[1]
    print(f"Output size: {sr_w}x{sr_h} (input was {w0}x{h0})")

    final_img = Image.fromarray((sr_np * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    del sr_np

    out_path.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(out_path, format="JPEG", quality=args.quality)

    total_elapsed = time.time() - t0
    print(f"Saved: {out_path}")
    print(f"Final size: {final_img.size[0]}x{final_img.size[1]}")
    print(f"Total tiles processed: {total_tiles}")
    print(f"Peak RSS: {peak_rss_mb():.0f} MB")
    print(f"Total runtime: {total_elapsed:.1f}s ({total_elapsed/60:.2f} min)")

    # Clean up checkpoint now that the final output is safely written.
    for fname in ("acc.npy", "wsum.npy", "state.json"):
        fp = ckpt_dir / fname
        if fp.exists():
            fp.unlink()
    print(f"Cleaned up checkpoint dir: {ckpt_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
