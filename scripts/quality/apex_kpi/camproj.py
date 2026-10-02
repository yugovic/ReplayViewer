"""Project CG course lines (road edges, kerb edges) into SmartyCam frames using the GPS pose.
Local analysis helper; frames stay in the scratchpad."""
from __future__ import annotations
import json, math, subprocess, sys, datetime as dt
from pathlib import Path
import numpy as np
import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT, WORK, XRK, VIDEO  # noqa: E402
from scipy.interpolate import CubicSpline

ROOT = ROOT
sys.path.insert(0, str(ROOT / 'pipeline'))
from register_gps_to_track import to_local  # noqa

VID = VIDEO
W_IMG, H_IMG = 1280, 720


def wall(s): return dt.datetime.fromisoformat(s.replace('Z', ''))


class Course:
    def __init__(self):
        g = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
        tr = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))
        self.origin = tr['origin']; self.width = tr['width']
        cl = tr['centerline']
        self.cd = np.array([p['dist'] for p in cl]); self.total = tr['totalLength']
        oa = tr['origin']['alt']
        self.altL = np.array([p['altLeft'] for p in cl]) - oa
        self.altR = np.array([p['altRight'] for p in cl]) - oa
        road = np.array([r[:11] for r in g['road']], float)
        self.st = road[:, 0]; self.centre = road[:, 1:3]
        n = road[:, 3:5]; self.normal = n / np.linalg.norm(n, axis=1)[:, None]
        self.lines = {}
        self.lines['roadL'] = (self.st.copy(), road[:, 7:9].copy())
        self.lines['roadR'] = (self.st.copy(), road[:, 9:11].copy())
        for i, c in enumerate(g['curbs']):
            rows = np.array(c['rows'], float)
            self.lines[f'k{i}{c["side"][0].upper()}in'] = (rows[:, 0], rows[:, 1:3])
            self.lines[f'k{i}{c["side"][0].upper()}out'] = (rows[:, 0], rows[:, 3:5])

    def height(self, s, xy):
        s = np.mod(s, self.total)
        aL = np.interp(s, self.cd, self.altL); aR = np.interp(s, self.cd, self.altR)
        i = np.clip(np.round(s / 0.5).astype(int), 0, len(self.st) - 1)
        q = np.einsum('ij,ij->i', xy - self.centre[i], self.normal[i])
        t = np.clip((q + self.width / 2) / self.width, -0.5, 1.5)
        return aL + (aR - aL) * t

    def line3d(self, name):
        s, xy = self.lines[name]
        return s, np.column_stack([xy[:, 0], self.height(s, xy), xy[:, 1]])

    def station_of(self, xy):
        d = np.linalg.norm(self.centre - xy[None, :], axis=1); i = int(np.argmin(d))
        return self.st[i], i


