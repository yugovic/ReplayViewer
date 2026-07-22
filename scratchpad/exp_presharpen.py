"""Pre-SR unsharp sweep on the Suzuka graded mosaic via the real validation
(DISTS/LPIPS/MUSIQ/MANIQA + LR-consistency & line-shift gates)."""
import json
import sys
from pathlib import Path

REPO = Path(r"D:\00_Dev\ReplayViewer")
sys.path.insert(0, str(REPO))
from pipeline import enhance_track_corridor as E

argv = [
    "--mode", "validation",
    "--input", str(REPO / "pipeline/cache/gsi_suzuka/native_mosaic_graded.png"),
    "--meta", str(REPO / "pipeline/cache/gsi_suzuka/native_mosaic_graded_meta.json"),
    "--track-def", str(REPO / "public/data/tracks/suzuka/track.json"),
    "--output", str(REPO / "Temp/suzuka_val_tmp"),
]
sys.argv = ["enhance_track_corridor.py"] + argv
args = E.parse_args()
image, meta, _track, points = E.read_inputs(args)

AMOUNTS = [0.0, 0.3, 0.5, 0.7]
summary = []
for amt in AMOUNTS:
    args.pre_sharpen_amount = amt
    args.output = REPO / f"Temp/suzuka_val_a{int(amt*100):02d}"
    args.output.mkdir(parents=True, exist_ok=True)
    rep = E.run_validation(image, meta, points, args)
    gh = rep["gateHistory"][-1]
    best = min(rep["meanStrengthCurve"], key=lambda r: (r["dists"], r["lpips"]))
    nr = rep["noReferenceRealScale"]
    musiq = round(sum(x["enhanced"]["musiq"] for x in nr) / len(nr), 2)
    maniqa = round(sum(x["enhanced"]["maniqa"] for x in nr) / len(nr), 4)
    lanc_musiq = round(sum(x["lanczos"]["musiq"] for x in nr) / len(nr), 2)
    row = {
        "amount": amt,
        "selStrength": rep["recommendedSrStrength"],
        "bestDISTS": best["dists"], "bestLPIPS": best["lpips"],
        "lrDb": gh["lrConsistencyPsnrDb"], "shiftP95": gh["lineShiftP95Px"],
        "passed": gh["passed"], "musiqEnh": musiq, "maniqaEnh": maniqa,
        "musiqLanc": lanc_musiq,
    }
    summary.append(row)
    print(json.dumps(row), flush=True)

(REPO / "scratchpad/exp_presharpen_result.json").write_text(json.dumps(summary, indent=2))
print("DONE")
