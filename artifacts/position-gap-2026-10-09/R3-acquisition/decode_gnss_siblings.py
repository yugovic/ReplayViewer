"""R3 forensics (read-only): decode the .rrk (NSOL) and .gpk (PSOL) sibling files of an AiM
session and compare them with the GPS messages inside the .xrk.

Findings this script checks (see notes.md):
  * .rrk  = 'HEAD' file header + 'NSOL' records of 64 bytes = tag(4) + logger ms(4) + u-blox
            UBX-NAV-SOL payload (52 bytes) + 4 trailing bytes.
  * .xrk  '<hGPS' messages = logger ms(4) + the same NAV-SOL payload (libxrk docstring).
  * .gpk  = 'PROV' header + 'PSOL' records of 144 bytes (doubles) -- decoded heuristically.
No raw GNSS observables (pseudorange / carrier phase / Doppler per satellite) are present
if no record type other than NSOL / PSOL exists and NSOL is NAV-SOL (a navigation solution).

Env: FUJI_WET_XRK, FUJI_DRY_XRK. Output: JSON on stdout with file names only."""
import json
import os
import re
import struct
import sys
from pathlib import Path

import numpy as np

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)


def ecef2lla(x, y, z):
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - E2))
    for _ in range(8):
        n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - E2 * n / (n + h)))
    n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - n
    return np.degrees(lat), np.degrees(lon), h


NAVSOL = struct.Struct('<IiHBBiiiIiiiIHBBI')  # 52 bytes
NAVSOL_FIELDS = ['iTOW', 'fTOW', 'week', 'gpsFix', 'flags', 'ecefX', 'ecefY', 'ecefZ', 'pAcc',
                 'ecefVX', 'ecefVY', 'ecefVZ', 'sAcc', 'pDOP', 'res1', 'numSV', 'res2']


def decode_rrk(b):
    recs = []
    for m in re.finditer(rb'NSOL', b):
        p = m.start()
        if p + 64 > len(b):
            continue
        ts = struct.unpack_from('<I', b, p + 4)[0]
        if ts == 0x100 and b[p + 8:p + 16] == bytes(8):
            continue  # header/definition record
        vals = NAVSOL.unpack_from(b, p + 8)
        recs.append((ts,) + vals + (b[p + 60:p + 64].hex(),))
    return recs


def decode_xrk_gps(b):
    """'<hGPS' + 0x00 + len(4?) ... use libxrk layout: header '<hGPS\0' + u32 len + u8 ver + '>'
    then payload of len bytes then footer '<GPS\0' + crc(2) + '>'. Payload = 56-byte messages."""
    out = []
    for m in re.finditer(rb'<hGPS\x00', b):
        p = m.start()
        ln = struct.unpack_from('<I', b, p + 6)[0]
        payload = b[p + 12:p + 12 + ln]
        for k in range(0, len(payload) - 55, 56):
            ts = struct.unpack_from('<i', payload, k)[0]
            vals = NAVSOL.unpack_from(payload, k + 4)
            out.append((ts,) + vals)
    return out


def decode_gpk(b):
    recs = []
    for m in re.finditer(rb'PSOL', b):
        p = m.start()
        if p + 144 > len(b):
            continue
        ts = struct.unpack_from('<I', b, p + 4)[0]
        if ts == 0x100 and b[p + 8:p + 16] == bytes(8):
            continue
        week, itow, fix, flags = struct.unpack_from('<HIBB', b, p + 8)
        dbl = struct.unpack_from('<' + 'd' * 16, b, p + 16)
        recs.append(dict(ts=ts, week=week, iTOW=itow, fix=fix, flags=flags, d=dbl))
    return recs


