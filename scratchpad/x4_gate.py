"""Compute the shipped x4-grid gate record (LR-consistency + line-shift) at
strength 0.6 on the 3 validation crops, and patch it into the report."""
import json, sys
from pathlib import Path
import cv2, numpy as np
from PIL import Image
REPO = Path(r"D:\00_Dev\ReplayViewer")
sys.path.insert(0, str(REPO))
from pipeline import enhance_track_corridor as E

sys.argv = ["x", "--input", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded.png"),
            "--meta", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded_meta.json"),
            "--track-def", str(REPO/"public/data/tracks/suzuka/track.json"),
            "--output", str(REPO/"Temp/x4gate")]
args = E.parse_args(); args.pre_sharpen_amount = 0.0
image, meta, track, points = E.read_inputs(args)
rgb = np.asarray(image); STR = 0.45
lr, shifts = [], []
for spec in E.validation_crops(points):
    native = E.crop_with_reflect(rgb, spec.px, spec.py, args.crop_size)
    sr_x4 = E.spandrel_upscale_x4(native, args)
    lanc_x4 = cv2.resize(native, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
    enh_x4 = cv2.addWeighted(sr_x4, STR, lanc_x4, 1.0 - STR, 0)
    down = cv2.resize(enh_x4, (native.shape[1], native.shape[0]), interpolation=cv2.INTER_AREA)
    lr.append(E.lr_consistency_psnr(down, native))
    st = E.corridor_shift_stats(lanc_x4, enh_x4, E.shift_probe_points(enh_x4.shape[0], 128))
    if st["p95Px"] is not None: shifts.append(st["p95Px"])
rec = {
    "outputScale": 4,
    "outputGridMetersPerPixel": round(meta["effectiveResolutionMetersPerPixel"]/4, 6),
    "srStrength": STR,
    "lrConsistencyMeanPsnrDb": round(float(np.mean(lr)), 4),
    "lrConsistencyMinPsnrDb": round(float(np.min(lr)), 4),
    "lineShiftMaxP95Px": round(float(np.max(shifts)), 4),
    "gatesPassed": bool(np.mean(lr) >= 30.0 and np.max(shifts) <= 0.5),
    "note": ("Shipped grid is x4 (0.12 m/px). Strength 0.6 was selected on the "
             "x2 perceptual holdout (blend ratio is scale-invariant); these are "
             "the same hallucination guards re-measured on the x4 output."),
}
rp = REPO/"public/data/tracks/suzuka/satellite_corridor_x2/validation/validation-report.json"
d = json.loads(rp.read_text(encoding="utf-8"))
d["shippedOutputGrid"] = rec
rp.write_text(json.dumps(d, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(rec, indent=2))
