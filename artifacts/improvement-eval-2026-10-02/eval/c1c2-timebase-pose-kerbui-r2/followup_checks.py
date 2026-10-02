"""Follow-up checks on harness_out.json: where the AFTER ghost-position error and the
real-pair model residual are non-zero, and the end-of-lap HUD delta vs official
lap-time difference (residual error of the GPS path-length distance basis, untouched by C1)."""
import json, os
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"D:\00_Dev\ReplayViewer"
h = json.load(open(os.path.join(HERE, "harness_out.json"), encoding="utf-8"))
out = {}
# 1) after ghost-position outliers (synthetic self pairs)
idx = []
for r in h["synthetic"]:
    pa = np.abs(np.asarray(r["ghostPosAfterM"]))
    bad = np.nonzero(pa > 0.05)[0]
    if bad.size:
        idx.append({"source": r["source"], "dm": r["deltaMain"], "dg": r["deltaGhost"], "badSampleIdx": bad.tolist()[:10], "maxM": round(float(pa.max()), 3)})
out["afterGhostPosOver5cm"] = {"pairsAffected": len(idx), "examples": idx[:6],
                               "allAtLapStart": all(max(e["badSampleIdx"]) <= 1 for e in idx)}
# 2) real-pair model residual location
laps = {}
for race in ["fuji_aim_01", "fuji_aim_2020_07_30"]:
    d = os.path.join(ROOT, "public", "data", "races", race)
    for rec in json.load(open(os.path.join(d, "laps.json"), encoding="utf-8"))["laps"]:
        if rec.get("data_file"):
            lap = json.load(open(os.path.join(d, rec["data_file"]), encoding="utf-8"))
            laps[f"{race}/L{lap['meta']['lap']}"] = (lap, rec)
res_rows = []
end_rows = []
for p in h["real"]:
    m, recm = laps[p["main"]]; g, recg = laps[p["ghost"]]
    dm = m["meta"]["first_sample_after_lap_start_seconds"]; dg = g["meta"]["first_sample_after_lap_start_seconds"]
    tg = np.asarray(g["t"]); distg = np.asarray(g["dist"])
    d = np.asarray(p["dist"]); b = np.asarray(p["before"]); a = np.asarray(p["after"])
    shift = p["s0Main"] - p["s0Ghost"]
    model = (dm - dg) - (np.interp(d + shift, distg, tg) - np.interp(d, distg, tg))
    resid = np.abs((a - b) - model)
    inside = d + shift >= distg[0]
    ok_end = d + shift <= distg[-1]
    res_rows.append({"pair": f"{p['main']} vs {p['ghost']}", "maxResidInsideMs": round(float(resid[inside & ok_end].max()) * 1000, 4),
                     "maxResidBeforeGhostFirstSampleMs": round(float(resid[~inside].max()) * 1000, 2) if (~inside).any() else None,
                     "samplesBeforeGhostFirstSample": int((~inside).sum())})
    # 3) end of lap: HUD delta at the main's last sample vs official lap-time difference
    official = recm["lap_time_seconds"] - recg["lap_time_seconds"]
    end_rows.append({"pair": f"{p['main']} vs {p['ghost']}", "officialDeltaS": round(official, 3),
                     "hudEndBeforeS": round(float(b[-1]), 3), "hudEndAfterS": round(float(a[-1]), 3),
                     "pathLenDiffM": round(recm["total_distance_m"] - recg["total_distance_m"], 2)})
out["realPairModelResidual"] = {"maxInsideMs": max(r["maxResidInsideMs"] for r in res_rows),
                                "rowsWithStartSamples": [r for r in res_rows if r["samplesBeforeGhostFirstSample"]][:6]}
eb = np.array([abs(r["hudEndBeforeS"] - r["officialDeltaS"]) for r in end_rows]) * 1000
ea = np.array([abs(r["hudEndAfterS"] - r["officialDeltaS"]) for r in end_rows]) * 1000
out["endOfLapHudDeltaVsOfficial"] = {
    "note": "truth at the main's last sample ~= official lap-time difference (both cars <0.1 s before the line on the straight); residual = path-length basis + last-sample gap",
    "absErrorMs_before": {"median": round(float(np.median(eb)), 1), "max": round(float(eb.max()), 1)},
    "absErrorMs_after": {"median": round(float(np.median(ea)), 1), "max": round(float(ea.max()), 1)},
    "worst5_after": sorted(end_rows, key=lambda r: -abs(r["hudEndAfterS"] - r["officialDeltaS"]))[:5],
}
json.dump(out, open(os.path.join(HERE, "followup_checks.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
