#!/usr/bin/env python3
"""Regression/integrity checks (read-only) for the 2026-10-02 improvement batch.

Writes integrity-checks.json next to this script. Reads repo files and the
baseline snapshot under artifacts/improvement-eval-2026-10-02/baseline.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "artifacts/improvement-eval-2026-10-02/baseline"
MANIFEST_NOW = ROOT / "artifacts/gps-accuracy-2026-09-22/verification-manifest.json"
MANIFEST_BASE = BASE / "verification-manifest.json"
CHANNELS = ["aps", "brake", "steer", "gear", "accx", "accy"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def deep_diff(a, b, path="", ignore=frozenset(), out=None):
    if out is None:
        out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in ignore:
                continue
            if k not in a:
                out.append(f"{path}.{k}: added")
            elif k not in b:
                out.append(f"{path}.{k}: removed")
            else:
                deep_diff(a[k], b[k], f"{path}.{k}", ignore, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: list length {len(a)} -> {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            deep_diff(x, y, f"{path}[{i}]", ignore, out)
    else:
        if type(a) is not type(b) or a != b:
            out.append(f"{path}: {a!r} -> {b!r}"[:300])
    return out


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


res: dict = {}

# (3) shipped registration offsets unchanged vs baseline
reg = {}
for race in ("fuji_aim_01", "fuji_aim_2020_07_30"):
    cur_p = ROOT / f"public/data/races/{race}/gps_registration.json"
    base_p = BASE / f"public/data/races/{race}/gps_registration.json"
    cur, base = load(cur_p), load(base_p)
    lap_off = []
    for lc, lb in zip(cur["laps"], base["laps"]):
        lap_off.append({
            "lap": lc["lap"],
            "offsetMeters_now": lc["offsetMeters"],
            "offsetMeters_base": lb["offsetMeters"],
            "equal": lc["offsetMeters"] == lb["offsetMeters"],
        })
    full = deep_diff(base, cur)
    rest = deep_diff(base, cur, ignore=frozenset({"dataSha256"}))
    reg[race] = {
        "byteIdentical": sha(cur_p) == sha(base_p),
        "sha_now": sha(cur_p),
        "sha_base": sha(base_p),
        "sessionOffsetMeters_now": cur.get("sessionOffsetMeters"),
        "sessionOffsetMeters_base": base.get("sessionOffsetMeters"),
        "sessionOffsetEqual": cur.get("sessionOffsetMeters") == base.get("sessionOffsetMeters"),
        "lapCount_now": len(cur["laps"]),
        "lapCount_base": len(base["laps"]),
        "laps": lap_off,
        "allLapOffsetsEqual": all(x["equal"] for x in lap_off) and len(cur["laps"]) == len(base["laps"]),
        "diffCount_all": len(full),
        "diff_all": full[:40],
        "diff_excluding_dataSha256": rest,
    }
res["3_registration_offsets"] = reg

# (4) 7/29 lap files byte-identical
man_base = load(MANIFEST_BASE)
man_now = load(MANIFEST_NOW)
r729 = {"files": []}
for entry in man_base["races"]["fuji_aim_01"]["inputs"]:
    p = ROOT / entry["source"]
    r729["files"].append({
        "source": entry["source"],
        "sha_now": sha(p),
        "sha_baseline_manifest": entry["sha256"],
        "match": sha(p) == entry["sha256"],
    })
for name in ("laps.json", "gps_registration.json"):
    p = ROOT / f"public/data/races/fuji_aim_01/{name}"
    b = BASE / f"public/data/races/fuji_aim_01/{name}"
    r729["files"].append({"source": str(p.relative_to(ROOT)).replace("\\", "/"),
                          "sha_now": sha(p), "sha_baseline_snapshot": sha(b), "match": sha(p) == sha(b)})
r729["allMatch"] = all(f["match"] for f in r729["files"])
r729["git_diff_name_only_HEAD"] = git("diff", "--name-only", "HEAD", "--", "public/data/races/fuji_aim_01").split()
r729["git_status_porcelain"] = git("status", "--porcelain", "--untracked-files=all", "--",
                                   "public/data/races/fuji_aim_01").splitlines()
r729["manifest_fuji_aim_01_unchanged"] = man_base["races"]["fuji_aim_01"] == man_now["races"]["fuji_aim_01"]
res["4_fuji_aim_01_lap_files"] = r729

# 7/30 lap files: only the six logger channels changed (plus meta.channel_time_alignment)
r730 = {"laps": []}
prev_hashes = {e["source"]: e["sha256"] for e in man_base["races"]["fuji_aim_2020_07_30"]["inputs"]}
for i in range(1, 7):
    name = f"osaki_hmr_demio_101_lap_{i:03d}.json"
    cp = ROOT / f"public/data/races/fuji_aim_2020_07_30/{name}"
    bp = BASE / f"public/data/races/fuji_aim_2020_07_30/{name}"
    c, b = load(cp), load(bp)
    src = f"public/data/races/fuji_aim_2020_07_30/{name}"
    keys_changed = sorted(k for k in set(c) | set(b) if c.get(k) != b.get(k))
    meta_changed = sorted(k for k in set(c["meta"]) | set(b["meta"]) if c["meta"].get(k) != b["meta"].get(k))
    gps_keys = [k for k in ("t", "lat", "lng", "speed", "dist") if k in b]
    lens = {k: (len(b[k]), len(c[k])) for k in CHANNELS + gps_keys if k in b}
    r730["laps"].append({
        "file": name,
        "baseline_sha_matches_previous_manifest": sha(bp) == prev_hashes.get(src),
        "current_sha_matches_current_manifest": sha(cp) == {e["source"]: e["sha256"] for e in man_now["races"]["fuji_aim_2020_07_30"]["inputs"]}.get(src),
        "top_level_keys_changed": keys_changed,
        "meta_keys_changed": meta_changed,
        "gps_arrays_identical": all(c[k] == b[k] for k in gps_keys),
        "array_lengths_base_now": lens,
        "channel_time_alignment": c["meta"].get("channel_time_alignment"),
    })
r730["onlyChannelsAndAlignmentMetaChanged"] = all(
    set(l["top_level_keys_changed"]) <= set(CHANNELS) | {"meta"}
    and set(l["meta_keys_changed"]) <= {"channel_time_alignment"}
    and l["gps_arrays_identical"]
    for l in r730["laps"])
lb, lc = load(BASE / "public/data/races/fuji_aim_2020_07_30/laps.json"), load(ROOT / "public/data/races/fuji_aim_2020_07_30/laps.json")
r730["laps_json_diff"] = deep_diff(lb, lc)[:60]
res["fuji_aim_2020_07_30_lap_files"] = r730

(HERE / "integrity-checks.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

# console summary
for race, r in reg.items():
    print(f"[3] {race}: byteIdentical={r['byteIdentical']} sessionOffsetEqual={r['sessionOffsetEqual']} "
          f"allLapOffsetsEqual={r['allLapOffsetsEqual']} diffs(all)={r['diffCount_all']} "
          f"diffs(excl dataSha256)={len(r['diff_excluding_dataSha256'])}")
    print("    session now:", r["sessionOffsetMeters_now"], " base:", r["sessionOffsetMeters_base"])
print(f"[4] fuji_aim_01 allMatch={r729['allMatch']} gitDiff={r729['git_diff_name_only_HEAD']} "
      f"status={r729['git_status_porcelain']} manifestEntryUnchanged={r729['manifest_fuji_aim_01_unchanged']}")
for f in r729["files"]:
    print("    ", f["source"], f["match"])
print(f"[7/30] onlyChannelsAndAlignmentMetaChanged={r730['onlyChannelsAndAlignmentMetaChanged']}")
for l in r730["laps"]:
    cta = l["channel_time_alignment"] or {}
    print("    ", l["file"], "prevManifestOK", l["baseline_sha_matches_previous_manifest"],
          "curManifestOK", l["current_sha_matches_current_manifest"],
          "keys", l["top_level_keys_changed"], "meta", l["meta_keys_changed"],
          "gpsSame", l["gps_arrays_identical"])
print("    laps.json diff:", r730["laps_json_diff"][:10])
