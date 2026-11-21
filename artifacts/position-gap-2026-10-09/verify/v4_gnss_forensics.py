"""Verifier V4: independent check of R3's decision-critical forensic claim
'no raw GNSS observables exist in the 2020 .xrk/.rrk/.gpk/.drk/.bak files'.

Read-only.  Paths come from env FUJI_WET_XRK / FUJI_DRY_XRK (siblings by extension); the output contains file
names and numbers only.  Checks (own code, not R3's):
  1. .rrk / .gpk byte tiling: does header + fixed-size records cover the WHOLE file (no room for other data)?
  2. Every file: checksum-valid UBX frames (0xB5 0x62 class id len payload ckA ckB), counted per class/id.
     RXM class (0x02: RAWX 0x15, SFRBX 0x13, RAW 0x10, SFRB 0x11) would be raw observables.
  3. XRK '<hGPS' messages: payload length histogram, and NAV-SOL decode (week, gpsFix, diffSoln flag, pAcc, numSV).
  4. .drk: printable strings containing 'GPS' or 'RAW' (channel names).
"""
from __future__ import annotations

import json
import os
import re
import struct
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NAVSOL = struct.Struct("<IiHBBiiiIiiiIHBBI")  # 52 bytes, UBX-NAV-SOL payload


def ubx_scan(b: bytes) -> dict:
    found = Counter()
    for m in re.finditer(rb"\xb5\x62", b):
        p = m.start()
        if p + 6 > len(b):
            continue
        cls, mid, ln = b[p + 2], b[p + 3], struct.unpack_from("<H", b, p + 4)[0]
        end = p + 6 + ln
        if end + 2 > len(b) or ln > 4096:
            continue
        ca = cb = 0
        for x in b[p + 2:end]:
            ca = (ca + x) & 0xFF
            cb = (cb + ca) & 0xFF
        if ca == b[end] and cb == b[end + 1]:
            found[f"0x{cls:02x}-0x{mid:02x}"] += 1
    return dict(found)


def tiling(b: bytes, tag: bytes, size: int) -> dict:
    pos = [m.start() for m in re.finditer(re.escape(tag), b)]
    # walk from the first record with a fixed stride
    if not pos:
        return {"records": 0}
    # choose the start (one of the first tag hits) that maximises tag hits on the fixed stride
    best = None
    for first in pos[:3]:
        n = (len(b) - first) // size
        ok = sum(1 for k in range(n) if b[first + k * size:first + k * size + 4] == tag)
        if best is None or ok > best[2]:
            best = (first, n, ok)
    first, n, ok = best
    return {"headerBytes": first, "recordSize": size, "strideRecords": n, "strideRecordsWithTag": ok,
            "trailingBytes": len(b) - first - n * size, "tagOccurrences": len(pos)}


def xrk_gps(b: bytes) -> dict:
    lens = Counter()
    rows = []
    for m in re.finditer(rb"<hGPS\x00", b):
        p = m.start()
        ln = struct.unpack_from("<I", b, p + 6)[0]
        lens[ln] += 1
        pay = b[p + 12:p + 12 + ln]
        if ln % 56 == 0:
            for k in range(0, ln, 56):
                v = NAVSOL.unpack_from(pay, k + 4)
                rows.append(v)
    if not rows:
        # fallback: maybe header length is different; report histogram only
        return {"payloadLengthHistogram": dict(lens), "decoded": 0}
    a = np.array(rows, dtype=float)
    week, fix, flags, pacc, nsv, pdop = a[:, 2], a[:, 3], a[:, 4].astype(int), a[:, 8] / 100, a[:, 15], a[:, 13] / 100
    return {"payloadLengthHistogram": {int(k): v for k, v in lens.items()}, "decoded": len(rows),
            "weeks": sorted(set(int(w) for w in week)), "gpsFixValues": dict(Counter(int(x) for x in fix)),
            "diffSolnFractionAll": float(np.mean((flags & 0x02) > 0)),
            "diffSolnFractionAfterFirstUncorrectedRun": float(np.mean((flags[np.argmax((flags & 0x02) > 0):] & 0x02) > 0)),
            "firstDiffSolnEpochIndex": int(np.argmax((flags & 0x02) > 0)),
            "lastNonDiffSolnEpochSecondsFromStart": float((a[np.nonzero((flags & 0x02) == 0)[0][-1], 0] - a[0, 0]) / 1000) if np.any((flags & 0x02) == 0) else None,
            "sessionSeconds": float((a[-1, 0] - a[0, 0]) / 1000),
            "pAccMedianM": float(np.median(pacc)), "pAccP95M": float(np.percentile(pacc, 95)),
            "numSVMedian": float(np.median(nsv)), "numSVRange": [int(nsv.min()), int(nsv.max())],
            "pDopMedian": float(np.median(pdop)),
            "iTOWStepMsHistogram": {int(k): int(v) for k, v in Counter(np.diff(a[:, 0]).astype(int)).most_common(4)}}


def main():
    out = {}
    for env in ("FUJI_WET_XRK", "FUJI_DRY_XRK"):
        xp = os.environ.get(env)
        if not xp:
            continue
        xp = Path(xp)
        res = {}
        for ext in (".xrk", ".rrk", ".gpk", ".drk", ".bak"):
            p = xp.with_suffix(ext)
            if not p.exists():
                res[ext] = None
                continue
            b = p.read_bytes()
            r = {"name": p.name, "size": len(b), "ubxFramesChecksumValid": ubx_scan(b)}
            if ext == ".rrk":
                r["tiling"] = tiling(b, b"NSOL", 64)
            if ext == ".gpk":
                r["tiling"] = tiling(b, b"PSOL", 144)
            if ext == ".xrk":
                r["gps"] = xrk_gps(b)
                r["tagTokens"] = dict(Counter(m.group(1).decode("latin1") for m in re.finditer(rb"<h([A-Za-z0-9+]{3,4})", b)).most_common())
            if ext == ".drk":
                strs = {s.decode("latin1") for s in re.findall(rb"[ -~]{4,}", b)}
                r["gpsOrRawStrings"] = sorted(s for s in strs if re.search(r"GPS|RAW|Raw|raw|RXM|Pseudo|Carrier|Doppler", s))[:60]
            res[ext] = r
        out[env] = res
    (HERE / "v4_gnss_forensics.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