class LapPose:
    def __init__(self, race, lap, registered=True):
        self.race, self.lap = race, lap
        d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
        self.d = d; self.meta = d['meta']
        tr = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))
        xy = to_local(d['lat'], d['lng'], tr['origin'])
        if registered:
            reg = json.loads((ROOT / f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
            off = {l['lap']: l['offsetMeters'] for l in reg['laps']}[lap]
            xy = xy + np.array(off)
        self.t = np.array(d['t'], float)
        self.cx = CubicSpline(self.t, xy[:, 0]); self.cz = CubicSpline(self.t, xy[:, 1])

    def at(self, t):
        x, z = float(self.cx(t)), float(self.cz(t))
        vx, vz = float(self.cx(t, 1)), float(self.cz(t, 1))
        h = math.atan2(vx, vz)  # forward = (sin h, cos h) in (x, z)
        return np.array([x, z]), h, math.hypot(vx, vz)


def video_pts_of_lap_t(race, lap, t, timer_reads):
    cfg = timer_reads[race]
    d = json.loads((ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').read_text(encoding='utf-8'))
    m = d['meta']
    lap_start = ((wall(m['lap_start_time']) - wall(cfg['referenceLapStart'])).total_seconds()
                 + cfg['referencePtsSeconds'] - cfg['perLapCorrectionSeconds'][str(lap)])
    return lap_start + m['first_sample_after_lap_start_seconds'] + t


def frame(race, pts):
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{pts:.3f}', '-i', VID[race], '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H_IMG, W_IMG, 3).copy()


def rot(yaw, pitch, roll):
    """camera-from-car rotation. car axes (right, down, forward); pitch>0 tilts camera down, yaw>0 to the right, roll>0 clockwise."""
    cy_, sy = math.cos(yaw), math.sin(yaw)
    Ry = np.array([[cy_, 0, -sy], [0, 1, 0], [sy, 0, cy_]])
    cp, sp = math.cos(pitch), math.sin(pitch)
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    cr, sr = math.cos(roll), math.sin(roll)
    Rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return Rz @ Rx @ Ry


def project(P3, car_xy, car_h, heading, cam, car_pitch=0.0, car_roll=0.0):
    """P3: (n,3) world (x, y_up, z_south). Returns pixels (n,2) and depth (n,)."""
    f = np.array([math.sin(heading), 0.0, math.cos(heading)])
    r = np.array([-math.cos(heading), 0.0, math.sin(heading)])  # driver's right
    u = np.array([0.0, 1.0, 0.0])
    # apply road pitch/roll to car body axes
    f2 = f * math.cos(car_pitch) + u * math.sin(car_pitch)
    u2 = u * math.cos(car_pitch) - f * math.sin(car_pitch)
    r2 = r * math.cos(car_roll) + u2 * math.sin(car_roll)
    u3 = u2 * math.cos(car_roll) - r * math.sin(car_roll)
    cam_pos = np.array([car_xy[0], car_h, car_xy[1]]) + r2 * cam['clat'] + f2 * cam['cfwd'] + u3 * cam['h']
    rel = P3 - cam_pos
    pc = np.stack([rel @ r2, -(rel @ u3), rel @ f2], 1)       # right, down, forward
    pc = pc @ rot(cam['yaw'], cam['pitch'], cam['roll']).T
    Z = pc[:, 2]
    xu, yu = pc[:, 0] / np.maximum(Z, 1e-6), pc[:, 1] / np.maximum(Z, 1e-6)
    r2_ = xu * xu + yu * yu
    k = 1 + cam['k1'] * r2_ + cam.get('k2', 0.0) * r2_ * r2_
    px = cam['cx'] + cam['f'] * xu * k
    py = cam['cy'] + cam['f'] * yu * k
    bad = (Z <= 1e-3) | (r2_ > 1.8)
    Z = np.where(bad, -1.0, Z)
    return np.stack([px, py], 1), Z


DEFAULT_CAM = dict(f=575.0, k1=-0.18, k2=0.0, cx=640.0, cy=360.0, h=1.25, clat=0.0, cfwd=-0.6,
                   pitch=math.radians(14.0), yaw=0.0, roll=0.0)


def car_state(course, pose, t):
    xy, h, v = pose.at(t)
    s, i = course.station_of(xy)
    y = float(course.height(np.array([s]), xy[None, :])[0])
    fwd = np.array([math.sin(h), math.cos(h)])
    yf = float(course.height(np.array([s + 3.0]), (xy + 3 * fwd)[None, :])[0])
    yb = float(course.height(np.array([s - 3.0]), (xy - 3 * fwd)[None, :])[0])
    pitch = math.atan2(yf - yb, 6.0)
    rgt = np.array([-math.cos(h), math.sin(h)])
    yr = float(course.height(np.array([s]), (xy + 1.5 * rgt)[None, :])[0])
    yl = float(course.height(np.array([s]), (xy - 1.5 * rgt)[None, :])[0])
    roll = math.atan2(yl - yr, 3.0)   # left higher -> body leans right (positive)
    return dict(xy=xy, h=h, v=v, s=s, y=y, pitch=pitch, roll=roll)


def projected_lines(course, st, cam, max_dist=45.0, min_depth=1.5, dlat=0.0, dhead=0.0, names=None):
    """Return {name: (pixels, depth)} for course lines within max_dist of the car."""
    out = {}
    h = st['h'] + dhead
    rgt = np.array([-math.cos(h), math.sin(h)])
    xy = st['xy'] + dlat * rgt
    for name in (names or course.lines.keys()):
        s, P = course.line3d(name)
        d2 = (P[:, 0] - xy[0]) ** 2 + (P[:, 2] - xy[1]) ** 2
        m = d2 < max_dist ** 2
        if m.sum() < 2:
            continue
        px, Z = project(P[m], xy, st['y'], h, cam, st['pitch'], st['roll'])
        ok = Z > min_depth
        if ok.sum() >= 2:
            out[name] = (px[ok], Z[ok], s[m][ok])
    return out
