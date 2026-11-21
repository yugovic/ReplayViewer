"""R3: per-lap receiver self-reported quality from the XRK NAV-SOL stream (read-only).

For every replay lap (public/data/races/<race>/osaki_hmr_demio_101_lap_NNN.json) find the
matching NAV-SOL epochs (lat/lng equality after ECEF->LLA) and report diffSoln fraction, pAcc,
numSV, pDOP. pAcc is the receiver's own accuracy ESTIMATE, not a measured error.
Also reports 7/30 L2 inside vs outside the 3250-3900 m window where a 1-2 m place-dependent
GPS error was found earlier (docs/fuji-apex-kerb-kpi-2026-10-02.md).

Env: FUJI_WET_XRK, FUJI_DRY_XRK. Repo root is derived from this file's location.
Output: navsol_lap_quality.json next to this script."""
import json
import os
import re
import struct
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NAVSOL = struct.Struct('<IiHBBiiiIiiiIHBBI')
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
    return np.degrees(lat), np.degrees(lon)


def navsol_from_xrk(path):
    b = Path(path).read_bytes()
    rows = []
    for m in re.finditer(rb'<hGPS\x00', b):
        p = m.start()
        ln = struct.unpack_from('<I', b, p + 6)[0]
        pl = b[p + 12:p + 12 + ln]
        for k in range(0, len(pl) - 55, 56):
            rows.append(NAVSOL.unpack_from(pl, k + 4))
    a = np.array(rows, dtype=np.float64)
    lat, lon = ecef2lla(a[:, 5] / 100, a[:, 6] / 100, a[:, 7] / 100)
    return dict(iTOW=a[:, 0], flags=a[:, 4].astype(int), pAcc=a[:, 8] / 100, sAcc=a[:, 12] / 100,
                pDOP=a[:, 13] / 100, numSV=a[:, 15], lat=lat, lon=lon)


def stats(v):
    return dict(median=round(float(np.median(v)), 3), p95=round(float(np.percentile(v, 95)), 3),
                max=round(float(np.max(v)), 3), min=round(float(np.min(v)), 3))


def main():
    out = {'note': 'pAcc/sAcc are receiver self-estimates (u-blox NAV-SOL), not measured errors.'}
    for race, key in [('fuji_aim_01', 'FUJI_WET_XRK'), ('fuji_aim_2020_07_30', 'FUJI_DRY_XRK')]:
        if not os.environ.get(key):
            continue
        ns = navsol_from_xrk(os.environ[key])
        rd = ROOT / 'public/data/races' / race
        laps = {}
        for f in sorted(rd.glob('osaki_hmr_demio_101_lap_*.json')):
            d = json.loads(f.read_text(encoding='utf-8'))
            n = len(d['lat'])
            la, lo = np.array(d['lat']), np.array(d['lng'])
            probe = np.arange(0, n, 25)
            best = (1e9, 0)
            for i0c in range(0, len(ns['lat']) - n):
                e = np.mean(np.hypot(ns['lat'][i0c + probe] - la[probe], ns['lon'][i0c + probe] - lo[probe]))
                if e < best[0]:
                    best = (e, i0c)
            i0 = best[1]
            seg = slice(i0, i0 + n)
            lat_err_m = float(np.max(np.abs(ns['lat'][seg] - np.array(d['lat'])))) * 111000
            r = dict(match_start_index=i0, n=n, max_latlon_mismatch_m=round(lat_err_m, 4),
                     diffSoln_fraction=round(float(np.mean((ns['flags'][seg] & 2) > 0)), 4),
                     pAcc_m=stats(ns['pAcc'][seg]), sAcc_mps=stats(ns['sAcc'][seg]),
                     numSV=stats(ns['numSV'][seg]), pDOP=stats(ns['pDOP'][seg]))
            if race == 'fuji_aim_2020_07_30' and f.name.endswith('_002.json'):
                dist = np.array(d['dist'], dtype=float)
                w = (dist >= 3250) & (dist <= 3900)
                pa = ns['pAcc'][seg]
                sv = ns['numSV'][seg]
                pd = ns['pDOP'][seg]
                r['window_3250_3900m'] = dict(n=int(w.sum()), pAcc_m=stats(pa[w]), numSV=stats(sv[w]), pDOP=stats(pd[w]))
                r['outside_window'] = dict(n=int((~w).sum()), pAcc_m=stats(pa[~w]), numSV=stats(sv[~w]), pDOP=stats(pd[~w]))
            laps[f.stem[-3:]] = r
        out[race] = laps
    (HERE / 'navsol_lap_quality.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