def summarize(p):
    p = Path(p)
    res = {'session_file': p.name}
    xb = p.read_bytes()
    rb = p.with_suffix('.rrk').read_bytes()
    gb = p.with_suffix('.gpk').read_bytes()
    nsol = decode_rrk(rb)
    xgps = decode_xrk_gps(xb)
    res['rrk_header_hex'] = rb[:48].hex()
    res['gpk_header_hex'] = gb[:80].hex()
    res['n_rrk_navsol'] = len(nsol)
    res['n_xrk_gps_msgs'] = len(xgps)
    # compare rrk vs xrk (iTOW + ecef)
    xi = {r[1]: r for r in xgps}
    same = 0
    diff = 0
    for r in nsol:
        x = xi.get(r[1])
        if x is None:
            continue
        if x[1:13] == r[1:13]:
            same += 1
        else:
            diff += 1
    res['rrk_vs_xrk_navsol_identical_records'] = same
    res['rrk_vs_xrk_navsol_differing_records'] = diff
    a = np.array([r[1:18] for r in nsol], dtype=np.float64)
    d = dict(zip(NAVSOL_FIELDS, a.T))
    itow = d['iTOW']
    dt = np.diff(itow)
    res['navsol_rate_hz_median'] = float(1000 / np.median(dt))
    res['navsol_week'] = sorted(set(int(w) for w in d['week']))
    res['navsol_gpsFix_counts'] = {int(k): int(v) for k, v in zip(*np.unique(d['gpsFix'], return_counts=True))}
    fl = d['flags'].astype(int)
    res['navsol_flags_counts'] = {hex(int(k)): int(v) for k, v in zip(*np.unique(fl, return_counts=True))}
    res['navsol_diffSoln_bit_set_fraction'] = float(np.mean((fl & 0x02) > 0))
    mov = np.hypot(np.hypot(d['ecefVX'], d['ecefVY']), d['ecefVZ']) > 500  # > 5 m/s
    for k, scale in [('pAcc', 0.01), ('sAcc', 0.01), ('pDOP', 0.01), ('numSV', 1)]:
        v = d[k][mov] * scale
        res[f'navsol_{k}_moving'] = dict(min=float(v.min()), p05=float(np.percentile(v, 5)),
                                         median=float(np.median(v)), p95=float(np.percentile(v, 95)),
                                         max=float(v.max()))
    res['navsol_res1_values'] = sorted(set(int(v) for v in d['res1']))[:10]
    lat, lon, h = ecef2lla(d['ecefX'] / 100, d['ecefY'] / 100, d['ecefZ'] / 100)
    res['navsol_lat_lon_median'] = [round(float(np.median(lat)), 4), round(float(np.median(lon)), 4)]
    res['navsol_height_ellipsoid_median_m'] = float(np.median(h))
    res['navsol_ecef_quantum_cm'] = 1
    res['navsol_trailing4_examples'] = list({r[-1] for r in nsol[:50]})[:5]
    # --- gpk ---
    ps = decode_gpk(gb)
    res['n_gpk_psol'] = len(ps)
    pts = np.array([r['ts'] for r in ps])
    pit = np.array([r['iTOW'] for r in ps])
    res['gpk_unique_iTOW'] = int(len(set(pit.tolist())))
    res['gpk_iTOW_step_counts'] = {int(k): int(v) for k, v in zip(*np.unique(np.diff(pit), return_counts=True)) if v > 2}
    D = np.array([r['d'] for r in ps])
    res['gpk_double_columns_summary'] = [dict(col=i, min=float(D[:, i].min()), median=float(np.median(D[:, i])),
                                              max=float(D[:, i].max())) for i in range(D.shape[1])]
    # pairs sharing iTOW: compare first 3 doubles
    by = {}
    for r in ps:
        by.setdefault(r['iTOW'], []).append(r)
    pairs = [v for v in by.values() if len(v) == 2]
    if pairs:
        dd = np.array([np.array(v[1]['d'][:3]) - np.array(v[0]['d'][:3]) for v in pairs])
        res['gpk_pairs_same_iTOW'] = len(pairs)
        res['gpk_pair_diff_first3_abs_median'] = [float(x) for x in np.median(np.abs(dd), axis=0)]
        res['gpk_pair_first_fix_values'] = [sorted(set(v[0]['fix'] for v in pairs)), sorted(set(v[1]['fix'] for v in pairs))]
        res['gpk_pair_first_flags_values'] = [sorted(set(hex(v[0]['flags']) for v in pairs))[:6], sorted(set(hex(v[1]['flags']) for v in pairs))[:6]]
    # relationship of gpk doubles to NAV-SOL: regress gpk d0..d2 on ENU of NAV-SOL at same iTOW
    nav_by = {int(r[1]): r for r in nsol}
    rows = []
    for r in ps:
        n = nav_by.get(r['iTOW'])
        if n is None:
            continue
        rows.append(list(r['d'][:12]) + [n[6] / 100, n[7] / 100, n[8] / 100, n[9] / 100, n[13] / 100, r['ts'], n[0]])
    R = np.array(rows)
    res['gpk_rows_matched_to_navsol'] = int(len(R))
    if len(R):
        # pAcc compare
        res['gpk_col3_vs_navsol_pAcc_m_maxabs'] = float(np.max(np.abs(R[:, 3] - R[:, 15])))
        res['gpk_col7_vs_navsol_sAcc_maxabs'] = float(np.max(np.abs(R[:, 7] - R[:, 16])))
        res['gpk_ts_minus_navsol_ts_unique'] = sorted(set((R[:, 17] - R[:, 18]).astype(int).tolist()))[:10]
        # Are d0..d2 an affine function of ECEF? least squares
        X = np.c_[R[:, 12:15] - R[:, 12:15].mean(0), np.ones(len(R))]
        fits = []
        for c in range(3):
            coef, *_ = np.linalg.lstsq(X, R[:, c], rcond=None)
            resid = R[:, c] - X @ coef
            fits.append(dict(col=c, coef=[float(x) for x in coef], resid_rms=float(np.sqrt(np.mean(resid ** 2)))))
        res['gpk_first3_affine_fit_to_navsol_ecef'] = fits
        res['_examples_first5'] = [[round(float(x), 6) for x in row] for row in R[:5]]
    return res


def main():
    out = {}
    for key in ['FUJI_WET_XRK', 'FUJI_DRY_XRK']:
        p = os.environ.get(key)
        if p:
            out[key] = summarize(p)
    json.dump(out, sys.stdout, indent=1)


if __name__ == '__main__':
    main()
