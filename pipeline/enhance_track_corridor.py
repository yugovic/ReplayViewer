#!/usr/bin/env python3
"""Build and validate a track-corridor-only enhanced orthophoto layer.

The input must be imagery whose licence permits derivative processing.  The
Fuji defaults intentionally use VIRTUAL SHIZUOKA's CC BY 4.0 20 cm ortho;
Google Map Tiles must never be supplied to this script.  For best quality
feed the lossless native mosaic (no JPEG stage)::

    python pipeline/fetch_shizuoka_ortho.py --skip-download \
        --out-image pipeline/cache/shizuoka_ortho/native_mosaic.png \
        --out-meta pipeline/cache/shizuoka_ortho/native_mosaic_meta.json \
        --max-long-edge 16384

Engines:

* ``spandrel`` (default): 4xNomosWebPhoto_RealPLKSR (CC BY 4.0, Philip
  Hofmann) via spandrel + torch CUDA.  4x inference downscaled to 2x.
* ``realesrgan``: legacy realesrgan-ncnn-vulkan x4plus path.
* ``lanczos``: sharpened Lanczos baseline, no ML.

Two modes are provided:

* ``validation`` runs a synthetic 2x holdout (downsample, restore, compare
  with the untouched native crop).  Gating is PERCEPTUAL: the blend strength
  is chosen by mean DISTS/LPIPS, then two hallucination guards must pass —
  LR-consistency (the output re-downsampled must reproduce the input) and
  a phase-correlation line-shift bound against the Lanczos reference.
  PSNR/SSIM are still reported but no longer gate (they systematically
  favour blur; see docs/research-ground-resolution-strategy-2026-07-16.md).
* ``tiles`` creates transparent WebP tiles only within the track corridor.
  Alpha is 1.0 within ``--core-metres`` and smoothly fades to zero at
  ``--outer-metres``.  The output manifest keeps source/licence, model and
  effective resolution explicit for the Replay renderer.

SR output is a perceptually sharper visual layer, not new surveyed truth.
Never use it for geometry extraction or measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Model/metric weight caches default to the repository drive (D:) — the
# system drive is chronically near-full and large downloads onto it have
# corrupted installs before. Explicit environment variables still win.
_CACHE_ROOT = Path(__file__).resolve().parent / "cache"
os.environ.setdefault("TORCH_HOME", str(_CACHE_ROOT / "torch"))
os.environ.setdefault("HF_HOME", str(_CACHE_ROOT / "hf"))

import cv2
import numpy as np
from PIL import Image, ImageDraw
from skimage.metrics import peak_signal_noise_ratio, structural_similarity


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACK_DIR = REPO_ROOT / "public" / "data" / "tracks" / "fuji"
DEFAULT_TRACK_DEF = REPO_ROOT / "track-creator" / "tracks" / "fuji" / "track.json"
DEFAULT_NCNN_DIR = Path(__file__).resolve().parent / "tools" / "realesrgan-ncnn-vulkan"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "tools" / "models" / "4xNomosWebPhoto_RealPLKSR.pth"
DEFAULT_MODEL_NAME = "4xNomosWebPhoto_RealPLKSR"
DEFAULT_MODEL_LICENSE = "CC BY 4.0"
DEFAULT_MODEL_URL = ("https://github.com/Phhofm/models/releases/download/"
                     "4xNomosWebPhoto_RealPLKSR/4xNomosWebPhoto_RealPLKSR.pth")
METRES_PER_DEGREE = 111_320.0


@dataclass(frozen=True)
class CropSpec:
    name: str
    px: float
    py: float


def mercator_y(lat_deg: float) -> float:
    return math.asinh(math.tan(math.radians(lat_deg)))


def local_to_pixel(x: float, z: float, origin: dict, meta: dict) -> tuple[float, float]:
    """Match src/replay/projection.ts and groundMath.ts exactly."""
    lng_scale = METRES_PER_DEGREE * math.cos(math.radians(origin["lat"]))
    lat = origin["lat"] - z / METRES_PER_DEGREE
    lng = origin["lng"] + x / lng_scale
    bbox = meta["bbox"]
    u = (lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"])
    if meta.get("mercator", True):
        y_north = mercator_y(bbox["maxLat"])
        y_south = mercator_y(bbox["minLat"])
        v = (y_north - mercator_y(lat)) / (y_north - y_south)
    else:
        v = (bbox["maxLat"] - lat) / (bbox["maxLat"] - bbox["minLat"])
    return u * (meta["imageWidth"] - 1), v * (meta["imageHeight"] - 1)


def smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    t = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_inputs(args: argparse.Namespace) -> tuple[Image.Image, dict, dict, list[tuple[float, float]]]:
    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    track = json.loads(args.track_def.read_text(encoding="utf-8"))
    image = Image.open(args.input).convert("RGB")
    if image.size != (meta["imageWidth"], meta["imageHeight"]):
        raise SystemExit(f"image/meta size mismatch: image={image.size}, meta="
                         f"{meta['imageWidth']}x{meta['imageHeight']}")
    # track-creator files carry controlPoints; OSM-bootstrapped track.json
    # only has the (denser) centerline — either works as the corridor spine.
    spine = track.get("controlPoints") or track["centerline"]
    points = [local_to_pixel(p["x"], p["z"], track["origin"], meta)
              for p in spine]
    if track.get("closed", True) and points:
        points.append(points[0])
    return image, meta, track, points


def crop_with_reflect(image: np.ndarray, cx: float, cy: float, size: int) -> np.ndarray:
    half = size // 2
    x0, y0 = round(cx) - half, round(cy) - half
    x1, y1 = x0 + size, y0 + size
    left, top = max(0, -x0), max(0, -y0)
    right, bottom = max(0, x1 - image.shape[1]), max(0, y1 - image.shape[0])
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.shape[1], x1), min(image.shape[0], y1)
    crop = image[y0:y1, x0:x1]
    if left or top or right or bottom:
        crop = cv2.copyMakeBorder(crop, top, bottom, left, right, cv2.BORDER_REFLECT_101)
    return crop


def crop_rect_with_reflect(image: np.ndarray, x0: int, y0: int,
                           width: int, height: int) -> np.ndarray:
    """Crop an exact rectangle, reflecting pixels outside the image."""
    x1, y1 = x0 + width, y0 + height
    left, top = max(0, -x0), max(0, -y0)
    right, bottom = max(0, x1 - image.shape[1]), max(0, y1 - image.shape[0])
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(image.shape[1], x1), min(image.shape[0], y1)
    crop = image[cy0:cy1, cx0:cx1]
    if left or top or right or bottom:
        crop = cv2.copyMakeBorder(crop, top, bottom, left, right, cv2.BORDER_REFLECT_101)
    return crop


def ncnn_executable(ncnn_dir: Path) -> Path:
    return ncnn_dir / ("realesrgan-ncnn-vulkan.exe" if sys.platform == "win32"
                       else "realesrgan-ncnn-vulkan")


def run_ncnn(input_path: Path, output_path: Path, args: argparse.Namespace, scale: int = 4) -> None:
    exe = ncnn_executable(args.ncnn_dir)
    models = args.ncnn_dir / "models"
    if not exe.exists():
        raise SystemExit(f"Real-ESRGAN executable not found: {exe}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path.is_dir():
        output_path.mkdir(parents=True, exist_ok=True)
    command = [
        str(exe), "-i", str(input_path.resolve()), "-o", str(output_path.resolve()),
        "-n", "realesrgan-x4plus", "-s", str(scale), "-t", str(args.inference_tile),
        "-m", str(models.resolve()), "-g", str(args.gpu), "-j", "2:2:2", "-f", "png",
    ]
    completed = subprocess.run(command, cwd=args.ncnn_dir, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"Real-ESRGAN failed with exit code {completed.returncode}")


# ── spandrel engine (torch imported lazily so unit tests never need it) ─────

_SPANDREL_CACHE: dict[str, tuple[object, object]] = {}


def load_spandrel(args: argparse.Namespace) -> tuple[object, object]:
    """Load the .pth once per process and move it to the requested device."""
    key = str(args.model_path)
    if key in _SPANDREL_CACHE:
        return _SPANDREL_CACHE[key]
    import torch
    from spandrel import ImageModelDescriptor, ModelLoader

    if not args.model_path.exists():
        raise SystemExit(f"model weights not found: {args.model_path}\n"
                         f"download from: {args.model_url}")
    descriptor = ModelLoader().load_from_file(args.model_path)
    if not isinstance(descriptor, ImageModelDescriptor):
        raise SystemExit("expected a single-image super-resolution model")
    if descriptor.scale != 4:
        raise SystemExit(f"expected a 4x model, got {descriptor.scale}x")
    use_cuda = args.device == "cuda" and torch.cuda.is_available()
    if args.device == "cuda" and not use_cuda:
        print("  WARNING: CUDA unavailable — falling back to CPU inference", flush=True)
    device = torch.device("cuda" if use_cuda else "cpu")
    descriptor = descriptor.to(device).eval()
    _SPANDREL_CACHE[key] = (descriptor, device)
    return descriptor, device


def pre_sharpen(rgb: np.ndarray, amount: float, radius: float) -> np.ndarray:
    """Mild unsharp mask to counter the source's optical/JPEG softness BEFORE
    super-resolution, so the SR model receives a crisper edge to work from.
    ``amount``<=0 is an identity (preserves the historical fuji/okayama path).
    Position-preserving (per-pixel), and any excess is caught by the LR-
    consistency and line-shift gates downstream."""
    if amount <= 0.0:
        return rgb
    blur = cv2.GaussianBlur(rgb, (0, 0), radius)
    return np.clip(cv2.addWeighted(rgb, 1.0 + amount, blur, -amount, 0),
                   0, 255).astype(np.uint8)


def spandrel_upscale_x4(rgb: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    import torch

    amount = getattr(args, "pre_sharpen_amount", 0.0)
    radius = getattr(args, "pre_sharpen_radius", 1.0)
    rgb = pre_sharpen(rgb, amount, radius)
    model, device = load_spandrel(args)
    tensor = torch.from_numpy(np.ascontiguousarray(rgb)).to(device)
    tensor = tensor.permute(2, 0, 1).unsqueeze(0).float().div_(255.0)
    with torch.no_grad():
        out = model(tensor)
    # spandrel runs the model under inference_mode, so the result forbids
    # in-place ops — keep this chain out-of-place.
    out = out.squeeze(0).clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    return out.permute(1, 2, 0).cpu().numpy()


def model_info(args: argparse.Namespace) -> dict | None:
    """Provenance block for reports/manifests (None for non-ML engines)."""
    if args.engine != "spandrel":
        return None
    return {
        "name": args.model_name,
        "file": args.model_path.name,
        "sha256": source_sha256(args.model_path),
        "license": args.model_license,
        "url": args.model_url,
    }


class PerceptualMetrics:
    """pyiqa wrappers: LPIPS/DISTS (full-reference, lower = better) and
    MUSIQ/MANIQA (no-reference, higher = better).  PSNR/SSIM systematically
    prefer blur over correct texture (perception-distortion trade-off), so
    they are reported but never used for gating."""

    def __init__(self, device: str) -> None:
        import pyiqa
        import torch

        self._torch = torch
        use_cuda = device == "cuda" and torch.cuda.is_available()
        self.device = torch.device("cuda" if use_cuda else "cpu")
        self._lpips = pyiqa.create_metric("lpips", device=self.device)
        self._dists = pyiqa.create_metric("dists", device=self.device)
        self._musiq = pyiqa.create_metric("musiq", device=self.device)
        self._maniqa = pyiqa.create_metric("maniqa", device=self.device)

    def _tensor(self, rgb: np.ndarray):
        array = np.ascontiguousarray(rgb, dtype=np.float32) / 255.0
        return self._torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).to(self.device)

    def full_reference(self, candidate: np.ndarray, reference: np.ndarray) -> dict:
        with self._torch.no_grad():
            cand, ref = self._tensor(candidate), self._tensor(reference)
            return {"lpips": float(self._lpips(cand, ref).item()),
                    "dists": float(self._dists(cand, ref).item())}

    def no_reference(self, image: np.ndarray) -> dict:
        with self._torch.no_grad():
            tensor = self._tensor(image)
            return {"musiq": float(self._musiq(tensor).item()),
                    "maniqa": float(self._maniqa(tensor).item())}


# ── hallucination guards (torch-free, unit-tested) ──────────────────────────

def lr_consistency_psnr(enhanced_x2: np.ndarray, native: np.ndarray) -> float:
    """Re-downsampling the 2x output must reproduce the input.  A low value
    means the engine invented structure the source never contained."""
    down = cv2.resize(enhanced_x2, (native.shape[1], native.shape[0]),
                      interpolation=cv2.INTER_AREA)
    return float(peak_signal_noise_ratio(native, down, data_range=255))


def corridor_shift_stats(reference: np.ndarray, candidate: np.ndarray,
                         points: list[tuple[float, float]], window: int = 128) -> dict:
    """Sub-pixel translation between two renderings of the same source,
    measured by phase correlation in windows centred on `points`.  Large
    shifts mean the SR engine moved line-like structures sideways — which
    would visibly contradict the vector line layer drawn on top."""
    gray_ref = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray_cand = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY).astype(np.float32)
    hann = cv2.createHanningWindow((window, window), cv2.CV_32F)
    half = window // 2
    height, width = gray_ref.shape
    shifts: list[float] = []
    for px, py in points:
        x0, y0 = int(round(px)) - half, int(round(py)) - half
        if x0 < 0 or y0 < 0 or x0 + window > width or y0 + window > height:
            continue
        (dx, dy), _response = cv2.phaseCorrelate(
            gray_ref[y0:y0 + window, x0:x0 + window],
            gray_cand[y0:y0 + window, x0:x0 + window], hann)
        shifts.append(float(math.hypot(dx, dy)))
    if not shifts:
        return {"windows": 0, "meanPx": None, "p95Px": None, "maxPx": None}
    return {"windows": len(shifts),
            "meanPx": round(float(np.mean(shifts)), 4),
            "p95Px": round(float(np.percentile(shifts, 95)), 4),
            "maxPx": round(float(np.max(shifts)), 4)}


def select_strength(mean_curve: list[dict]) -> tuple[float, str]:
    """Perceptual selection: the strength with the lowest mean DISTS wins,
    LPIPS breaks ties.  Replaces the old 'strongest within PSNR/SSIM
    tolerance' rule, which always dragged the result toward Lanczos blur."""
    best = min(mean_curve, key=lambda row: (row["dists"], row["lpips"]))
    return float(best["strength"]), "argmin mean DISTS over strengths, LPIPS tie-break"


