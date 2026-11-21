"""Verifier step 2a (v2): video yaw proxy separated from translation parallax.

Same decode as v_video_flow.py (ffmpeg accurate input seek, -copyts, showinfo PTS per frame;
windscreen band full-res x 200-1100, y 150-300 at 1/2 scale; duct / mirror / pillar masked).
For every frame pair, Farneback flow; for every image row a robust least-squares line
    dx(x) = a_row + b_row * (x - xc),   xc = principal column (full-res x 640)
(forward translation over the ground gives dx proportional to (x - x_FOE) * (y - y_horizon); yaw gives a
uniform dx). Saved per frame pair: a_row, b_row, valid count per row (75 rows), plus the plain median dx.
The yaw estimate (a_row extrapolated to the horizon row where b_row -> 0) is formed in v_offset2.py.
Output $VERIFY_WORK/flow2_<tag>.npz (derived numbers only; no pixels).

usage: python v_video_flow2.py <video> <pts_start> <pts_end> <tag>     (pipeline/.venv-sr python, cv2)
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.dont_write_bytecode = True
video, p0, p1, tag = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
WORK = Path(os.environ['VERIFY_WORK'])
W, H = 450, 75
XC = (640 - 200) / 2.0
errlog = WORK / f'flow2_{tag}_showinfo.log'
cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-ss', f'{p0:.3f}', '-t', f'{p1 - p0:.3f}', '-i', video, '-copyts',
       '-an', '-vf', 'crop=900:150:200:150,scale=450:75,showinfo', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
mask = np.ones((H, W), bool)
mask[:, 132:226] = False          # air duct (full-res x 465-650)
mask[:8, 226:] = False            # mirror lower edge
mask[:, 440:] = False             # A-pillar edge
X = np.arange(W, dtype=float) - XC
A_rows, B_rows, N_rows, MED = [], [], [], []
with open(errlog, 'w') as ef:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef, bufsize=W * H * 16)
    prev = None
    n = 0
    while True:
        buf = proc.stdout.read(W * H)
        if len(buf) < W * H:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, img, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dx = fl[..., 0]
            MED.append(float(np.median(dx[mask])))
            a = np.full(H, np.nan)
            b = np.full(H, np.nan)
            cnt = np.zeros(H)
            for r in range(H):
                m = mask[r]
                xr, yr = X[m], dx[r, m]
                keep = np.ones(len(xr), bool)
                for _ in range(3):
                    if keep.sum() < 40:
                        break
                    Am = np.stack([np.ones(keep.sum()), xr[keep]], 1)
                    coef, *_ = np.linalg.lstsq(Am, yr[keep], rcond=None)
                    res = yr - (coef[0] + coef[1] * xr)
                    s = 1.4826 * np.median(np.abs(res[keep])) + 0.05
                    keep = np.abs(res) < 2.5 * s
                if keep.sum() >= 40:
                    a[r], b[r], cnt[r] = coef[0], coef[1], keep.sum()
            A_rows.append(a)
            B_rows.append(b)
            N_rows.append(cnt)
        prev = img
        n += 1
    proc.wait()
pts = []
for line in open(errlog, encoding='utf-8', errors='replace'):
    m = re.search(r'n:\s*(\d+).*?pts_time:([0-9.]+)', line)
    if m and 'Parsed_showinfo' in line:
        pts.append(float(m.group(2)))
pts = np.array(pts[:n])
assert len(pts) == n and n > 1, (len(pts), n)
np.savez_compressed(WORK / f'flow2_{tag}.npz', pts=pts, mid=0.5 * (pts[1:] + pts[:-1]), a=np.array(A_rows, np.float32),
                    b=np.array(B_rows, np.float32), cnt=np.array(N_rows, np.int16), med=np.array(MED))
print(tag, 'frames', n, 'pts', pts[0], pts[-1])
