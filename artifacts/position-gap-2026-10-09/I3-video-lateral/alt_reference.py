"""Scene offsets under the alternative absolute reference found by the 5-parameter training fit
(camera globals + one common lateral offset of the 3 training frames).  Under that hypothesis the
camera is the 5-parameter one and scene dy values are absolute w.r.t. the drawn pose.
Writes alt-reference.json (numbers only)."""
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lo", HERE / "lateral_offset.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)
res = json.loads((HERE / "lateral-offset-results.json").read_text(encoding="utf-8"))
alt = res["trainingCommonOffsetIdentifiability"]
cam = np.array(alt["x"][:4])
b = alt["x"][4]
out = {"alternativeCamera": cam.tolist(), "trainingCommonDyMeters": b, "costAlt": alt["cost"],
       "costFrozen": alt["frozenCameraCost"], "scenes": {}}
for row in lo.ROWS:
    if row["group"] == "train":
        D, _ = lo.scene_distances(row, cam, 1.25, lo.DY_GRID)
        g = lo.grid_fit(D, lo.DY_GRID)
        x, d, _ = lo.continuous(row, cam, 1.25, ["dy"], [[g]], lo.BOUNDS)
        out["scenes"][row["id"]] = {"fixedYawDy": float(x[0]), "medianPx": float(np.median(d)),
                                    "note": "in-sample; the common training offset itself is b"}
        continue
    D, _ = lo.scene_distances(row, cam, 1.25, lo.DY_GRID)
    base = D[np.argmin(np.abs(lo.DY_GRID))]
    g = lo.grid_fit(D, lo.DY_GRID)
    x, d, _ = lo.continuous(row, cam, 1.25, ["dy"], [[g]], lo.BOUNDS)
    out["scenes"][row["id"]] = {"fixedYawDy": float(x[0]), "baselineMedianPx": float(np.median(base)),
                                "medianPx": float(np.median(d))}
    print(row["id"], round(float(x[0]), 2), round(float(np.median(base)), 1), round(float(np.median(d)), 1))
held = [out["scenes"][r["id"]]["baselineMedianPx"] for r in lo.ROWS if r["group"] == "heldout"]
out["heldoutMedianOfSceneMediansPxAlt"] = float(np.median(held))
out["heldoutMeanOfSceneMediansPxAlt"] = float(np.mean(held))
(HERE / "alt-reference.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print("held-out median of medians (alt camera, no per-scene shift)", round(float(np.median(held)), 2))
