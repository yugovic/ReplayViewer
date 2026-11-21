"""Verifier V3: kerb-contact label consistency recomputed independently.

1. From the COMMITTED public apex_kpi.json (shipped registration): g = min(gBlockFront, gBlockRear) per labelled pass,
   contradiction = strong & g > +0.15  or  silent & g < -0.55 (definition in apex_kpi.json 'labelCheck'),
   AUC = P(g_strong < g_silent) with ties counted 1/2.  -> must give I1/I2's 4 / 0.874 for 7/30.
2. From I2's labelGTable (per-label g for every variant): recompute count + AUC with this file's own code and
   compare to the numbers I2 reports in kerbLabels.
3. Max |I2 shipped g - committed g| (does I2's re-implementation of the shipped KPI match the committed one?).
4. Sign check for I3's claim that L2 k21/k23 'strong-but-off' contradictions mean 'car further RIGHT':
   reads the kerb side from runtime-geometry zones.
Repo root: env REPLAYVIEWER_ROOT, else three levels above this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("REPLAYVIEWER_ROOT", HERE.parents[2]))
I2 = ROOT / "artifacts" / "position-gap-2026-10-09" / "I2-fusion-trial" / "out"


def auc(S, N):
    if not S or not N:
        return None
    s = np.array(S)[:, None]
    n = np.array(N)[None, :]
    return float(np.mean((s < n) + 0.5 * (s == n)))


def metrics(rows):
    S = [r["g"] for r in rows if r["cls"] == "strong"]
    N = [r["g"] for r in rows if r["cls"] == "silent"]
    contra = [(r["lap"], r["kerb"], r["cls"], r["g"]) for r in rows
              if (r["cls"] == "strong" and r["g"] > 0.15) or (r["cls"] == "silent" and r["g"] < -0.55)]
    return {"n": len(S) + len(N), "strong": len(S), "silent": len(N), "contradictions": len(contra),
            "list": contra, "auc": None if auc(S, N) is None else round(auc(S, N), 4)}


def main():
    out = {}
    zones = {z["id"]: z for z in json.loads((ROOT / "artifacts" / "video-cg-alignment-2026-10-03" / "runtime-geometry.json").read_text("utf-8"))["zones"]}
    for race in ("fuji_aim_2020_07_30", "fuji_aim_01"):
        apex = json.loads((ROOT / "public" / "data" / "races" / race / "apex_kpi.json").read_text("utf-8"))
        rows = []
        for lapk, corners in apex["laps"].items():
            for c in corners:
                cls = (c.get("contact") or {}).get("class")
                if cls not in ("strong", "silent"):
                    continue
                if c.get("gBlockFront") is None or c.get("gBlockRear") is None:
                    continue
                rows.append({"lap": int(lapk), "kerb": c["kerb"], "cls": cls, "g": min(c["gBlockFront"], c["gBlockRear"])})
        committed = metrics(rows)
        ev = json.loads((I2 / f"eval_{race}.json").read_text("utf-8"))
        table = ev["labelGTable"]
        variants = [k for k in table[0] if k not in ("lap", "kerb", "cls", "anomaly")]
        recomputed = {}
        for v in variants:
            rr = [{"lap": r["lap"], "kerb": r["kerb"], "cls": r["cls"], "g": r[v]} for r in table
                  if r["cls"] in ("strong", "silent") and r[v] is not None]
            m = metrics(rr)
            rep = ev["kerbLabels"].get(v, {}).get("allLabels", {})
            recomputed[v] = {"mine": {k: m[k] for k in ("n", "contradictions", "auc")},
                             "i2Reported": {"n": rep.get("n"), "contradictions": rep.get("contradictionCount"), "auc": rep.get("auc")}}
        cm = {(r["lap"], r["kerb"]): r["g"] for r in rows}
        diffs = [abs(r["shipped"] - cm[(r["lap"], r["kerb"])]) for r in table if (r["lap"], r["kerb"]) in cm and r["shipped"] is not None]
        sign = []
        for c in committed["list"]:
            z = zones.get(c[1])
            side = z["side"] if z else None
            # strong-but-off on a RIGHT-side kerb -> car must move RIGHT to touch; on a LEFT kerb -> LEFT.
            # silent-but-deep on a RIGHT kerb -> car must move LEFT (away); on LEFT kerb -> RIGHT.
            if side is None:
                need = None
            elif c[2] == "strong":
                need = "right" if side == "right" else "left"
            else:
                need = "left" if side == "right" else "right"
            sign.append({"lap": c[0], "kerb": c[1], "cls": c[2], "g": c[3], "kerbSide": side, "carMustMove": need,
                         "zone": [z["from"], z["to"]] if z else None})
        out[race] = {"committedShipped": committed, "i2ShippedVsCommittedMaxAbsDiffM": max(diffs) if diffs else None,
                     "i2PairsMatched": len(diffs), "variants": recomputed, "contradictionSigns": sign}
    (HERE / "v3_kerb_labels.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", "utf-8")
    for race, o in out.items():
        print(race, "committed", {k: o["committedShipped"][k] for k in ("n", "strong", "silent", "contradictions", "auc")},
              "I2-vs-committed max|dg|", o["i2ShippedVsCommittedMaxAbsDiffM"], "n", o["i2PairsMatched"])
        for v, r in o["variants"].items():
            flag = "" if (r["mine"]["contradictions"] == r["i2Reported"]["contradictions"] and
                          (r["i2Reported"]["auc"] is None or abs(r["mine"]["auc"] - r["i2Reported"]["auc"]) < 0.0015)) else "  <-- MISMATCH"
            print(f"   {v:16s} mine {r['mine']}  I2 {r['i2Reported']}{flag}")
        for s in o["contradictionSigns"]:
            print("   contradiction", s)


if __name__ == "__main__":
    main()
