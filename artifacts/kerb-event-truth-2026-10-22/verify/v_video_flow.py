"""Verifier step 2a: own video yaw proxy (global horizontal image motion) for one lap window.

Decodes the onboard video with ffmpeg (accurate input seek, -copyts, so every frame keeps its
container PTS; showinfo gives the PTS of each decoded frame), crops the windscreen band
(full-res x 200-1100, y 150-300, scaled 1/2), masks the static air duct / mirror, and computes
Farneback dense optical flow between consecutive frames. Output per frame pair: median dx and
dy (px at 640-wide scale) and the pair's mid PTS. Saved to $VERIFY_WORK/flow_<tag>.npz
(derived numbers only; no pixels are stored).

usage: python v_video_flow.py <video> <pts_start> <pts_end> <tag>
Run with pipeline/.venv-sr python (cv2).
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
W, H = 450, 75            # crop 900x150 at 1/2 scale
errlog = WORK / f'flow_{tag}_showinfo.log'
cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-ss', f'{p0:.3f}', '-t', f'{p1 - p0:.3f}', '-i', video, '-copyts',
       '-an', '-vf', 'crop=900:150:200:150,scale=450:75,showinfo', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
with open(errlog, 'w') as ef:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef, bufsize=W * H * 16)
    frames = []
    prev = None
    dxs, dys, ok = [], [], []
    mask = np.ones((H, W), bool)
    mask[:, 132:226] = False          # air duct (full-res x 465-650)
    mask[:8, 226:] = False            # mirror lower edge (full-res y < 166 right of the duct)
    mask[:, 440:] = False             # A-pillar edge
    n = 0
    while True:
        buf = proc.stdout.read(W * H)
        if len(buf) < W * H:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, img, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dx = fl[..., 0][mask]
            dy = fl[..., 1][mask]
            dxs.append(float(np.median(dx)))
            dys.append(float(np.median(dy)))
            # fraction of pixels agreeing with the median within 1 px (wiper / occlusion indicator)
            ok.append(float(np.mean(np.abs(dx - dxs[-1]) < 1.0)))
        prev = img
        n += 1
    proc.wait()
pts = []
for line in open(errlog, encoding='utf-8', errors='replace'):
    m = re.search(r'n:\s*(\d+).*?pts_time:([0-9.]+)', line)
    if m and 'Parsed_showinfo' in line:
        pts.append(float(m.group(2)))
pts = np.array(pts[:n])
assert len(pts) == n, (len(pts), n)
mid = 0.5 * (pts[1:] + pts[:-1])
np.savez_compressed(WORK / f'flow_{tag}.npz', pts=pts, mid=mid, dx=np.array(dxs), dy=np.array(dys), agree=np.array(ok))
print(tag, 'frames', n, 'pts', pts[0], pts[-1], 'median frame dt', float(np.median(np.diff(pts))))
