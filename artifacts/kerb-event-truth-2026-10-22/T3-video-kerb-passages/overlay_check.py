"""Diagnostic only: draw the replay-predicted kerb lines (kerb_zones paint/block/outer, camera profile, replay pose,
flat local road) over decoded frames. Images are written ONLY to $T3_WORK/overlay (outside the repository).
usage: python overlay_check.py <race> <lap> <lapTimeT> [<lapTimeT> ...]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main():
    race, lap = sys.argv[1], int(sys.argv[2])
    Ts = [float(x) for x in sys.argv[3:]]
    course = C.Course()
    zones = C.load_zones()
    L = C.Lap(race, lap, course)
    p0 = C.lap_start_pts(race, lap, L.meta)
    cam = C.cam_profile()
    out = C.WORK / 'overlay'
    out.mkdir(parents=True, exist_ok=True)
    for T in Ts:
        fr, pts = C.decode_frames(race, p0 + T, 1)
        Tf = pts[0] - p0
        st = L.at(Tf - L.delta)
        cam_xy = st['xy'] + st['fwd'] * cam['fwd'] + st['right'] * cam['right']
        img = Image.fromarray(fr[0])
        dr = ImageDraw.Draw(img)
        for kid, z in zones.items():
            for name, col in (('paint', (255, 255, 0)), ('block', (0, 255, 255)), ('outer', (255, 0, 255))):
                P = z[name]
                d = P - cam_xy
                xr = d @ st['right']; zf = d @ st['fwd']
                m = (zf > 1.0) & (zf < 80)
                if m.sum() < 2:
                    continue
                u, v = C.ground_to_pixel(xr[m], zf[m], cam['f'], cam['h'], cam['pitch'], cam['yaw'], cam['roll'])
                pts_ = list(zip(u.tolist(), v.tolist()))
                dr.line(pts_, fill=col, width=2)
                if name == 'block':
                    # mark both ends of the zone
                    for j in (0, len(P) - 1):
                        if m[j]:
                            uu, vv = C.ground_to_pixel(xr[j:j + 1], zf[j:j + 1], cam['f'], cam['h'], cam['pitch'], cam['yaw'], cam['roll'])
                            dr.ellipse([uu[0] - 6, vv[0] - 6, uu[0] + 6, vv[0] + 6], outline=(255, 0, 0), width=3)
                            dr.text((uu[0] + 8, vv[0] - 8), f'{kid}{"s" if j == 0 else "e"}', fill=(255, 0, 0))
        dr.text((20, 120), f'{race} L{lap} T={Tf:.3f} pts={pts[0]:.3f} s={st["s"]:.1f} v={st["v"]:.1f}', fill=(255, 255, 0))
        img.save(out / f'{race}_L{lap}_T{Tf:07.3f}.jpg', quality=88)
        print(out / f'{race}_L{lap}_T{Tf:07.3f}.jpg', round(st['s'], 1))


if __name__ == '__main__':
    main()
