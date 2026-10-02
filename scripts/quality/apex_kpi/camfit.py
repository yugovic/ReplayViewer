"""Edge-snapping fit of SmartyCam camera parameters and per-frame pose corrections to CG course lines."""
from __future__ import annotations
import json, math, subprocess, sys
import numpy as np, cv2
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from scipy.optimize import least_squares
from camproj import *  # noqa

SP = str(WORK)
READS = json.loads((ROOT / 'artifacts/video-position-gap-2026-10-02/video-timer-reads.json').read_text(encoding='utf-8'))['sessions']


def exterior_mask(race, lap=3, n=120):
    """Pixels that change over a lap = outside world. Interior/overlay are static."""
    path = f'{SP}/mask_{race}.png'
    try:
        m = cv2.imread(path, 0)
        if m is not None:
            return m > 0
    except Exception:
        pass
    p0 = video_pts_of_lap_t(race, lap, 2.0, READS)
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{p0:.3f}', '-i', VID[race], '-t', '120', '-vf', 'fps=1,format=gray', '-f', 'rawvideo', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H_IMG, W_IMG).astype(np.float32)
    sd = fr.std(axis=0)
    m = (cv2.GaussianBlur(sd, (0, 0), 3) > 18).astype(np.uint8)
    m = cv2.erode(m, np.ones((9, 9), np.uint8))
    m[:140, :300] = 0; m[:135, 1070:] = 0; m[440:, :] = 0   # overlay map/logo, lower overlays
    cv2.imwrite(path, m * 255)
    return m > 0


