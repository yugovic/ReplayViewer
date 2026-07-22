"""Find the highest SR strength whose x4 output passes BOTH gates
(LR-consistency >=30 dB, line-shift p95 <=0.5 output px)."""
import json, sys
from pathlib import Path
import cv2, numpy as np
REPO = Path(r"D:\00_Dev\ReplayViewer"); sys.path.insert(0, str(REPO))
from pipeline import enhance_track_corridor as E
sys.argv = ["x", "--input", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded.png"),
            "--meta", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded_meta.json"),
            "--track-def", str(REPO/"public/data/tracks/suzuka/track.json"),
            "--output", str(REPO/"Temp/x4str")]
args = E.parse_args(); args.pre_sharpen_amount = 0.0
image, meta, track, points = E.read_inputs(args)
rgb = np.asarray(image)
# precompute per-crop sr_x4 & lanc_x4 once
crops = []
for spec in E.validation_crops(points):
    native = E.crop_with_reflect(rgb, spec.px, spec.py, args.crop_size)
    sr_x4 = E.spandrel_upscale_x4(native, args)
    lanc_x4 = cv2.resize(native, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
    crops.append((native, sr_x4, lanc_x4))
out = []
for STR in [0.6, 0.55, 0.5, 0.45, 0.4]:
    lr, sh = [], []
    for native, sr_x4, lanc_x4 in crops:
        enh = cv2.addWeighted(sr_x4, STR, lanc_x4, 1.0-STR, 0)
        down = cv2.resize(enh, (native.shape[1], native.shape[0]), interpolation=cv2.INTER_AREA)
        lr.append(E.lr_consistency_psnr(down, native))
        st = E.corridor_shift_stats(lanc_x4, enh, E.shift_probe_points(enh.shape[0], 128))
        if st["p95Px"] is not None: sh.append(st["p95Px"])
    row = {"strength": STR, "lrMean": round(float(np.mean(lr)),3), "lrMin": round(float(np.min(lr)),3),
           "shiftP95": round(float(np.max(sh)),4),
           "pass": bool(np.mean(lr)>=30.0 and np.max(sh)<=0.5)}
    out.append(row); print(json.dumps(row), flush=True)
(REPO/"scratchpad/x4_strength_result.json").write_text(json.dumps(out, indent=2))
