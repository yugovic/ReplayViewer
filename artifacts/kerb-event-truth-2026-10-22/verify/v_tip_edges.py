"""Verifier step 5: own pixel measurement of the inside-kerb road-side edge per image row around a T3 tip.

Decodes full-res RGB frames (ffmpeg accurate seek, -copyts, showinfo PTS) for [pts0, pts1], and for each row in
ROWS finds the first column (scanning from the image centre towards the kerb side) where a >= 3 px run of
'marking' pixels (luminance > row asphalt median + 18, saturation < 60) follows >= 6 'asphalt' pixels
(|lum - median| < 28, sat < 32). The asphalt median is taken over x 520-760 of the same row. The tip per row
is the extreme toward the centre (parabola through the +-2 frames around the extreme of a 3-frame median).
Only numbers are written ($VERIFY_WORK/tipedges_<tag>.json); no pixels are stored.
usage: python v_tip_edges.py <video> <pts0> <pts1> <side left|right> <tag>
"""
import json, os, re, subprocess, sys
from pathlib import Path
import numpy as np
video, p0, p1, side, tag = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
WORK = Path(os.environ['VERIFY_WORK'])
ROWS = [264, 268, 272, 276, 280, 284]
W, H = 1280, 720
cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-ss', f'{p0:.3f}', '-t', f'{p1 - p0:.3f}', '-i', video, '-copyts', '-an',
       '-vf', 'showinfo', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
r = subprocess.run(cmd, capture_output=True)
raw = np.frombuffer(r.stdout, np.uint8)
n = raw.size // (W * H * 3)
fr = raw[:n * W * H * 3].reshape(n, H, W, 3)
pts = np.array([float(x) for x in re.findall(r'n:\s*\d+.*?pts_time:([0-9.]+)', r.stderr.decode('utf-8', 'replace'))][:n])
out = {'pts': pts.round(4).tolist(), 'rows': {}}
for y in ROWS:
    f = fr[:, y, :, :].astype(int)
    lum = f.mean(-1); sat = f.max(-1) - f.min(-1)
    edges = []
    for k in range(n):
        La = np.median(lum[k, 520:760])
        asph = (np.abs(lum[k] - La) < 28) & (sat[k] < 32)
        mark = (lum[k] > La + 18) & (sat[k] < 60)
        xs = range(560, 1100) if side == 'right' else range(720, 180, -1)
        g = 0; e = None
        for x in xs:
            step = 1 if side == 'right' else -1
            if mark[x] and mark[x + step] and mark[x + 2 * step]:
                if g >= 6:
                    e = x; break
                g = 0
            elif asph[x]:
                g += 1
        edges.append(e)
    u = np.array([np.nan if e is None else e for e in edges], float)
    um = np.array([np.nanmedian(u[max(0, i - 1):i + 2]) for i in range(n)])
    s = -um if side == 'right' else um
    k = int(np.nanargmax(np.where(np.isfinite(s), s, -np.inf)))
    tip = None
    if 2 <= k < n - 2 and np.isfinite(s[k - 2:k + 3]).all():
        c = np.polyfit(pts[k - 2:k + 3] - pts[k], s[k - 2:k + 3], 2)
        tip = float(pts[k] - c[1] / (2 * c[0])) if c[0] < 0 else float(pts[k])
    out['rows'][str(y)] = dict(edge=[None if not np.isfinite(v) else int(v) for v in u], tipPts=tip, tipU=None if not np.isfinite(um[k]) else float(um[k]))
    print(tag, 'row', y, 'tip PTS', None if tip is None else round(tip, 3), 'u', um[k])
(WORK / f'tipedges_{tag}.json').write_text(json.dumps(out), encoding='utf-8')