def edge_image(img_rgb):
    g = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    r = img_rgb[..., 0].astype(np.float32) - 0.5 * (img_rgb[..., 1].astype(np.float32) + img_rgb[..., 2].astype(np.float32))
    out = []
    for ch in (g, r):
        ch = cv2.GaussianBlur(ch, (0, 0), 1.2)
        gx = cv2.Sobel(ch, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(ch, cv2.CV_32F, 0, 1, ksize=3)
        out.append((gx, gy))
    return out


def snap(lines, edges, mask, radius=18, step_px=10, min_depth=3.0, max_depth=40.0, min_mag=25.0):
    """For each projected line, sample along it and find the strongest edge along the image normal.
    Returns list of (name, s_station, depth, proj_xy, snapped_xy)."""
    obs = []
    H, W = mask.shape
    for name, (px, Z, s) in lines.items():
        ok = (Z > min_depth) & (Z < max_depth) & (px[:, 0] > 2) & (px[:, 0] < W - 3) & (px[:, 1] > 2) & (px[:, 1] < H - 3)
        idx = np.flatnonzero(ok)
        if len(idx) < 2:
            continue
        # resample in image arc length
        last = None
        for j in idx:
            if j + 1 >= len(px):
                continue
            p = px[j]
            if last is not None and np.hypot(*(p - last)) < step_px:
                continue
            tvec = px[min(j + 1, len(px) - 1)] - px[max(j - 1, 0)]
            nt = np.hypot(*tvec)
            if nt < 1e-3:
                continue
            n = np.array([-tvec[1], tvec[0]]) / nt
            best = (0.0, None)
            for o in range(-radius, radius + 1):
                q = p + o * n
                xi, yi = int(round(q[0])), int(round(q[1]))
                if not (0 <= xi < W and 0 <= yi < H) or not mask[yi, xi]:
                    continue
                mag = 0.0
                for gx, gy in edges:
                    mag = max(mag, abs(gx[yi, xi] * n[0] + gy[yi, xi] * n[1]))
                if mag > best[0]:
                    best = (mag, q)
            if best[1] is not None and best[0] > min_mag:
                obs.append((name, float(s[j]), float(Z[j]), p.copy(), best[1]))
            last = p
    return obs


CAM_KEYS = ['f', 'k1', 'h', 'pitch', 'yaw', 'roll']
HEAD_PRIOR_DEG = 1.0


def residuals(theta, frames, course, cam0, fit_cam=True):
    cam = dict(cam0)
    k = 0
    if fit_cam:
        for key in CAM_KEYS:
            cam[key] = theta[k]; k += 1
    res = []
    for fr in frames:
        dlat, dhead = theta[k], theta[k + 1]; k += 2
        st = fr['state']
        h = st['h'] + dhead
        rgt = np.array([-math.cos(h), math.sin(h)])
        xy = st['xy'] + dlat * rgt
        for (name, s, Z, p, q, P3) in fr['obs']:
            px, _ = project(P3[None, :], xy, st['y'], h, cam, st['pitch'], st['roll'])
            # residual along the stored image normal (point-to-line)
            res.append(np.dot(px[0] - q, fr['nrm'][(name, s)]))
        if fr.get('head_prior', True):
            res.append(dhead / math.radians(HEAD_PRIOR_DEG) * 3.0)
    return np.array(res)


def collect(course, race, lap, t, cam, tau, mask, names=None, radius=18):
    pose = LapPose(race, lap)
    img = frame(race, video_pts_of_lap_t(race, lap, t, READS))
    st = car_state(course, pose, t + tau)
    L = projected_lines(course, st, cam, names=names)
    obs = snap(L, edge_image(img), mask, radius=radius)
    full = []; nrm = {}
    for (name, s, Z, p, q) in obs:
        S, P = course.line3d(name)
        j = int(np.argmin(np.abs(S - s)))
        # local image tangent of projected line for normal
        px, _ = L[name][0], None
        jj = int(np.argmin(np.abs(L[name][2] - s)))
        a = L[name][0][max(jj - 1, 0)]; b = L[name][0][min(jj + 1, len(L[name][0]) - 1)]
        tv = b - a; tv = tv / max(np.hypot(*tv), 1e-6)
        nrm[(name, s)] = np.array([-tv[1], tv[0]])
        full.append((name, s, Z, p, q, P[j]))
    return dict(race=race, lap=lap, t=t, state=st, obs=full, nrm=nrm, img=img)


def fit(frames, course, cam0, fit_cam=True, loss='soft_l1', f_scale=3.0):
    x0 = ([cam0[k] for k in CAM_KEYS] if fit_cam else []) + [0.0, 0.0] * len(frames)
    lb = ([400, -0.6, 0.9, math.radians(5), math.radians(-10), math.radians(-6)] if fit_cam else []) + [-4, -0.15] * len(frames)
    ub = ([800, 0.3, 1.7, math.radians(25), math.radians(10), math.radians(6)] if fit_cam else []) + [4, 0.15] * len(frames)
    r = least_squares(residuals, x0, args=(frames, course, cam0, fit_cam), loss=loss, f_scale=f_scale, bounds=(lb, ub))
    cam = dict(cam0); k = 0
    if fit_cam:
        for key in CAM_KEYS:
            cam[key] = float(r.x[k]); k += 1
    per = [(float(r.x[k + 2 * i]), float(r.x[k + 2 * i + 1])) for i in range(len(frames))]
    return cam, per, r


def draw(fr, course, cam, dlat=0.0, dhead=0.0, path=None, tau=0.2):
    img = fr['img'][:, :, ::-1].copy()
    L = projected_lines(course, fr['state'], cam, dlat=dlat, dhead=dhead)
    for name, (px, Z, s) in L.items():
        ok = (Z > 2.0) & (Z < 60)
        col = (0, 255, 255) if name.startswith('road') else ((0, 0, 255) if name.endswith('in') else (255, 0, 255))
        p = np.round(px).astype(int)
        for a, b, o1, o2 in zip(p[:-1], p[1:], ok[:-1], ok[1:]):
            if o1 and o2 and abs(a[0]) < 4000 and abs(b[0]) < 4000:
                cv2.line(img, tuple(a), tuple(b), col, 1, cv2.LINE_AA)
    for (name, s, Z, p, q, P3) in fr['obs']:
        cv2.circle(img, (int(q[0]), int(q[1])), 2, (0, 255, 0), -1)
    cv2.putText(img, f"{fr['race']} L{fr['lap']} t={fr['t']:.2f} dlat={dlat:+.2f} dhead={math.degrees(dhead):+.2f}deg",
                (10, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    if path:
        cv2.imwrite(path, img[:420])
    return img
