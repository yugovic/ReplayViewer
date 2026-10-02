"""Side-attribution confound test: wheel-speed ripple L/R ratio in cornering WITHOUT kerb vibration, by turn direction."""
import json
import numpy as np
from scipy.ndimage import uniform_filter1d
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import WORK  # noqa: E402
from vib import lap_data, SHIFT, SP, vib  # noqa: E402
from scipy.interpolate import CubicSpline  # noqa: E402
out = {}
for race, n in (('fuji_aim_2020_07_30', 6), ('fuji_aim_01', 4)):
    z = dict(np.load(f'{SP}/xrk_{race}.npz')); maps = json.loads(str(z['maps']))
    rows = {'left': [], 'right': [], 'straight': []}
    for k in range(1, n + 1):
        tc0 = maps[str(k)]['tc0']; d, t, raw, reg = lap_data(race, k)
        cx, cz = CubicSpline(t, raw[:, 0]), CubicSpline(t, raw[:, 1])
        V = vib(z, tc0, SHIFT[race]); tr, rr = V['RollRate']
        rip = {}
        for w in ('FL', 'FR', 'RL', 'RR'):
            tw = (z[f'ECU_WS_{w}_t'] - tc0) / 1000 + SHIFT[race]; v = z[f'ECU_WS_{w}']
            h = v - uniform_filter1d(v, 5); rip[w] = (tw, np.sqrt(np.maximum(uniform_filter1d(h * h, 8), 0)))
        for tc in np.arange(2, t[-1] - 2, 0.4):
            if np.interp(tc, tr, rr) > 0.8:      # skip any vibration (kerb or bump)
                continue
            vx, vz = cx(tc, 1), cz(tc, 1); ax, az = cx(tc, 2), cz(tc, 2)
            sp = np.hypot(vx, vz); lat = (vx * az - vz * ax) / max(sp, 1)   # sign: compare with known corners below
            L_ = np.interp(tc, *rip['FL']) + np.interp(tc, *rip['RL']); R_ = np.interp(tc, *rip['FR']) + np.interp(tc, *rip['RR'])
            key = 'straight' if abs(lat) < 2 else ('posLat' if lat > 0 else 'negLat')
            rows.setdefault(key, []).append(L_ / max(R_, 1e-6))
    res = {k: dict(n=len(v), medianLR=round(float(np.median(v)), 2), p90=round(float(np.percentile(v, 90)), 2), p10=round(float(np.percentile(v, 10)), 2),
                   fracAbove1p25=round(float(np.mean(np.array(v) > 1.25)), 2), fracBelow0p8=round(float(np.mean(np.array(v) < 0.8)), 2)) for k, v in rows.items() if v}
    out[race] = res
    print(race, json.dumps(res))
json.dump(out, open(WORK / 'side_baseline.json', 'w'), indent=1)