def enhance_array(rgb: np.ndarray, args: argparse.Namespace, work_dir: Path, stem: str,
                  sr_strength: float | None = None) -> np.ndarray:
    """Return a 2x RGB result using the requested engine."""
    target_size = (rgb.shape[1] * 2, rgb.shape[0] * 2)
    lanczos = cv2.resize(rgb, target_size, interpolation=cv2.INTER_LANCZOS4)
    if args.engine == "lanczos":
        blur = cv2.GaussianBlur(lanczos, (0, 0), 0.8)
        return np.clip(cv2.addWeighted(lanczos, 1.25, blur, -0.25, 0), 0, 255).astype(np.uint8)

    if args.engine == "spandrel":
        sr_x4 = spandrel_upscale_x4(rgb, args)
    else:
        input_path = work_dir / f"{stem}_input.png"
        output_path = work_dir / f"{stem}_x4.png"
        Image.fromarray(rgb).save(input_path)
        run_ncnn(input_path, output_path, args, scale=4)
        sr_x4 = np.asarray(Image.open(output_path).convert("RGB"))
    sr_x2 = cv2.resize(sr_x4, target_size, interpolation=cv2.INTER_LANCZOS4)
    # Blending toward Lanczos suppresses residual artifacts; the validation
    # mode picks the strength by perceptual metrics (DISTS/LPIPS) plus
    # hallucination guards.  1.0 is raw SR and 0.0 is pure Lanczos.
    strength = args.sr_strength if sr_strength is None else sr_strength
    return cv2.addWeighted(sr_x2, strength, lanczos, 1.0 - strength, 0)


