"""Final adopted config vs old shipped: x4@0.45 vs x2@0.6, measured at the
on-screen (x4) display size, with before/after panels."""
import json, sys
from pathlib import Path
import cv2, numpy as np
from PIL import Image, ImageDraw
REPO = Path(r"D:\00_Dev\ReplayViewer"); sys.path.insert(0, str(REPO))
from pipeline import enhance_track_corridor as E
sys.argv = ["x", "--input", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded.png"),
            "--meta", str(REPO/"pipeline/cache/gsi_suzuka/native_mosaic_graded_meta.json"),
            "--track-def", str(REPO/"public/data/tracks/suzuka/track.json"),
            "--output", str(REPO/"Temp/final")]
args = E.parse_args(); args.pre_sharpen_amount = 0.0
image, meta, track, points = E.read_inputs(args)
rgb = np.asarray(image)
metrics = E.PerceptualMetrics("cuda")
TEMP = REPO/"Temp"; rows=[]
for spec in E.validation_crops(points):
    native = E.crop_with_reflect(rgb, spec.px, spec.py, args.crop_size)
    sr_x4 = E.spandrel_upscale_x4(native, args)
    lanc_x4 = cv2.resize(native, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
    lanc_x2 = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)
    sr_x2 = cv2.resize(sr_x4, (native.shape[1]*2, native.shape[0]*2), interpolation=cv2.INTER_LANCZOS4)
    old_x2 = cv2.addWeighted(sr_x2, 0.6, lanc_x2, 0.4, 0)            # OLD shipped
    new_x4 = cv2.addWeighted(sr_x4, 0.45, lanc_x4, 0.55, 0)         # NEW shipped
    old_shown = cv2.resize(old_x2, (new_x4.shape[1], new_x4.shape[0]), interpolation=cv2.INTER_LINEAR)
    m_old = metrics.no_reference(old_shown); m_new = metrics.no_reference(new_x4)
    rows.append({"crop": spec.name,
                 "old_x2_musiq": round(m_old["musiq"],2), "new_x4_musiq": round(m_new["musiq"],2),
                 "old_x2_maniqa": round(m_old["maniqa"],4), "new_x4_maniqa": round(m_new["maniqa"],4)})
    print(json.dumps(rows[-1]), flush=True)
    c = new_x4.shape[0]//2; win=360
    a = old_shown[c-win:c+win, c-win:c+win]; b = new_x4[c-win:c+win, c-win:c+win]
    canvas = Image.new("RGB", (win*4, win*2+26), "#101418"); d=ImageDraw.Draw(canvas)
    canvas.paste(Image.fromarray(a),(0,26)); canvas.paste(Image.fromarray(b),(win*2,26))
    d.text((6,7), f"OLD x2 grid @0.6 (0.245 m/px)  MUSIQ {m_old['musiq']:.1f}", fill="white")
    d.text((win*2+6,7), f"NEW x4 grid @0.45 (0.123 m/px)  MUSIQ {m_new['musiq']:.1f}", fill="white")
    canvas.save(TEMP/f"suzuka-improve-final-{spec.name}.jpg", quality=93)
mean={"old_x2_musiq":round(np.mean([r['old_x2_musiq'] for r in rows]),2),
      "new_x4_musiq":round(np.mean([r['new_x4_musiq'] for r in rows]),2),
      "old_x2_maniqa":round(np.mean([r['old_x2_maniqa'] for r in rows]),4),
      "new_x4_maniqa":round(np.mean([r['new_x4_maniqa'] for r in rows]),4)}
print("MEAN", json.dumps(mean))
(REPO/"scratchpad/final_panels_result.json").write_text(json.dumps({"rows":rows,"mean":mean},indent=2))
