#!/usr/bin/env python3
"""Evidence pack for the track-limit GPS registration (Fuji, 2020-07-29/30).

Reproduces the figures and tables cited by
docs/policy-gps-cg-alignment-2026-09-20.md from the distributed lap JSON, the
traced CG road and the licensed VIRTUAL SHIZUOKA orthophoto. Read-only for all
inputs. Needs numpy, scipy, matplotlib, pillow:

    pipeline/.venv-sr/Scripts/python.exe scripts/quality/analyze-fuji-gps-registration.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image, ImageDraw

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import register_gps_to_track as reg  # noqa: E402

OUT = ROOT / os.environ.get("GPS_ANALYSIS_OUT", "artifacts/fuji-gps-registration-2026-09-20")
TRACK_DIR = ROOT / "public" / "data" / "tracks" / "fuji"
RACES = {"fuji_aim_01": ("wet", "2020-07-29 (wet)", "tab:blue"),
         "fuji_aim_2020_07_30": ("dry", "2020-07-30 (dry)", "tab:red")}
Image.MAX_IMAGE_PIXELS = None


def load_session(race, track, limits):
    race_dir = ROOT / "public" / "data" / "races" / race
    index = json.loads((race_dir / "laps.json").read_text(encoding="utf-8"))
    fit = json.loads((race_dir / "gps_registration.json").read_text(encoding="utf-8"))
    laps = []
    for record, entry in zip(index["selected"], fit["laps"]):
        data = json.loads((race_dir / record["data_file"]).read_text(encoding="utf-8"))
        t = np.asarray(data["t"], float)
        xy = reg.to_local(data["lat"], data["lng"], track["origin"])
        idx, q = limits.lateral(xy)
        laps.append(dict(lap=record["lap"], t=t, xy=xy, station=limits.dist[idx], q=q,
                         wheels=reg.wheel_points(xy, t, antenna=(fit["method"].get("antennaOffsetMeters", {}).get("right", 0), fit["method"].get("antennaOffsetMeters", {}).get("forward", 0)), geometry=fit["method"].get("vehicleGeometry")), keep=reg.racing_mask(limits, xy, t),
                         offset=np.array(entry["offsetMeters"])))
    return laps, fit


def cost(d, lap, limits, curb_use, mask=None):
    keep = lap["keep"] if mask is None else lap["keep"] & mask
    m = limits.margins(lap["wheels"][keep] + np.asarray(d), curb_use)
    return float(reg.huber(np.maximum(0.0, -m)).sum())


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    track = json.loads((TRACK_DIR / "track.json").read_text(encoding="utf-8"))
    limits = reg.load_limits(TRACK_DIR)
    total = float(track["totalLength"])
    sessions = {race: load_session(race, track, limits) for race in RACES}
    summary = {"limits": {"source": limits.source, "sha256": limits.source_sha256}, "sessions": {}}

    # 1. Lateral position of every lap against the traced road (raw GPS).
    fig, axes = plt.subplots(5, 1, figsize=(22, 22))
    for k, ax in enumerate(axes):
        a, b = k * total / 5, (k + 1) * total / 5
        m = (limits.dist >= a) & (limits.dist < b)
        ax.fill_between(limits.dist[m], limits.left[m], limits.right[m], color="0.85")
        ax.fill_between(limits.dist[m], limits.left[m] - limits.curb_left[m], limits.left[m], color="orange", alpha=.5)
        ax.fill_between(limits.dist[m], limits.right[m], limits.right[m] + limits.curb_right[m], color="orange", alpha=.5)
        for race, (laps, _) in sessions.items():
            for lap in laps:
                mm = (lap["station"] >= a) & (lap["station"] < b)
                ax.plot(lap["station"][mm], lap["q"][mm], ".", ms=2.5, color=RACES[race][2], alpha=.7)
        ax.set_xlim(a, b); ax.set_ylim(16, -16); ax.grid(alpha=.3)
        ax.set_ylabel("lateral [m]  (+ = driver's right)")
    axes[0].set_title("RAW GPS centre vs traced road (grey) and kerbs (orange). blue = 2020-07-29 wet, red = 2020-07-30 dry")
    axes[-1].set_xlabel("distance along the lap [m]")
    fig.tight_layout(); fig.savefig(OUT / "lateral-all-laps-raw.png", dpi=70); plt.close(fig)

    # 2. Cost landscape per lap and 3. split-half cross-validation.
    grid = np.arange(-4, 4.001, 0.25)
    fig, axes = plt.subplots(2, 5, figsize=(26, 11))
    ax_iter = iter(axes.ravel())
    for race, (laps, fit) in sessions.items():
        curb_use = fit["method"]["curbUse"]
        rows = []
        for lap in laps:
            surface = np.array([[cost((dx, dz), lap, limits, curb_use) for dz in grid] for dx in grid])
            ax = next(ax_iter)
            ax.imshow(np.log10(surface.T + 0.1), origin="upper", cmap="viridis",
                      extent=[grid[0] - .125, grid[-1] + .125, grid[-1] + .125, grid[0] - .125])
            ax.contour(grid, grid, surface.T, levels=[1, 5, 20], colors="w", linewidths=.7)
            ax.plot(*lap["offset"], "r*", ms=14); ax.plot(0, 0, "w+", ms=12)
            ax.set_title(f'{RACES[race][1]} lap {lap["lap"]}  fit=({lap["offset"][0]:+.2f},{lap["offset"][1]:+.2f}) m')
            ax.set_xlabel("dx east [m]"); ax.set_ylabel("dz south [m]")
            first = lap["station"] < total / 2
            cv = {}
            for name, train, test in (("fitFirstHalf_testSecond", first, ~first), ("fitSecondHalf_testFirst", ~first, first)):
                # Leak-free: the training half is fitted exactly like a session fit
                # (coarse grid + polish, with the tool's tiny pull toward zero as the
                # tie-break inside the zero-cost region) and never starts from the
                # full-lap optimum, which has already seen the held-out half.
                fitted, _ = reg.solve(lambda d, m=train: cost(d, lap, limits, curb_use, m) + reg.SESSION_EPS * float(np.dot(d, d)))
                cv[name] = {"offset": np.round(fitted, 2).tolist(),
                            "heldOutCostRaw": round(cost((0, 0), lap, limits, curb_use, test), 2),
                            "heldOutCostFitted": round(cost(fitted, lap, limits, curb_use, test), 2)}
            rows.append({"lap": lap["lap"], "offsetMeters": lap["offset"].tolist(),
                         "costRaw": round(cost((0, 0), lap, limits, curb_use), 2),
                         "costRegistered": round(cost(lap["offset"], lap, limits, curb_use), 2),
                         "crossValidation": cv})
        summary["sessions"][race] = {"surface": fit["method"]["surface"], "sessionOffsetMeters": fit["sessionOffsetMeters"], "laps": rows}
    fig.tight_layout(); fig.savefig(OUT / "cost-maps.png", dpi=55); plt.close(fig)

    # 4. Independent check: the pit entry road was excluded from every fit.
    meta = json.loads((TRACK_DIR / "satellite_shizuoka_meta.json").read_text(encoding="utf-8"))
    ortho = Image.open(TRACK_DIR / meta["imageFile"])
    bbox, origin = meta["bbox"], track["origin"]
    north, south = (math.asinh(math.tan(math.radians(bbox[k]))) for k in ("maxLat", "minLat"))

    def pixel(x, z):
        lat = origin["lat"] - z / reg.METERS_PER_DEGREE
        lng = origin["lng"] + x / (reg.METERS_PER_DEGREE * math.cos(math.radians(origin["lat"])))
        return ((lng - bbox["minLng"]) / (bbox["maxLng"] - bbox["minLng"]) * ortho.width,
                (north - math.asinh(math.tan(math.radians(lat)))) / (north - south) * ortho.height)

    for race, lap_no in (("fuji_aim_2020_07_30", 6), ("fuji_aim_01", 4)):
        lap = next(l for l in sessions[race][0] if l["lap"] == lap_no)
        n = len(lap["xy"])
        assert not lap["keep"][n - 110:].any(), "pit entry must be outside the fitted samples"
        for name, sl in (("entry", slice(n - 110, n - 60)), ("lane", slice(n - 45, n))):
            cx, cz = lap["xy"][sl].mean(axis=0)
            (px0, py0), (px1, py1) = pixel(cx - 45, cz - 45), pixel(cx + 45, cz + 45)
            scale = 4
            crop = ortho.crop((int(px0), int(py0), int(px1), int(py1)))
            crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
            draw = ImageDraw.Draw(crop)
            for pts, colour in ((lap["xy"][n - 160:], (255, 40, 40)), (lap["xy"][n - 160:] + lap["offset"], (40, 255, 255))):
                line = [((pixel(x, z)[0] - int(px0)) * scale, (pixel(x, z)[1] - int(py0)) * scale) for x, z in pts]
                draw.line(line, fill=colour, width=3)
            crop.convert("RGB").save(OUT / f"pit-{race}-L{lap_no}-{name}.jpg", quality=86)
    (OUT / "imagery-source.json").write_text(json.dumps({
        "pitOverlays": {"derivedFrom": meta["imageFile"], "source": meta["source"], "sourceUrl": meta["sourceUrl"],
                        "license": meta["license"], "processing": "crop, 4x Lanczos resize, vector overlay (red = raw GPS, cyan = registered)"},
        "videoCheck": {"source": "user-supplied onboard SCHD0613.MOV", "permission": "local reference only; no redistribution",
                       "processing": "decoded frames, crop/resize and measurement grid only; no AI enhancement"},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ratios = [1 - v["heldOutCostFitted"] / v["heldOutCostRaw"] for s in summary["sessions"].values()
              for row in s["laps"] for v in row["crossValidation"].values() if v["heldOutCostRaw"] > 0]
    summary["crossValidationSummary"] = {
        "pairs": len(ratios), "allImproved": bool(all(r > 0 for r in ratios)),
        "minImprovement": round(min(ratios), 3), "medianImprovement": round(float(np.median(ratios)), 3),
        "pairsAbove90pct": int(sum(r >= 0.9 for r in ratios)),
        "definition": "held-out half-lap cost reduction; training half fitted from the coarse grid, never from the full-lap optimum",
    }
    (OUT / "analysis-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("cross-validation:", summary["crossValidationSummary"])
    for race, s in summary["sessions"].items():
        print(race, "session", s["sessionOffsetMeters"])
        for row in s["laps"]:
            print("  lap", row["lap"], row["offsetMeters"], "cost", row["costRaw"], "->", row["costRegistered"],
                  "| held-out", [(v["heldOutCostRaw"], v["heldOutCostFitted"]) for v in row["crossValidation"].values()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
