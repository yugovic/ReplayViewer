"""T2 step 2c: ground-plane yaw proxy. Same decoding/masks as t2_video_egomotion.py (copied), plus:
for a flat ground plane and a camera translating along the car axis, the translation flow at image row y is
u_t = k(y) * (x - xf) with k depending on the row only (depth of ground pixels depends on the row only), so
per frame  u = A + sum_b k_b * (x - xf) * [row in band b]  over textured scene pixels of rows 200-292 px on BOTH
sides of the FOE column (left / centre / right windshield areas), IRLS (Huber). A is the yaw term, with the
parallax removed for ground pixels whatever the road curvature; vertical objects are down-weighted by IRLS.
Also stores left / right medians of u for diagnostics. No frames are written anywhere.

(Original description of the copied radial-FOE model follows.)
Per-frame camera rotation (yaw / pitch / roll proxies) from the windshield scene, with the
forward-translation (parallax) flow eliminated.

For a static scene seen by a camera translating along the car's forward axis and rotating, each pixel's flow
is  (u, v) = (A, B) + k * (x - xf, y - yf) + R * (-(y - yc), (x - xc))
(A ~ f*yaw*dt, B ~ f*pitch*dt, R = roll*dt, k = V*dt/Z unknown per pixel, FOE (xf, yf) on the horizon).
Eliminating k:  A*(y - yf) - B*(x - xf) + R*[...] = u*(y - yf) - v*(x - xf), linear in (A, B, R) and
independent of depth. Solved per frame by IRLS (Huber) on textured pixels of the visible windshield scene
(sticker / air duct / mirror / pillars / dashboard masked). Magnitudes are in px/frame of the half-res crop
and are NOT calibrated (focal length unknown); only timing / correlation is used downstream.

Output: <T2_WORK>/video/<tag>_ground.npz (Ag, nGround, uMedL, uMedR, uMedFar, A per frame pair; pts_mid).
Usage: python t2_video_ground.py <race> <pts_start> <pts_end>
"""
import re
import subprocess
import sys

import cv2
import numpy as np

from t2_common import TAG, VIDEO, WORK

# full-res geometry of the crop
X0, Y0, W, H = 200, 150, 900, 320          # x 200-1100, y 150-470
S = 2                                      # half resolution
w, h = W // S, H // S
XF, YF = 640.0, 195.0                      # assumed FOE (image centre column, horizon row), full res
XC, YC = 640.0, 360.0                      # assumed principal point for the roll term
# visible windshield scene (full-res rectangles x0, x1, y0, y1)
SCENE = [(245, 465, 195, 292), (465, 650, 232, 292), (650, 1065, 160, 292)]
DASH = (230, 760, 330, 460)


def first_output_pts(path):
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-t", "2", "-map", "0:v:0", "-fps_mode", "passthrough",
                        "-vf", "showinfo", "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    m = re.search(r"n:\s*0\s+pts:\s*\d+\s+pts_time:([0-9.]+)", r.stderr)
    return float(m.group(1))


def build():
    ys, xs = np.mgrid[0:h, 0:w]
    X = X0 + (xs + 0.5) * S
    Y = Y0 + (ys + 0.5) * S
    mask = np.zeros((h, w), bool)
    for x0, x1, y0, y1 in SCENE:
        mask |= (X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)
    return X, Y, mask