def labelled_panel(images: list[tuple[str, np.ndarray]], output: Path) -> None:
    h = max(image.shape[0] for _, image in images)
    width = sum(image.shape[1] for _, image in images)
    canvas = Image.new("RGB", (width, h + 42), "#101418")
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, array in images:
        panel = Image.fromarray(array)
        canvas.paste(panel, (x, 42))
        draw.text((x + 12, 12), label, fill="white")
        x += panel.width
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=92)


def validation_crops(points: list[tuple[float, float]]) -> list[CropSpec]:
    indices = [0, len(points) // 3, (2 * len(points)) // 3]
    names = ["home_straight", "middle_sector", "final_sector"]
    return [CropSpec(name, *points[index]) for name, index in zip(names, indices)]


GATE_LR_CONSISTENCY_MIN_PSNR_DB = 30.0
GATE_LINE_SHIFT_MAX_P95_PX = 0.5  # output px; 0.5 px = 5 cm << 20 cm line width


def shift_probe_points(size: int, window: int) -> list[tuple[float, float]]:
    """3x3 probe grid inside a `size`-square image, spaced so `window`-sized
    patches stay in bounds."""
    lo, mid, hi = window / 2, size / 2, size - window / 2
    return [(x, y) for y in (lo, mid, hi) for x in (lo, mid, hi)]


def run_validation(image: Image.Image, meta: dict, points: list[tuple[float, float]],
                   args: argparse.Namespace) -> dict:
    out_dir = args.output / "validation"
    work_dir = args.output / "_work" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(image)
    metrics = PerceptualMetrics(args.device)
    strengths = [round(float(s), 2) for s in np.linspace(0.0, 1.0, 21)]
    samples = []
    crops = []  # (spec, native, lanczos, sr_raw) for the real-scale gate loop

    for spec in validation_crops(points):
        native = crop_with_reflect(rgb, spec.px, spec.py, args.crop_size)
        lanczos = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)
        sr_raw = enhance_array(native, args, work_dir, spec.name, sr_strength=1.0)
        crops.append((spec, native, lanczos, sr_raw))

        # Synthetic holdout: 20 cm native acts as truth, 40 cm is restored.
        degraded = cv2.resize(native, (native.shape[1] // 2, native.shape[0] // 2),
                              interpolation=cv2.INTER_AREA)
        baseline = cv2.resize(degraded, (native.shape[1], native.shape[0]),
                              interpolation=cv2.INTER_LANCZOS4)
        restored_raw_x2 = enhance_array(
            degraded, args, work_dir, f"{spec.name}_holdout", sr_strength=1.0,
        )
        restored_raw = cv2.resize(restored_raw_x2, (native.shape[1], native.shape[0]),
                                  interpolation=cv2.INTER_LANCZOS4)

        base_row = {
            "psnrDb": round(float(peak_signal_noise_ratio(native, baseline, data_range=255)), 4),
            "ssim": round(float(structural_similarity(native, baseline, channel_axis=2,
                                                      data_range=255)), 6),
        }
        base_row.update({k: round(v, 6) for k, v
                         in metrics.full_reference(baseline, native).items()})

        strength_curve = []
        for strength in strengths:
            candidate = cv2.addWeighted(restored_raw, strength, baseline, 1.0 - strength, 0)
            row = {
                "strength": strength,
                "psnrDb": round(float(peak_signal_noise_ratio(native, candidate,
                                                              data_range=255)), 4),
                "ssim": round(float(structural_similarity(native, candidate, channel_axis=2,
                                                          data_range=255)), 6),
            }
            row.update({k: round(v, 6) for k, v
                        in metrics.full_reference(candidate, native).items()})
            strength_curve.append(row)

        samples.append({
            "name": spec.name,
            "centerPixel": [round(spec.px, 2), round(spec.py, 2)],
            "baselineLanczos": base_row,
            "strengthCurve": strength_curve,
        })

    mean_curve = []
    for index, strength in enumerate(strengths):
        mean_curve.append({
            "strength": strength,
            **{key: round(float(np.mean([s["strengthCurve"][index][key] for s in samples])), 6)
               for key in ("psnrDb", "ssim", "lpips", "dists")},
        })
    baseline_mean = {key: round(float(np.mean([s["baselineLanczos"][key] for s in samples])), 6)
                     for key in ("psnrDb", "ssim", "lpips", "dists")}

    selected, rule = select_strength(mean_curve)
    initial_selection = selected

    # Hallucination guards on REAL-scale output (not the holdout): reduce the
    # strength until both pass.  The Lanczos rendering is the geometric
    # reference — it cannot move structures by construction.
    gate_history = []
    while True:
        lr_values, shift_p95s = [], []
        for _spec, native, lanczos, sr_raw in crops:
            enhanced = cv2.addWeighted(sr_raw, selected, lanczos, 1.0 - selected, 0)
            lr_values.append(lr_consistency_psnr(enhanced, native))
            stats = corridor_shift_stats(
                lanczos, enhanced, shift_probe_points(enhanced.shape[0], 128))
            if stats["p95Px"] is not None:
                shift_p95s.append(stats["p95Px"])
        lr_mean = round(float(np.mean(lr_values)), 4)
        shift_p95 = round(float(np.max(shift_p95s)), 4) if shift_p95s else None
        passed = (lr_mean >= GATE_LR_CONSISTENCY_MIN_PSNR_DB
                  and (shift_p95 is None or shift_p95 <= GATE_LINE_SHIFT_MAX_P95_PX))
        gate_history.append({"strength": selected, "lrConsistencyPsnrDb": lr_mean,
                             "lineShiftP95Px": shift_p95, "passed": passed})
        if passed or selected <= 0.0:
            break
        selected = round(max(0.0, selected - 0.05), 2)

    # Real-scale artifacts + no-reference sharpness comparison at the final
    # strength (MUSIQ/MANIQA higher = better perceived quality).
    no_reference = []
    for spec, native, lanczos, sr_raw in crops:
        enhanced = cv2.addWeighted(sr_raw, selected, lanczos, 1.0 - selected, 0)
        native_pixels = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        labelled_panel([
            ("Native 20 cm pixels", native_pixels),
            ("Lanczos 2x", lanczos),
            (f"{args.engine} 2x s={selected} (visual only)", enhanced),
        ], out_dir / f"{spec.name}_comparison.jpg")
        Image.fromarray(enhanced).save(out_dir / f"{spec.name}_{args.engine}_x2.webp",
                                       format="WEBP", quality=args.quality, method=6)
        no_reference.append({
            "name": spec.name,
            "lanczos": {k: round(v, 4) for k, v in metrics.no_reference(lanczos).items()},
            "enhanced": {k: round(v, 4) for k, v in metrics.no_reference(enhanced).items()},
        })

    report = {
        "purpose": "visual validation only; not surveyed truth or measurement data",
        "engine": args.engine,
        "model": model_info(args),
        "sourceResolutionMetersPerPixel": meta.get("effectiveResolutionMetersPerPixel", 0.2192),
        "outputGridMetersPerPixel": meta.get("effectiveResolutionMetersPerPixel", 0.2192) / 2,
        "selectionRule": rule,
        "preSharpen": {"amount": args.pre_sharpen_amount, "radius": args.pre_sharpen_radius},
        "gates": {
            "lrConsistencyMinPsnrDb": GATE_LR_CONSISTENCY_MIN_PSNR_DB,
            "lineShiftMaxP95Px": GATE_LINE_SHIFT_MAX_P95_PX,
        },
        "initialSelectedStrength": initial_selection,
        "recommendedSrStrength": selected,
        "gateHistory": gate_history,
        "baselineLanczosMean": baseline_mean,
        "meanStrengthCurve": mean_curve,
        "noReferenceRealScale": no_reference,
        "samples": samples,
    }
    (out_dir / "validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    return report


def build_corridor_alpha(width: int, height: int, points: list[tuple[float, float]],
                         metres_per_pixel: float, core_metres: float,
                         outer_metres: float) -> np.ndarray:
    line = np.full((height, width), 255, dtype=np.uint8)
    polyline = np.round(np.asarray(points)).astype(np.int32).reshape((-1, 1, 2))
    cv2.polylines(line, [polyline], False, 0, thickness=3, lineType=cv2.LINE_8)
    distance_px = cv2.distanceTransform(line, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    core_px = core_metres / metres_per_pixel
    outer_px = outer_metres / metres_per_pixel
    alpha = 1.0 - smoothstep(core_px, outer_px, distance_px)
    return np.round(alpha * 255).astype(np.uint8)


def run_tiles(image: Image.Image, meta: dict, points: list[tuple[float, float]],
              args: argparse.Namespace) -> dict:
    out_dir = args.output
    tiles_dir = out_dir / "tiles"
    work_dir = out_dir / "_work" / "tiles"
    if tiles_dir.exists():
        shutil.rmtree(tiles_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(image)
    mpp = float(meta.get("effectiveResolutionMetersPerPixel", 0.2192))
    # Cache-busting stamp: tile FILENAMES are grid indices and stay identical
    # across regenerations even when the pixel grid (and therefore each
    # tile's geographic footprint) changes. Long-running browser sessions
    # mixing cached old tiles with a new manifest rendered imagery tens of
    # metres off — the ?v= query keys each generation separately.
    stamp = time.strftime("%Y%m%dT%H%M%S")
    alpha = build_corridor_alpha(image.width, image.height, points, mpp,
                                 args.core_metres, args.outer_metres)
    scale = args.output_scale
    source_tile = args.tile_size // scale
    margin = args.context
    candidates = []

    for sy in range(0, image.height, source_tile):
        for sx in range(0, image.width, source_tile):
            sw = min(source_tile, image.width - sx)
            sh = min(source_tile, image.height - sy)
            alpha_core = alpha[sy:sy + sh, sx:sx + sw]
            if int(alpha_core.max()) == 0:
                continue
            context = crop_rect_with_reflect(
                rgb, sx - margin, sy - margin, sw + margin * 2, sh + margin * 2,
            )
            candidates.append((sx, sy, sw, sh, context))

    raw_dir = work_dir / "raw_x4"
    input_dir = work_dir / "input"
    if args.engine == "realesrgan":
        input_dir.mkdir(parents=True, exist_ok=True)
        for index, (_sx, _sy, _sw, _sh, context) in enumerate(candidates):
            Image.fromarray(context).save(input_dir / f"{index:04d}.png")
        run_ncnn(input_dir, raw_dir, args, scale=4)

    def raw_x4_for(index: int, context: np.ndarray) -> np.ndarray | None:
        if args.engine == "realesrgan":
            return np.asarray(Image.open(raw_dir / f"{index:04d}.png").convert("RGB"))
        if args.engine == "spandrel":
            return spandrel_upscale_x4(context, args)
        return None

    records = []
    for index, (sx, sy, sw, sh, context) in enumerate(candidates):
            context_out_size = (context.shape[1] * scale, context.shape[0] * scale)
            lanczos_context = cv2.resize(context, context_out_size, interpolation=cv2.INTER_LANCZOS4)
            raw_x4 = raw_x4_for(index, context)
            if raw_x4 is not None:
                # raw_x4 is 4x; resample to the requested output scale (identity
                # for scale=4, downscale for scale=2). This keeps the SR grid
                # native (0.12 m/px) when scale=4 instead of re-softening to x2.
                sr_context = cv2.resize(raw_x4, context_out_size, interpolation=cv2.INTER_LANCZOS4)
                enhanced_context = cv2.addWeighted(
                    sr_context, args.sr_strength, lanczos_context, 1.0 - args.sr_strength, 0,
                )
            else:
                blur = cv2.GaussianBlur(lanczos_context, (0, 0), 0.8)
                enhanced_context = np.clip(
                    cv2.addWeighted(lanczos_context, 1.25, blur, -0.25, 0), 0, 255,
                ).astype(np.uint8)
            offset = margin * scale
            enhanced = enhanced_context[offset:offset + sh * scale, offset:offset + sw * scale]
            alpha_core = alpha[sy:sy + sh, sx:sx + sw]
            alpha_out = cv2.resize(alpha_core, (sw * scale, sh * scale), interpolation=cv2.INTER_LANCZOS4)
            rgba = np.dstack([enhanced, alpha_out])
            filename = f"{sx // source_tile}_{sy // source_tile}.webp"
            Image.fromarray(rgba, "RGBA").save(tiles_dir / filename, format="WEBP",
                                               quality=args.quality, method=6)
            records.append({
                "file": f"tiles/{filename}?v={stamp}",
                "sourcePixel": {"x": sx, "y": sy, "width": sw, "height": sh},
                "outputPixel": {"width": sw * scale, "height": sh * scale},
                "uv": {
                    "u0": sx / image.width, "v0": sy / image.height,
                    "u1": (sx + sw) / image.width, "v1": (sy + sh) / image.height,
                },
            })
            print(f"  tile {index + 1}/{len(candidates)}: {filename}", flush=True)

    manifest = {
        "schemaVersion": 1,
        "kind": "track-corridor-enhancement",
        "engine": args.engine,
        "model": model_info(args),
        "srStrength": args.sr_strength if args.engine != "lanczos" else 0,
        "preSharpen": {"amount": args.pre_sharpen_amount, "radius": args.pre_sharpen_radius},
        "visualizationOnly": True,
        "prohibitedUses": ["geometry extraction", "distance measurement", "survey validation"],
        "source": meta.get("source"),
        "sourceUrl": meta.get("sourceUrl"),
        "license": meta.get("license"),
        "sourceImage": args.input.name,
        "sourceSha256": source_sha256(args.input),
        "bbox": meta["bbox"],
        "mercator": meta.get("mercator", True),
        "sourceImageSize": [image.width, image.height],
        "scale": scale,
        "tileSize": args.tile_size,
        "coreMetres": args.core_metres,
        "outerMetres": args.outer_metres,
        "sourceResolutionMetersPerPixel": mpp,
        "outputGridMetersPerPixel": mpp / scale,
        "nativeInformationResolutionMetersPerPixel": mpp,
        "tileCount": len(records),
        "tiles": records,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["validation", "tiles"], default="validation")
    parser.add_argument("--engine", choices=["spandrel", "realesrgan", "lanczos"],
                        default="spandrel")
    parser.add_argument("--input", type=Path, default=DEFAULT_TRACK_DIR / "satellite_shizuoka.jpg")
    parser.add_argument("--meta", type=Path, default=DEFAULT_TRACK_DIR / "satellite_shizuoka_meta.json")
    parser.add_argument("--track-def", type=Path, default=DEFAULT_TRACK_DEF)
    parser.add_argument("--output", type=Path,
                        default=DEFAULT_TRACK_DIR / "satellite_corridor_x2")
    parser.add_argument("--ncnn-dir", type=Path, default=DEFAULT_NCNN_DIR)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH,
                        help="spandrel-loadable 4x .pth (default: 4xNomosWebPhoto_RealPLKSR)")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-license", default=DEFAULT_MODEL_LICENSE)
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--inference-tile", type=int, default=256)
    parser.add_argument("--crop-size", type=int, default=512)
    parser.add_argument("--tile-size", type=int, default=1024,
                        help="Final output tile size in px; must be divisible "
                             "by --output-scale")
    parser.add_argument("--output-scale", type=int, default=2, choices=[2, 4],
                        help="Output grid multiplier over the source. 2 (default) "
                             "keeps the historical fuji/okayama 2x grid; 4 ships "
                             "the model's native x4 grid (finer display grid, "
                             "~4x tile bytes).")
    parser.add_argument("--context", type=int, default=64,
                        help="Native-resolution context pixels around each tile "
                             "(receptive-field margin against tile seams)")
    parser.add_argument("--core-metres", type=float, default=80.0)
    parser.add_argument("--outer-metres", type=float, default=140.0)
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--sr-strength", type=float, default=1.0,
                        help="SR blend in [0,1]; 0=Lanczos, 1=raw SR. Use the "
                             "value recommended by --mode validation")
    parser.add_argument("--pre-sharpen-amount", type=float, default=0.0,
                        help="Unsharp amount applied to the SR input before the "
                             "4x model (0=off, preserves fuji/okayama). Counters "
                             "soft GSI web-tile imagery; excess is caught by the "
                             "LR-consistency and line-shift gates.")
    parser.add_argument("--pre-sharpen-radius", type=float, default=1.0,
                        help="Gaussian radius (px) for --pre-sharpen-amount")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()
    if args.tile_size % args.output_scale:
        parser.error("--tile-size must be divisible by --output-scale")
    if not 0 <= args.quality <= 100:
        parser.error("--quality must be between 0 and 100")
    if not 0 <= args.sr_strength <= 1:
        parser.error("--sr-strength must be between 0 and 1")
    if not 0 < args.core_metres < args.outer_metres:
        parser.error("require 0 < --core-metres < --outer-metres")
    return args


def main() -> int:
    args = parse_args()
    started = time.time()
    image, meta, _track, points = read_inputs(args)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "validation":
        result = run_validation(image, meta, points, args)
        count = len(result["samples"])
        print(f"Validation complete: {count} samples")
    else:
        result = run_tiles(image, meta, points, args)
        print(f"Corridor complete: {result['tileCount']} tiles")
    print(f"Output: {args.output}")
    print(f"Elapsed: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
