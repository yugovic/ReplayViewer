"""T3 step 2: decode every inside-kerb pass window at 30 fps (ffmpeg accurate PTS seek) and save colour masks
of the windshield road band. Masks/frames are written ONLY to $T3_WORK/masks (outside the repository).

Masks (rows Y0..Y1 of the 1280x720 frame, packed bits along x):
  red   kerb red   : R-G > 28, R-B > 18, R > 105
  white kerb/paint : min(RGB) > 170, max-min < 48
  blue  glove      : B-R > 35, B-G > 15, B > 70  (driver's gloves; used to flag arm occlusion)
  rows  raw RGB of rows 232..292 step 2 (separate *_rows.npz, work folder only)
  road  static windshield road band (temporal std > 14 over 240 frames of the same video, opened; car-fixed)
usage: python extract.py [race-filter] [kerb-filter]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

Y0, Y1 = 170, 330
RGB_ROWS = np.arange(232, 294, 2)   # raw RGB rows kept for threshold tuning (work folder only)


def road_mask(race: str) -> np.ndarray:
    p = C.WORK / f'mask_stats_{race}.npz'
    if not p.exists():
        acc = []
        for t in np.linspace(*((180, 740) if race == 'fuji_aim_01' else (160, 920)), 240):
            fr, _ = C.decode_frames(race, float(t), 1)
            acc.append(fr[0, 100:400].astype(np.float32))
        A = np.stack(acc)
        np.savez_compressed(p, sd=A.std(0).mean(-1), med=np.median(A, 0))
    sd = np.load(p)['sd']                       # rows 100..399
    m = ndi.binary_opening(sd > 14, iterations=2)
    full = np.zeros((C.H_IMG, C.W_IMG), bool)
    full[100:400] = m
    full[:175] = False
    # windshield bottom per column: the first row (from 180 down) followed by >= 6 static rows
    for x in range(C.W_IMG):
        col = full[:, x]
        bot = None
        for y in range(200, 345):
            if col[y] and not col[y + 1:y + 7].any():
                bot = y
                break
        if bot is None:
            full[:, x] = False
        else:
            full[bot - 1:, x] = False
    full[:, :200] = False           # left side window / roll cage: not the windshield
    full[:, 1090:] = False          # right A-pillar and beyond
    return full


def masks(fr: np.ndarray, road: np.ndarray):
    f = fr.astype(np.int16)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    red = (R - G > 28) & (R - B > 18) & (R > 105)
    white = (mn > 170) & (mx - mn < 48)
    blue = (B - R > 35) & (B - G > 15) & (B > 70)
    return red & road, white & road, blue


def main():
    rf = sys.argv[1] if len(sys.argv) > 1 else ''
    kf = sys.argv[2] if len(sys.argv) > 2 else ''
    win = json.loads((Path(__file__).resolve().parent / 'passes_windows.json').read_text(encoding='utf-8'))
    out = C.WORK / 'masks'
    out.mkdir(parents=True, exist_ok=True)
    roads = {}
    for r in win['rows']:
        if 'window' not in r or rf not in r['race'] or (kf and r['kerb'] != kf):
            continue
        race = r['race']
        if race not in roads:
            roads[race] = road_mask(race)
            np.save(C.WORK / f'roadmask_{race}.npy', roads[race])
        road = roads[race][Y0:Y1]
        dst = out / f'{race}_L{r["lap"]}_{r["kerb"]}.npz'
        dst_rgb = out / f'{race}_L{r["lap"]}_{r["kerb"]}_rows.npz'
        if dst.exists() and dst_rgb.exists():
            continue
        a, b = r['window']['pts']
        n = int(round((b - a) * C.FPS)) + 1
        t0 = time.time()
        fr, pts = C.decode_frames(race, a, n)
        np.savez_compressed(dst_rgb, rgb=fr[:, RGB_ROWS], rows=RGB_ROWS, pts=pts)
        fr = fr[:, Y0:Y1]
        red, white, blue = masks(fr, road)
        np.savez_compressed(dst, red=np.packbits(red, axis=-1), white=np.packbits(white, axis=-1),
                            blue=np.packbits(blue, axis=-1), pts=pts, y0=Y0, y1=Y1,
                            meanRGB=fr.reshape(len(fr), -1, 3).mean(1))
        print(dst.name, len(pts), f'{time.time() - t0:.1f}s', flush=True)


if __name__ == '__main__':
    main()
