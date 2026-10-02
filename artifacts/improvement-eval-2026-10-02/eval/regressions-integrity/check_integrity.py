"""Regression/integrity checks (3), (4) and 7/30 lap-field integrity, measured against the
baseline snapshot in artifacts/improvement-eval-2026-10-02/baseline/.
Read-only on repo files; writes integrity-checks.json next to this script."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline"
RACES = ["fuji_aim_01", "fuji_aim_2020_07_30"]
CHANNELS = {"aps", "brake", "steer", "gear", "accx", "accy"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def git_head_bytes(rel: str) -> bytes | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


out: dict = {}

# (3) registration offsets: baseline vs current, both races
reg = {}
for race in RACES:
    b = load(BASE / f"public/data/races/{race}/gps_registration.json")
    c = load(ROOT / f"public/data/races/{race}/gps_registration.json")
    laps = []
    for lb, lc in zip(b["laps"], c["laps"]):
        laps.append({
            "lap": lc["lap"],
            "offsetBaseline": lb["offsetMeters"], "offsetNow": lc["offsetMeters"],
            "offsetEqual": lb["offsetMeters"] == lc["offsetMeters"],
            "jackknifeEqual": lb["jackknifeSigmaMeters"] == lc["jackknifeSigmaMeters"],
            "rawKpiEqual": lb["raw"] == lc["raw"], "registeredKpiEqual": lb["registered"] == lc["registered"],
            "dataShaChanged": lb["dataSha256"] != lc["dataSha256"],
        })
    # every top-level key except laps, and every lap key except dataSha256
    diff_keys = [k for k in set(b) | set(c) if k != "laps" and b.get(k) != c.get(k)]
    lap_diff = sorted({k for lb, lc in zip(b["laps"], c["laps"]) for k in set(lb) | set(lc)
                       if k != "dataSha256" and lb.get(k) != lc.get(k)})
    reg[race] = {
        "lapCountBaseline": len(b["laps"]), "lapCountNow": len(c["laps"]),
        "sessionOffsetBaseline": b["sessionOffsetMeters"], "sessionOffsetNow": c["sessionOffsetMeters"],
        "sessionOffsetEqual": b["sessionOffsetMeters"] == c["sessionOffsetMeters"],
        "allLapOffsetsEqual": all(x["offsetEqual"] for x in laps),
        "topLevelKeysDiffering": diff_keys, "lapKeysDifferingExceptDataSha": lap_diff,
        "fileByteIdenticalToBaseline": sha(BASE / f"public/data/races/{race}/gps_registration.json")
        == sha(ROOT / f"public/data/races/{race}/gps_registration.json"),
        "laps": laps,
    }
out["registration"] = reg

# (4) 7/29 (fuji_aim_01) lap files: baseline manifest sha, HEAD bytes, registration dataSha256
bm = load(BASE / "verification-manifest.json")
cm = load(ROOT / "artifacts/gps-accuracy-2026-09-22/verification-manifest.json")
wet = []
reg01 = load(ROOT / "public/data/races/fuji_aim_01/gps_registration.json")
reg_sha = {f"public/data/races/fuji_aim_01/{l['dataFile']}": l["dataSha256"] for l in reg01["laps"]}
for entry in bm["races"]["fuji_aim_01"]["inputs"]:
    p = ROOT / entry["source"]
    now = sha(p) if p.is_file() else None
    head = git_head_bytes(entry["source"])
    wet.append({
        "source": entry["source"], "shaNow": now, "shaBaselineManifest": entry["sha256"],
        "matchesBaselineManifest": now == entry["sha256"],
        "trackedInGit": head is not None,
        "byteIdenticalToHEAD": (head is not None and p.is_file() and head == p.read_bytes()),
        "matchesRegistrationDataSha": (reg_sha.get(entry["source"]) == now) if entry["source"] in reg_sha else None,
    })
wet_laps = sorted((ROOT / "public/data/races/fuji_aim_01").glob("*_lap_*.json"))
out["wet0729"] = {
    "manifestSectionUnchanged": bm["races"]["fuji_aim_01"] == cm["races"]["fuji_aim_01"],
    "lapFilesOnDisk": [p.name for p in wet_laps],
    "laps_json_identical_to_baseline": sha(BASE / "public/data/races/fuji_aim_01/laps.json")
    == sha(ROOT / "public/data/races/fuji_aim_01/laps.json"),
    "inputs": wet,
}

# Baseline snapshot fidelity: baseline 7/30 files vs baseline manifest and vs HEAD
fid = []
for entry in bm["races"]["fuji_aim_2020_07_30"]["inputs"]:
    bp = BASE / entry["source"]
    head = git_head_bytes(entry["source"])
    fid.append({"source": entry["source"], "baselineFileExists": bp.is_file(),
                "baselineMatchesBaselineManifest": bp.is_file() and sha(bp) == entry["sha256"],
                "baselineEqualsHEAD": bp.is_file() and head is not None and head == bp.read_bytes()})
out["baselineSnapshotFidelity0730"] = fid

# 7/30 lap files: only logger channels + meta.channel_time_alignment may differ
dry = []
for i in range(1, 7):
    name = f"osaki_hmr_demio_101_lap_{i:03d}.json"
    b = load(BASE / f"public/data/races/fuji_aim_2020_07_30/{name}")
    c = load(ROOT / f"public/data/races/fuji_aim_2020_07_30/{name}")
    changed_arrays = sorted(k for k in set(b) | set(c) if k != "meta" and b.get(k) != c.get(k))
    meta_changed = sorted(k for k in set(b["meta"]) | set(c["meta"]) if b["meta"].get(k) != c["meta"].get(k))
    lens = {k: len(c[k]) for k in c if k != "meta"}
    shift = c["meta"].get("channel_time_alignment", {}).get("shiftSeconds")
    dry.append({
        "file": name, "changedArrays": changed_arrays, "changedMetaKeys": meta_changed,
        "onlyAllowedChanged": set(changed_arrays) <= CHANNELS and set(meta_changed) <= {"channel_time_alignment"},
        "gpsArraysIdentical": all(b[k] == c[k] for k in ("t", "lat", "lng", "speed", "dist")),
        "arrayLengthsConsistent": len(set(lens.values())) == 1, "pointCount": c["meta"]["point_count"],
        "shiftSeconds": shift,
    })
out["dry0730LapFields"] = dry
lb = load(BASE / "public/data/races/fuji_aim_2020_07_30/laps.json")
lc = load(ROOT / "public/data/races/fuji_aim_2020_07_30/laps.json")


def walk_diff(a, b, path=""):
    if type(a) != type(b):
        return [path]
    if isinstance(a, dict):
        r = []
        for k in set(a) | set(b):
            r += walk_diff(a.get(k), b.get(k), f"{path}.{k}")
        return r
    if isinstance(a, list):
        if len(a) != len(b):
            return [path + "[len]"]
        r = []
        for i, (x, y) in enumerate(zip(a, b)):
            r += walk_diff(x, y, f"{path}[{i}]")
        return r
    return [] if a == b else [path]


out["dry0730LapsJsonDiffPaths"] = sorted(walk_diff(lb, lc))
out["summary"] = {
    "registrationOffsetsUnchangedBothRaces": all(r["allLapOffsetsEqual"] and r["sessionOffsetEqual"] for r in reg.values()),
    "wetRegistrationByteIdentical": reg["fuji_aim_01"]["fileByteIdenticalToBaseline"],
    "wetLapFilesMatchBaselineManifest": all(w["matchesBaselineManifest"] for w in wet),
    "wetLapFilesByteIdenticalToHEAD": all(w["byteIdenticalToHEAD"] for w in wet),
    "dry0730OnlyChannelFieldsChanged": all(d["onlyAllowedChanged"] for d in dry),
}
(HERE / "integrity-checks.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps(out["summary"], indent=1))
for race, r in reg.items():
    print(race, "session", r["sessionOffsetBaseline"], "->", r["sessionOffsetNow"], "topDiff", r["topLevelKeysDiffering"],
          "lapDiff", r["lapKeysDifferingExceptDataSha"], "byteIdentical", r["fileByteIdenticalToBaseline"])
    for l in r["laps"]:
        print("  L%d %s -> %s eq=%s jk=%s shaChanged=%s" % (l["lap"], l["offsetBaseline"], l["offsetNow"], l["offsetEqual"], l["jackknifeEqual"], l["dataShaChanged"]))
for w in wet:
    print(w["source"].split("/")[-1], w["matchesBaselineManifest"], w["trackedInGit"], w["byteIdenticalToHEAD"], w["matchesRegistrationDataSha"])
print("lapfiles on disk", out["wet0729"]["lapFilesOnDisk"])
for f in fid:
    print("fidelity", f)
for d in dry:
    print(d)
print("laps.json diff", out["dry0730LapsJsonDiffPaths"][:40], len(out["dry0730LapsJsonDiffPaths"]))
