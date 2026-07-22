"""Grade candidate sweep for the Suzuka GSI mosaic.

No-reference perceptual scoring (MUSIQ/MANIQA, higher=better) on 3 track-
corridor crops, plus before/after panels. Position-preserving grade only.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(r"D:\00_Dev\ReplayViewer")
sys.path.insert(0, str(REPO))
from pipeline.grade_ortho import GradeParams, grade_image
from pipeline.enhance_track_corridor import local_to_pixel, PerceptualMetrics, crop_with_reflect

NATIVE = REPO / "pipeline/cache/gsi_suzuka/native_mosaic.png"
META = REPO / "pipeline/cache/gsi_suzuka/native_mosaic_meta.json"
TRACK = REPO / "public/data/tracks/suzuka/track.json"
TEMP = REPO / "Temp"
TEMP.mkdir(exist_ok=True)

Image.MAX_IMAGE_PIXELS = None
meta = json.loads(META.read_text(encoding="utf-8"))
track = json.loads(TRACK.read_text(encoding="utf-8"))
img = np.asarray(Image.open(NATIVE).convert("RGB"))

spine = track.get("controlPoints") or track["centerline"]
pts = [local_to_pixel(p["x"], p["z"], track["origin"], meta) for p in spine]
idx = [0, len(pts) // 3, 2 * len(pts) // 3]
names = ["home_straight", "middle_sector", "final_sector"]
centers = [(names[i], pts[j]) for i, j in enumerate(idx)]

CANDS = {
    "C0_default":  GradeParams(),
    "C1_punchy":   GradeParams(clahe_clip=2.6, clahe_tile_px=96,  saturation=1.18, stretch_strength=0.95),
    "C2_strong":   GradeParams(clahe_clip=3.5, clahe_tile_px=80,  saturation=1.22, stretch_strength=1.0),
    "C3_mild":     GradeParams(clahe_clip=2.2, clahe_tile_px=128, saturation=1.16, stretch_strength=0.90),
}

metrics = PerceptualMetrics("cuda")
CROP = 512
rows = {}
graded_cache = {}
for cname, params in CANDS.items():
    graded = grade_image(img, params)
    graded_cache[cname] = graded
    musiq, maniqa = [], []
    for _n, (px, py) in centers:
        crop = crop_with_reflect(graded, px, py, CROP)
        nr = metrics.no_reference(crop)
        musiq.append(nr["musiq"]); maniqa.append(nr["maniqa"])
    rows[cname] = {"musiq": round(float(np.mean(musiq)), 3),
                   "maniqa": round(float(np.mean(maniqa)), 4),
                   "musiq_each": [round(m, 2) for m in musiq]}
    print(cname, rows[cname], flush=True)

# panels: native vs each candidate, per crop
for n, (px, py) in centers:
    nat = crop_with_reflect(img, px, py, CROP)
    strip = [("native", nat)] + [(c, crop_with_reflect(graded_cache[c], px, py, CROP)) for c in CANDS]
    h = CROP
    canvas = Image.new("RGB", (CROP * len(strip), h + 24), "#101418")
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    for k, (lbl, a) in enumerate(strip):
        canvas.paste(Image.fromarray(a), (k * CROP, 24))
        d.text((k * CROP + 6, 6), lbl, fill="white")
    canvas.save(TEMP / f"suzuka-improve-grade-{n}.jpg", quality=92)

(REPO / "scratchpad/exp_grade_result.json").write_text(json.dumps(rows, indent=2))
print("DONE")
