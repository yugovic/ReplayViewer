"""C2 data-level checks of the shipped kerb_contacts.json / apex_kpi.json against the
UI rules: no side field, bins = f(peak), +-0.7 m width present, delta = value - session
median, apexLapTime = apexT + delta_lap, IMU time base shift = lap channel shift."""
import json, os, re
import numpy as np
ROOT = r"D:\00_Dev\ReplayViewer"
HERE = os.path.dirname(os.path.abspath(__file__))
SIDE = re.compile(r"side|left|right|wheel|lr\b|_l\b|_r\b", re.I)
def bin_of(p):
    if p is None or p < 1.0: return None
    return "low" if p < 1.5 else ("mid" if p < 3.0 else "high")
out = {}
for race in ["fuji_aim_01", "fuji_aim_2020_07_30"]:
    d = os.path.join(ROOT, "public", "data", "races", race)
    kc = json.load(open(os.path.join(d, "kerb_contacts.json"), encoding="utf-8"))
    ak = json.load(open(os.path.join(d, "apex_kpi.json"), encoding="utf-8"))
    laps = {}
    for rec in json.load(open(os.path.join(d, "laps.json"), encoding="utf-8"))["laps"]:
        if rec.get("data_file"):
            m = json.load(open(os.path.join(d, rec["data_file"]), encoding="utf-8"))["meta"]
            laps[str(m["lap"])] = m
    keys = set()
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k); walk(v, path + "." + k)
        elif isinstance(o, list):
            for v in o: walk(v, path)
    walk(kc); kc_keys = sorted(keys); keys.clear(); walk(ak); ak_keys = sorted(keys)
    ev = [(lap, e) for lap, es in kc.get("events", {}).items() for e in es]
    bin_mismatch = [(lap, e.get("peak"), e.get("bin")) for lap, e in ev if "bin" in e and e["bin"] != bin_of(e.get("peak"))]
    peaks = np.array([e["peak"] for _, e in ev])
    below = int((peaks < 1.0).sum())
    lab = [(lap, l) for lap, ls in kc.get("laps", {}).items() for l in ls]
    cls = {}
    for _, l in lab: cls[l["class"]] = cls.get(l["class"], 0) + 1
    strong_mid = sum(1 for _, l in lab if l["class"] == "strong" and bin_of(l["peakRollRms"]) == "mid")
    shift_kc = (kc.get("timeBase") or {}).get("shiftSecondsByLap") or {}
    shift_cmp = {lap: (shift_kc.get(lap), (laps[lap].get("channel_time_alignment") or {}).get("shiftSeconds")) for lap in laps}
    # apex_kpi checks
    rows = [(lap, r) for lap, rs in ak["laps"].items() for r in rs]
    lt_err = [abs(r["apexLapTime"] - (r["apexT"] + laps[lap]["first_sample_after_lap_start_seconds"])) for lap, r in rows if r.get("apexLapTime") is not None]
    by_kerb = {}
    for lap, r in rows: by_kerb.setdefault(r["kerb"], []).append((lap, r))
    d_err = []
    for kerb, rs in by_kerb.items():
        for key in ["gBlockFront", "apexStation", "onBlockLengthM"]:
            vals = [r[key] for _, r in rs if r.get(key) is not None]
            if not vals: continue
            med = float(np.median(vals))
            for _, r in rs:
                dv = (r.get("deltaVsSessionMedian") or {}).get(key)
                if r.get(key) is not None and dv is not None:
                    d_err.append(abs(dv - (r[key] - med)))
    names = {c["kerb"]: c["name"] for c in ak.get("corners", [])}
    side_names = {k: v for k, v in names.items() if re.search("右|左", v)}
    out[race] = {
        "kerbContacts_keys": kc_keys,
        "kerbContacts_sideLikeKeys": [k for k in kc_keys if SIDE.search(k)],
        "apexKpi_sideLikeKeys": [k for k in ak_keys if SIDE.search(k)],
        "events": len(ev), "eventsKerbAttributed": sum(1 for _, e in ev if e.get("kerb")),
        "eventsPeakBelow1": below, "fileBinVsPeakMismatch": bin_mismatch[:5],
        "eventBinCounts": {b: int(sum(1 for _, e in ev if bin_of(e["peak"]) == b)) for b in ["low", "mid", "high"]},
        "passLabels": len(lab), "passClassCounts": cls, "strongClassShownAsMidBin": strong_mid,
        "timeBaseShift_kerbContacts_vs_lapMeta": shift_cmp,
        "apexKpi_absoluteUncertaintyMeters": ak.get("absoluteUncertaintyMeters"),
        "apexKpi_absoluteUncertaintyReason": ak.get("absoluteUncertaintyReason"),
        "apexKpi_rows": len(rows), "apexLapTimeMinusTrueMaxS": round(max(lt_err), 6) if lt_err else None,
        "deltaVsSessionMedian_recomputeMaxAbsErr": round(max(d_err), 6) if d_err else None,
        "lapsPerKerbMin": min(len(v) for v in by_kerb.values()), "lapsPerKerbMax": max(len(v) for v in by_kerb.values()),
        "cornerNamesContainingLeftRight": side_names,
        "gBlockFrontAbsRange": [round(min(r["gBlockFront"] for _, r in rows if r.get("gBlockFront") is not None), 3),
                                round(max(r["gBlockFront"] for _, r in rows if r.get("gBlockFront") is not None), 3)],
    }
json.dump(out, open(os.path.join(HERE, "c2_data_checks.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