def main():
    race, p0, p1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    tag = TAG[race]
    first = first_output_pts(VIDEO[race])
    X, Y, scene = build()
    xp, yp = (X - XF) / S, (Y - YF) / S        # half-res pixel units relative to FOE
    xr, yr = (X - XC) / S, (Y - YC) / S
    dx0, dx1, dy0, dy1 = [(v - o) // S for v, o in zip(DASH, (X0, X0, Y0, Y0))]
    hann = cv2.createHanningWindow((dx1 - dx0, dy1 - dy0), cv2.CV_64F)
    cmd = ["ffmpeg", "-v", "error", "-i", VIDEO[race], "-t", f"{p1:.3f}", "-map", "0:v:0", "-fps_mode", "passthrough",
           "-vf", f"crop={W}:{H}:{X0}:{Y0},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 64)
    out = {k: [] for k in ("A", "B", "R", "npx", "resid", "Dx", "Dy", "Dr", "uMedFar", "vMedFar", "Ag", "nGround", "uMedL", "uMedR")}
    pts_mid = []
    prev = None
    i = -1
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h:
            break
        i += 1
        cur = np.frombuffer(buf, np.uint8).reshape(h, w)
        t = first + i / 30.0
        if prev is not None and t - 1 / 30.0 >= p0:
            fl = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 3, 13, 3, 5, 1.1, 0)
            u, v = fl[..., 0], fl[..., 1]
            gx = cv2.Sobel(prev, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(prev, cv2.CV_32F, 0, 1, ksize=3)
            tex = np.hypot(gx, gy) > 24.0
            m = scene & tex
            n = int(m.sum())
            A = B = R = res = np.nan
            if n > 60:
                a1, a2 = yp[m], -xp[m]
                a3 = -(yr[m] * yp[m] + xr[m] * xp[m])
                M = np.stack([a1, a2, a3], 1)
                rhs = u[m] * yp[m] - v[m] * xp[m]
                wts = np.ones(n)
                for _ in range(3):
                    sw = np.sqrt(wts)
                    sol, *_ = np.linalg.lstsq(M * sw[:, None], rhs * sw, rcond=None)
                    r = rhs - M @ sol
                    s = 1.4826 * np.median(np.abs(r)) + 1e-6
                    c = 1.5 * s
                    wts = np.where(np.abs(r) <= c, 1.0, c / np.abs(r))
                A, B, R = (float(x) for x in sol)
                res = float(s)
            out["A"].append(A)
            out["B"].append(B)
            out["R"].append(R)
            out["npx"].append(n)
            out["resid"].append(res)
            far = m & (Y < 200)
            out["uMedFar"].append(float(np.median(u[far])) if far.sum() > 20 else np.nan)
            out["vMedFar"].append(float(np.median(v[far])) if far.sum() > 20 else np.nan)
            g = m & (Y >= 200)
            ng = int(g.sum())
            Ag = np.nan
            if ng > 80:
                bands = np.clip(((Y[g] - 200) // 12).astype(int), 0, 7)
                xg = xp[g]
                Mg = np.zeros((ng, 9))
                Mg[:, 0] = 1.0
                Mg[np.arange(ng), 1 + bands] = xg
                ug = u[g]
                wts = np.ones(ng)
                for _ in range(3):
                    sw = np.sqrt(wts)
                    sol, *_ = np.linalg.lstsq(Mg * sw[:, None], ug * sw, rcond=None)
                    r = ug - Mg @ sol
                    s_ = 1.4826 * np.median(np.abs(r)) + 1e-6
                    c = 1.5 * s_
                    wts = np.where(np.abs(r) <= c, 1.0, c / np.abs(r))
                Ag = float(sol[0])
            out["Ag"].append(Ag)
            out["nGround"].append(ng)
            L_ = g & (X < 465)
            R_ = g & (X >= 650)
            out["uMedL"].append(float(np.median(u[L_])) if L_.sum() > 20 else np.nan)
            out["uMedR"].append(float(np.median(u[R_])) if R_.sum() > 20 else np.nan)
            (sx, sy), rr = cv2.phaseCorrelate(prev[dy0:dy1, dx0:dx1].astype(np.float64),
                                              cur[dy0:dy1, dx0:dx1].astype(np.float64), hann)
            out["Dx"].append(sx)
            out["Dy"].append(sy)
            out["Dr"].append(rr)
            pts_mid.append(t - 0.5 / 30.0)
        prev = cur
        if i % 3000 == 0:
            print(race, "frame", i, flush=True)
    proc.wait()
    arr = {k: np.asarray(v, np.float32) for k, v in out.items()}
    arr["pts_mid"] = np.asarray(pts_mid, np.float64)
    arr["first_pts"] = np.float64(first)
    arr["foe"] = np.array([XF, YF])
    (WORK / "video").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK / "video" / f"{tag}_ground.npz", **arr)
    print(race, "done", len(pts_mid))


if __name__ == "__main__":
    main()
