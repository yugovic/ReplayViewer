"""x2 vs x4 output-grid comparison at matched on-screen size.

For each corridor crop, build the current x2 tile (strength 0.6) and the full
x4 tile (strength 0.6), then measure MUSIQ/MANIQA at the SAME display size
(x4 pixels) so the score reflects what the viewer sees when zoomed in. x2 is
upsampled to x4 the way the GPU sampler would (bilinear/lanczos)."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

REPO = Path(r"D:\00_Dev\ReplayViewer")
sys.path.insert(0, str(REPO))
from pipeline import enhance_track_corridor as E

sys.argv = ["x", "--input", str(REPO / "pipeline/cache/gsi_suzuka/native_mosaic_graded.png"),
            "--meta", str(REPO / "pipeline/cache/gsi_suzuka/native_mosaic_graded_meta.json"),
            "--track-def", str(REPO / "public/data/tracks/suzuka/track.json"),
            "--output", str(REPO / "Temp/suzuka_x4_tmp")]
args = E.parse_args()
args.pre_sharpen_amount = 0.0
image, meta, track, points = E.read_inputs(args)
rgb = np.asarray(image)
metrics = E.PerceptualMetrics("cuda")
STR = 0.6
CROP = 512
crops = E.validation_crops(points)
TEMP = REPO / "Temp"; TEMP.mkdir(exist_ok=True)
rows = []
for spec in crops:
    native = E.crop_with_reflect(rgb, spec.px, spec.py, CROP)
    sr_x4 = E.spandrel_upscale_x4(native, args)  # 2048
    lanc_x4 = cv2.resize(native, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
    enh_x4 = cv2.addWeighted(sr_x4, STR, lanc_x4, 1.0 - STR, 0)
    # current pipeline x2 result:
    lanc_x2 = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)
    sr_x2 = cv2.resize(sr_x4, (native.shape[1] * 2, native.shape[0] * 2), interpolation=cv2.INTER_LANCZOS4)
    enh_x2 = cv2.addWeighted(sr_x2, STR, lanc_x2, 1.0 - STR, 0)
    # display x2 at x4 size (what the sampler shows when you zoom past x2 grid):
    x2_shown = cv2.resize(enh_x2, (enh_x4.shape[1], enh_x4.shape[0]), interpolation=cv2.INTER_LINEAR)
    m_x4 = metrics.no_reference(enh_x4)
    m_x2 = metrics.no_reference(x2_shown)
    # LR-consistency of x4 output vs native (guard)
    lr_x4 = E.lr_consistency_psnr(cv2.resize(enh_x4, (native.shape[1], native.shape[0]), interpolation=cv2.INTER_AREA), native)
    rows.append({"crop": spec.name,
                 "x2_musiq": round(m_x2["musiq"], 2), "x4_musiq": round(m_x4["musiq"], 2),
                 "x2_maniqa": round(m_x2["maniqa"], 4), "x4_maniqa": round(m_x4["maniqa"], 4),
                 "x4_lrDb": round(lr_x4, 3)})
    print(json.dumps(rows[-1]), flush=True)
    # panel: crop center 640px region of x2_shown vs x4
    c = enh_x4.shape[0] // 2
    a = x2_shown[c-320:c+320, c-320:c+320]; b = enh_x4[c-320:c+320, c-320:c+320]
    canvas = Image.new("RGB", (1280, 664), "#101418"); d = ImageDraw.Draw(canvas)
    canvas.paste(Image.fromarray(a), (0, 24)); canvas.paste(Image.fromarray(b), (640, 24))
    d.text((6, 6), f"x2 grid shown @x4  MUSIQ {m_x2['musiq']:.1f}", fill="white")
    d.text((646, 6), f"x4 grid  MUSIQ {m_x4['musiq']:.1f}", fill="white")
    canvas.save(TEMP / f"suzuka-improve-x4-{spec.name}.jpg", quality=92)

mean = {"x2_musiq": round(np.mean([r['x2_musiq'] for r in rows]), 2),
        "x4_musiq": round(np.mean([r['x4_musiq'] for r in rows]), 2),
        "x2_maniqa": round(np.mean([r['x2_maniqa'] for r in rows]), 4),
        "x4_maniqa": round(np.mean([r['x4_maniqa'] for r in rows]), 4)}
print("MEAN", json.dumps(mean))
(REPO / "scratchpad/exp_x4_result.json").write_text(json.dumps({"rows": rows, "mean": mean}, indent=2))
print("DONE")
