#!/usr/bin/env python3
"""
Real-ESRGAN x4 super-resolution upscaler for satellite imagery, followed by
a Lanczos downsample to reach an effective final scale (default 2x).

Self-contained: implements RRDBNet inline (no basicsr/realesrgan dependency).
Loads the official xinntao/Real-ESRGAN RealESRGAN_x4plus.pth weights.

Usage:
    python upscale_satellite.py --input in.jpg --output out.jpg \
        [--final-scale 2] [--quality 90] [--tile 512] [--overlap 64] \
        [--model pipeline/models/RealESRGAN_x4plus.pth]
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image


# --------------------------------------------------------------------------
# RRDBNet architecture (standard ESRGAN / Real-ESRGAN generator)
# --------------------------------------------------------------------------

class ResidualDenseBlock(nn.Module):
    """Residual Dense Block with 5 convs, growth channel num_grow_ch."""

    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    """Residual in Residual Dense Block, made of 3 ResidualDenseBlocks."""

    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    """RRDBNet generator used by Real-ESRGAN (x4plus variant)."""

    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64,
                 num_block=23, num_grow_ch=32, scale=4):
        super().__init__()
        self.scale = scale
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.ModuleList(
            [RRDB(num_feat, num_grow_ch) for _ in range(num_block)]
        )
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        # upsample (x4 = two 2x nearest-neighbour + conv stages)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        body_feat = feat
        for block in self.body:
            body_feat = block(body_feat)
        body_feat = self.conv_body(body_feat)
        feat = feat + body_feat

        feat = self.lrelu(
            self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest"))
        )
        feat = self.lrelu(
            self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest"))
        )
        out = self.conv_last(self.lrelu(self.conv_hr(feat)))
        return out


# --------------------------------------------------------------------------
# Tiled inference with feathered blending
# --------------------------------------------------------------------------

def make_feather_ramp(length, overlap):
    """1D ramp: 0->1 over `overlap` samples at each end, 1 in the middle."""
    w = np.ones(length, dtype=np.float32)
    if overlap > 0:
        ramp = np.linspace(0.0, 1.0, overlap, endpoint=False, dtype=np.float32)
        ramp = np.concatenate([ramp, np.array([1.0], dtype=np.float32)])[:overlap]
        # simple linear ramp 1..overlap -> use (i+1)/(overlap+1) to avoid 0 weight
        ramp = (np.arange(1, overlap + 1, dtype=np.float32)) / (overlap + 1)
        w[:overlap] = np.minimum(w[:overlap], ramp)
        w[-overlap:] = np.minimum(w[-overlap:], ramp[::-1])
    return w


def run_tiled_sr(model, img_np, device, tile=512, overlap=64, progress=True,
                 final_scale=2.0):
    """
    img_np: float32 HxWx3 in [0,1]
    Each x4 SR tile is immediately Lanczos-downsampled to `final_scale`
    before accumulation, so the output buffers live at final resolution
    (~1GB for 2x on a 4096x3665 input instead of ~4GB at 4x).
    MPS cache is emptied after every tile to cap allocator growth.
    Returns: float32 (final_scale*H)x(final_scale*W)x3 in [0,1]
    """
    fs = final_scale
    h, w, c = img_np.shape
    out_h, out_w = round(h * fs), round(w * fs)

    acc = np.zeros((out_h, out_w, c), dtype=np.float32)
    wsum = np.zeros((out_h, out_w, 1), dtype=np.float32)

    # Compute tile grid: core step = tile, context = tile + 2*overlap (clamped)
    xs = list(range(0, w, tile))
    ys = list(range(0, h, tile))
    total = len(xs) * len(ys)
    idx = 0
    t_start = time.time()

    for ty in ys:
        core_h = min(tile, h - ty)
        ctx_y0 = max(0, ty - overlap)
        ctx_y1 = min(h, ty + core_h + overlap)
        for tx in xs:
            idx += 1
            core_w = min(tile, w - tx)
            ctx_x0 = max(0, tx - overlap)
            ctx_x1 = min(w, tx + core_w + overlap)

            patch = img_np[ctx_y0:ctx_y1, ctx_x0:ctx_x1, :]
            tens = torch.from_numpy(patch).permute(2, 0, 1).unsqueeze(0).float()

            sr_tens = infer_with_fallback(model, tens, device)
            sr_x4 = sr_tens.squeeze(0).permute(1, 2, 0).cpu().numpy()
            sr_x4 = np.clip(sr_x4, 0.0, 1.0)
            del sr_tens, tens
            if device.type == "mps":
                torch.mps.empty_cache()

            # Immediately downsample this x4 tile to final scale (Lanczos)
            ctx_w_src = ctx_x1 - ctx_x0
            ctx_h_src = ctx_y1 - ctx_y0
            fin_w = round(ctx_w_src * fs)
            fin_h = round(ctx_h_src * fs)
            tile_img = Image.fromarray(
                (sr_x4 * 255.0 + 0.5).astype(np.uint8), mode="RGB"
            )
            del sr_x4
            tile_img = tile_img.resize((fin_w, fin_h), resample=Image.LANCZOS)
            sr_patch = np.asarray(tile_img).astype(np.float32) / 255.0
            del tile_img

            # Offsets of this context patch in the final output space
            oy0 = round(ctx_y0 * fs)
            ox0 = round(ctx_x0 * fs)
            ph, pw = sr_patch.shape[0], sr_patch.shape[1]

            # Feather weight ramps sized to actual context patch,
            # zero-weight the "overflow" margin beyond image edges naturally
            # (no overlap possible there since context clamps to border).
            top_pad = round((ty - ctx_y0) * fs)
            bot_pad = round((ctx_y1 - (ty + core_h)) * fs)
            left_pad = round((tx - ctx_x0) * fs)
            right_pad = round((ctx_x1 - (tx + core_w)) * fs)

            wy = make_feather_ramp(ph, 0)
            wx = make_feather_ramp(pw, 0)
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

            if progress:
                elapsed = time.time() - t_start
                print(
                    f"  tile {idx}/{total}  "
                    f"(core {core_w}x{core_h} @ {tx},{ty})  "
                    f"elapsed {elapsed:6.1f}s",
                    flush=True,
                )

    wsum = np.maximum(wsum, 1e-8)
    result = acc / wsum
    return np.clip(result, 0.0, 1.0)


def infer_with_fallback(model, tens, device):
    """Run model on device; if MPS op fails, retry this tile on CPU."""
    try:
        with torch.no_grad():
            t = tens.to(device)
            out = model(t)
        return out
    except Exception as e:  # noqa: BLE001 - deliberate broad fallback
        if str(device) != "cpu":
            print(f"    [warn] device inference failed ({e}); retrying on CPU", flush=True)
            with torch.no_grad():
                out = model(tens.to("cpu"))
            return out
        raise


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_model(weights_path, device):
    state = torch.load(weights_path, map_location="cpu")
    if "params_ema" in state:
        params = state["params_ema"]
    elif "params" in state:
        params = state["params"]
    else:
        params = state
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                     num_block=23, num_grow_ch=32, scale=4)
    model.load_state_dict(params, strict=True)
    model.eval()
    model.to(device)
    return model


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Input JPEG path")
    ap.add_argument("--output", required=True, help="Output JPEG path (must differ from input)")
    ap.add_argument("--model", default=str(Path(__file__).parent / "models" / "RealESRGAN_x4plus.pth"))
    ap.add_argument("--final-scale", type=float, default=2.0,
                     help="Effective final scale relative to input (default 2 = x4 SR then 0.5 Lanczos)")
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--tile", type=int, default=512)
    ap.add_argument("--overlap", type=int, default=64)
    args = ap.parse_args()

    in_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()

    if in_path == out_path:
        print("ERROR: --output must not equal --input", file=sys.stderr)
        sys.exit(1)

    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: model weights not found: {model_path}", file=sys.stderr)
        sys.exit(1)

    if torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    print(f"Device: {device}")
    print(f"Torch: {torch.__version__}")

    t0 = time.time()
    print(f"Loading model from {model_path} ...")
    model = load_model(model_path, device)
    print(f"Model loaded in {time.time() - t0:.1f}s")

    print(f"Opening image {in_path} ...")
    img = Image.open(in_path).convert("RGB")
    w0, h0 = img.size
    print(f"Input size: {w0}x{h0}")

    img_np = np.asarray(img).astype(np.float32) / 255.0

    n_tiles_x = (w0 + args.tile - 1) // args.tile
    n_tiles_y = (h0 + args.tile - 1) // args.tile
    total_tiles = n_tiles_x * n_tiles_y
    print(f"Tiling: {n_tiles_x}x{n_tiles_y} = {total_tiles} tiles "
          f"(tile={args.tile}, overlap={args.overlap})")

    t1 = time.time()
    sr_np = run_tiled_sr(model, img_np, device, tile=args.tile,
                          overlap=args.overlap, final_scale=args.final_scale)
    sr_elapsed = time.time() - t1
    print(f"SR inference complete in {sr_elapsed:.1f}s")

    sr_h, sr_w = sr_np.shape[0], sr_np.shape[1]
    print(f"Final-scale ({args.final_scale}x) output size: {sr_w}x{sr_h}")

    final_img = Image.fromarray((sr_np * 255.0 + 0.5).astype(np.uint8), mode="RGB")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(out_path, format="JPEG", quality=args.quality)

    total_elapsed = time.time() - t0
    print(f"Saved: {out_path}")
    print(f"Final size: {final_img.size[0]}x{final_img.size[1]}")
    print(f"Total tiles processed: {total_tiles}")
    print(f"Total runtime: {total_elapsed:.1f}s ({total_elapsed/60:.2f} min)")


if __name__ == "__main__":
    main()
