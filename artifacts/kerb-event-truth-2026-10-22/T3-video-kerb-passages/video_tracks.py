"""T3 step 3: per pass and per image row, the road-side edge of the inside kerb (white strip / paint) and red-block
presence, from the raw RGB rows saved by extract.py (work folder). Camera-independent: pixels and frame PTS only.

Per frame and row: La = median luminance of low-saturation road pixels (asphalt reference).
  asphalt = |lum - La| < 28 and sat < 32
  marking = lum > La + 18 and sat < 52          (white strip, white blocks and the desaturated red blocks; not grass)
  red     = R - G > 10 and R - B > 4 and lum > La + 8 and sat < 90
For a LEFT kerb the row is scanned from u=760 leftwards (RIGHT kerbs: from u=520 rightwards); the edge is the first
column of a >= 3 px marking run that follows >= 6 asphalt pixels (<= 3 transitional pixels tolerated). Red presence = >= 4 red pixels within
[edge, edge + 1.4 m outward] (approximate px/m from the measured horizon row). The longest temporally continuous edge
track (|du| <= 60 px per frame, gaps <= 5 frames: the 7/29 wiper blade crosses a row for 2-4 frames) that contains red is kept. Frames with >= 30 glove-blue pixels within
120 px of the edge (driver's arm) are dropped, and isolated detections > 25 px from the median of the +-3 neighbouring
detections are dropped (wiper blade edge, spray, glare).
Output: $T3_WORK/tracks/<race>_L<lap>_<kerb>.json (per-frame numbers; kept outside the repository with the masks).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

ROWS = [240, 252, 264, 276, 286]
V_VP = 196.8
PXM_286 = 70.0


def road_mask_v2(race: str) -> np.ndarray:
    """Windshield road band: between the A-pillars and above the dashboard edge (both car-fixed, from the temporal
    statistics of 240 frames of the same video), rows 175..330. The centre is NOT excluded (low temporal variance there
    only means uniform asphalt). Hose (x 465-640, y < 225) and mirror (y < 160) excluded."""
    p = C.WORK / f'roadmask2_{race}.npy'
    if p.exists():
        return np.load(p)
    st = np.load(C.WORK / f'mask_stats_{race}.npz')
    sd, med = st['sd'], st['med'].mean(-1)            # rows 100..399
    old = sd > 14
    m = np.zeros((C.H_IMG, C.W_IMG), bool)
    for x in range(200, 1100):
        col = med[:, x]
        ybot = None
        for r in range(150, 240):
            if col[r] < 70 and col[r + 1] < 70 and col[r + 2] < 70:
                ybot = r + 100
                break
        if ybot is not None:
            m[175:ybot - 2, x] = True
    for y in range(175, 331):
        run = np.where(old[y - 100, 150:1150])[0] + 150
        if len(run) < 50:
            m[y] = False
            continue
        lo = run[(run > 180) & (run < 400)]
        hi = run[(run > 900) & (run < 1150)]
        left = int(lo.min()) if len(lo) else 230
        right = int(hi.max()) if len(hi) else 1070
        m[y, :max(left, 200)] = False
        m[y, min(right, 1090) + 1:] = False
    m[:225, 465:641] = False
    m[280:, 612:708] = False      # dash-top display unit
    m[:160] = False
    np.save(p, m)
    return m


def classify(rgb: np.ndarray, road_row: np.ndarray):
    f = rgb.astype(np.int16)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    cand = road_row[None, :] & (sat < 25)
    La = np.array([np.median(l[c]) if c.sum() > 50 else np.median(l[road_row]) for l, c in zip(lum, cand)])
    asphalt = (np.abs(lum - La[:, None]) < 28) & (sat < 32) & road_row[None, :]
    marking = (lum > La[:, None] + 18) & (sat < 52) & road_row[None, :]
    red = (R - G > 10) & (R - B > 4) & (lum > La[:, None] + 8) & (sat < 90) & road_row[None, :]
    blue = (B - R > 35) & (B - G > 15) & (B > 70)
    return asphalt, marking | red, red, blue, La


def first_edge(mark, asph, start, stop, step):
    n = mark.shape[0]
    idx = np.arange(start, stop, step)
    mm, aa = mark[:, idx], asph[:, idx]
    out = np.full(n, -1)
    for k in range(n):
        g = 0
        unk = 0
        row, ar = mm[k], aa[k]
        for j in range(len(idx) - 3):
            if row[j] and row[j + 1] and row[j + 2]:
                if g >= 6:
                    out[k] = idx[j]
                    break
                g = 0
                unk = 0
            elif ar[j]:
                g += 1
                unk = 0
            else:
                unk += 1          # up to 3 transitional pixels between asphalt and the marking are tolerated
                if unk > 3:
                    g = 0
    return out


def track(e, redp, n):
    tr, cur, last, gap = [], [], None, 0
    for k in range(n):
        if e[k] < 0:
            gap += 1
            if gap > 5 and cur:
                tr.append(cur); cur = []; last = None
            continue
        if last is not None and abs(e[k] - e[last]) > 60 * (k - last):
            tr.append(cur); cur = []
        cur.append(k); last = k; gap = 0
    if cur:
        tr.append(cur)
    best = None
    for t_ in tr:
        nr = int(redp[t_].sum())
        if nr >= 2 and (best is None or len(t_) + 3 * nr > best[0]):
            best = (len(t_) + 3 * nr, t_)
    keep = np.zeros(n, bool)
    if best:
        keep[best[1]] = True
    return keep


def tracks_for(path_rows: Path, side: str, road: np.ndarray):
    d = np.load(path_rows)
    rgb, rows, pts = d['rgb'], list(d['rows']), d['pts']
    n = len(pts)
    res = {}
    for y in ROWS:
        i = rows.index(y)
        asph, mark, red, blue, La = classify(rgb[:, i], road[y])
        if side == 'left':
            e = first_edge(mark, asph, 760, 205, -1)
        else:
            e = first_edge(mark, asph, 520, 1088, 1)
        wpx = int(1.4 * PXM_286 * (y - V_VP) / (286 - V_VP))
        nred = np.zeros(n, int)
        arm = np.zeros(n, bool)
        for k in range(n):
            if e[k] < 0:
                continue
            lo, hi = (max(e[k] - wpx, 0), e[k] + 3) if side == 'left' else (e[k] - 2, min(e[k] + wpx, 1280))
            nred[k] = int(red[k, lo:hi].sum())
            arm[k] = blue[k, max(e[k] - 120, 0):e[k] + 120].sum() >= 30
        e[arm] = -1
        # isolated outliers (wiper edge, spray, specular glare): > 25 px from the median of the +-3 neighbouring detections
        e2 = e.copy()
        for k in range(n):
            if e[k] < 0:
                continue
            nb = [e[j] for j in range(max(0, k - 3), min(n, k + 4)) if j != k and e[j] >= 0]
            if len(nb) >= 3 and abs(e[k] - np.median(nb)) > 25:
                e2[k] = -1
        e = e2
        redp = nred >= 4
        keep = track(e, redp, n)
        cols = np.where(road[y])[0]
        res[y] = dict(edge=[int(e[k]) if keep[k] and e[k] >= 0 else None for k in range(n)],
                      red=[bool(redp[k] and keep[k]) for k in range(n)],
                      nred=[int(nred[k]) if keep[k] else 0 for k in range(n)],
                      rawEdge=[int(x) for x in e], arm=[bool(a) for a in arm],
                      asphaltLum=[round(float(x), 1) for x in La],
                      boundaryOut=int(cols.min() if side == 'left' else cols.max()),
                      boundaryIn=int(cols.max() if side == 'left' else cols.min()))
    return dict(pts=[float(p) for p in pts], rows=res)


def main():
    win = json.loads((Path(__file__).resolve().parent / 'passes_windows.json').read_text(encoding='utf-8'))
    out = C.WORK / 'tracks'
    out.mkdir(parents=True, exist_ok=True)
    rf = sys.argv[1] if len(sys.argv) > 1 else ''
    kf = sys.argv[2] if len(sys.argv) > 2 else ''
    roads = {}
    for r in win['rows']:
        if 'window' not in r or rf not in r['race'] or (kf and r['kerb'] != kf):
            continue
        race = r['race']
        if race not in roads:
            roads[race] = road_mask_v2(race)
        p = C.WORK / 'masks' / f'{race}_L{r["lap"]}_{r["kerb"]}_rows.npz'
        if not p.exists():
            continue
        t = tracks_for(p, r['side'], roads[race])
        t.update(race=race, lap=r['lap'], kerb=r['kerb'], side=r['side'])
        (out / f'{race}_L{r["lap"]}_{r["kerb"]}.json').write_text(json.dumps(t), encoding='utf-8')
        print(race, r['lap'], r['kerb'], {y: int(sum(e is not None for e in t['rows'][y]['edge'])) for y in ROWS},
              {y: int(sum(t['rows'][y]['red'])) for y in ROWS}, flush=True)


if __name__ == '__main__':
    main()
