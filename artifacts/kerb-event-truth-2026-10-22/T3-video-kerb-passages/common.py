"""T3 shared helpers: course stations, registered lap paths, video PTS mapping, camera ground model.

Paths come from environment variables only (no personal absolute paths in this folder):
  REPLAYVIEWER_ROOT   repository root (default: three levels above this file)
  FUJI_WET_VIDEO      7/29 SmartyCam MOV (local source media, read-only)
  FUJI_DRY_VIDEO      7/30 SmartyCam MOV (local source media, read-only)
  T3_WORK             work folder OUTSIDE the repository for every video-derived file
Nothing here writes video pixels into the repository.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('REPLAYVIEWER_ROOT', HERE.parents[2]))
WORK = Path(os.environ.get('T3_WORK', '')) if os.environ.get('T3_WORK') else None
VIDEO = {'fuji_aim_01': os.environ.get('FUJI_WET_VIDEO', ''), 'fuji_aim_2020_07_30': os.environ.get('FUJI_DRY_VIDEO', '')}
RACES = ('fuji_aim_01', 'fuji_aim_2020_07_30')
DATE = {'fuji_aim_01': '2020-07-29', 'fuji_aim_2020_07_30': '2020-07-30'}
INSIDE = ['k13', 'k1', 'k15', 'k3', 'k18', 'k19', 'kDunlopR', 'k7', 'k20', 'k21', 'k9', 'k10', 'k23', 'k24']
NAMES = {'k13': '1st corner (TGR)', 'k1': 'Coca-Cola', 'k15': '1500m', 'k3': 'Hairpin', 'k18': '2420m (100R)',
         'k19': '2650m', 'kDunlopR': 'Dunlop right', 'k7': 'Dunlop left', 'k20': '3090m', 'k21': '3250m',
         'k9': '3350m', 'k10': '3540m', 'k23': '3600m', 'k24': 'Final corner (Panasonic)'}
W_IMG, H_IMG, FPS = 1280, 720, 30.0


def rel(p) -> str:
    try:
        return Path(p).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return Path(p).name


def wall(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace('Z', ''))


class Course:
    """CG road centre (0.5 m stations) from cg_study/geometry.json; normal points to the driver's right."""

    def __init__(self):
        g = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/geometry.json').read_text(encoding='utf-8'))
        road = np.array([r[:11] for r in g['road']], float)
        self.st = road[:, 0]
        self.centre = road[:, 1:3]
        n = road[:, 3:5]
        self.normal = n / np.linalg.norm(n, axis=1)[:, None]
        self.tree = cKDTree(self.centre)
        self.total = float(self.st[-1] + 0.5)
        tr = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text(encoding='utf-8'))
        self.origin = tr['origin']

    def station(self, xy: np.ndarray):
        _, idx = self.tree.query(xy)
        d = xy - self.centre[idx]
        q = np.einsum('ij,ij->i', d, self.normal[idx])
        tang = np.stack([self.normal[idx, 1], -self.normal[idx, 0]], 1)  # forward = normal rotated left
        a = np.einsum('ij,ij->i', d, tang)
        return self.st[idx] + a, q, idx


def to_local(lat, lng, origin) -> np.ndarray:
    # identical to pipeline/register_gps_to_track.to_local (METERS_PER_DEGREE = 111320)
    mpd = 111320.0
    lat, lng = np.asarray(lat, float), np.asarray(lng, float)
    scale = mpd * math.cos(math.radians(origin['lat']))
    return np.stack([(lng - origin['lng']) * scale, -(lat - origin['lat']) * mpd], axis=1)


def load_zones():
    doc = json.loads((ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json').read_text(encoding='utf-8'))
    out = {}
    for kb in doc['kerbs']:
        out[kb['id']] = dict(id=kb['id'], side=kb['side'], station=np.asarray(kb['station'], float),
                             paint=np.asarray(kb['paint'], float), block=np.asarray(kb['block'], float),
                             outer=np.asarray(kb['outer'], float), observed=kb.get('observed'),
                             bandWidthMedian=kb.get('bandWidthMedian'), stripWidthMedian=kb.get('stripWidthMedian'))
    return out


class Lap:
    """Registered (shipped gps_registration.json) 50 Hz path of one shipped lap, in lap-t base."""

    def __init__(self, race: str, lap: int, course: Course, dtt: float = 0.02):
        p = ROOT / f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json'
        self.path = p
        d = json.loads(p.read_text(encoding='utf-8'))
        self.d, self.meta = d, d['meta']
        reg = json.loads((ROOT / f'public/data/races/{race}/gps_registration.json').read_text(encoding='utf-8'))
        off = np.asarray({int(l['lap']): l['offsetMeters'] for l in reg['laps']}[lap], float)
        self.offset = off
        t = np.asarray(d['t'], float)
        raw = to_local(d['lat'], d['lng'], course.origin) + off
        self.cx, self.cz = CubicSpline(t, raw[:, 0]), CubicSpline(t, raw[:, 1])
        self.t = np.arange(t[0], t[-1], dtt)
        self.xy = np.stack([self.cx(self.t), self.cz(self.t)], 1)
        v = np.stack([self.cx(self.t, 1), self.cz(self.t, 1)], 1)
        self.v = np.linalg.norm(v, axis=1)
        self.fwd = v / np.maximum(self.v, 1e-6)[:, None]
        self.right = np.stack([-self.fwd[:, 1], self.fwd[:, 0]], 1)
        s, q, idx = course.station(self.xy)
        # unwrap near the start/finish line so stations increase monotonically through the lap
        s = np.where((s > course.total - 600) & (self.t < 30), s - course.total, s)
        s = np.where((s < 600) & (self.t > self.t[-1] - 30), s + course.total, s)
        self.s, self.q = s, q
        self.delta = float(self.meta.get('first_sample_after_lap_start_seconds') or 0.0)
        self.speed_t = t
        self.speed_kmh = np.asarray(d['speed'], float)

    def t_at_station(self, s_target: float):
        """First time the (monotone-smoothed) station passes s_target (linear interpolation)."""
        s = np.maximum.accumulate(self.s)
        if s_target < s[0] or s_target > s[-1]:
            return None
        i = int(np.searchsorted(s, s_target))
        if i == 0:
            return float(self.t[0])
        s0, s1 = s[i - 1], s[i]
        return float(self.t[i - 1] + (self.t[i] - self.t[i - 1]) * (s_target - s0) / max(s1 - s0, 1e-9))

    def at(self, t: float):
        i = int(np.clip(np.searchsorted(self.t, t), 1, len(self.t) - 1))
        a = (t - self.t[i - 1]) / (self.t[i] - self.t[i - 1])
        lerp = lambda arr: arr[i - 1] + a * (arr[i] - arr[i - 1])
        return dict(xy=lerp(self.xy), fwd=lerp(self.fwd), right=lerp(self.right), v=float(lerp(self.v)),
                    s=float(lerp(self.s)), q=float(lerp(self.q)))


def timer_reads():
    return json.loads((ROOT / 'artifacts/video-position-gap-2026-10-02/video-timer-reads.json').read_text(encoding='utf-8'))['sessions']


def lap_start_pts(race: str, lap: int, meta: dict, reads=None) -> float:
    """PTS (s, ffmpeg base) of the true lap start (lap time T = 0). video PTS = lap_start_pts + T, T = t + delta."""
    c = (reads or timer_reads())[race]
    return ((wall(meta['lap_start_time']) - wall(c['referenceLapStart'])).total_seconds()
            + c['referencePtsSeconds'] - c['perLapCorrectionSeconds'][str(lap)])


def decode_frames(race: str, pts0: float, n: int, scale: int = 1):
    """Decode n consecutive frames starting at the first frame with PTS >= pts0 (ffmpeg accurate input seek).
    Returns (frames uint8 [n,H,W,3], pts array). Frame PTS are exact multiples of 1/30 s (CFR, start_time 0)."""
    vid = VIDEO[race]
    if not vid:
        raise RuntimeError('FUJI_WET_VIDEO / FUJI_DRY_VIDEO not set')
    k0 = int(math.ceil(pts0 * FPS - 1e-6))
    seek = k0 / FPS - 0.5 / FPS  # half a frame before the target frame -> ffmpeg returns frame k0 first
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{seek:.4f}', '-i', vid, '-map', '0:v:0', '-frames:v', str(n),
           '-vf', 'showinfo', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    r = subprocess.run(cmd, capture_output=True, check=True)
    raw = np.frombuffer(r.stdout, np.uint8)
    m = raw.size // (H_IMG * W_IMG * 3)
    fr = raw[:m * H_IMG * W_IMG * 3].reshape(m, H_IMG, W_IMG, 3)
    # showinfo pts_time is relative to the seek point (no -copyts); use it to confirm the first frame index
    import re
    rel_pts = [float(x) for x in re.findall(r'pts_time:([-0-9.]+)', r.stderr.decode('utf-8', 'replace'))]
    first = k0
    if rel_pts:
        first = int(round((seek + rel_pts[0]) * FPS))
    pts = (first + np.arange(m)) / FPS
    return fr, pts


# ----------------------------------------------------------------------------- camera ground model
def cam_profile():
    p = json.loads((ROOT / 'artifacts/video-cg-alignment-2026-10-03/camera-profile.json').read_text(encoding='utf-8'))
    return dict(f=p['f'], cx=p['cx'], cy=p['cy'], h=p['upMeters'], fwd=p['forwardMeters'], right=p['rightMeters'],
                pitch=math.radians(p['pitchDeg']), yaw=math.radians(p['yawDeg']), roll=math.radians(p['rollDeg']))


def R_cam(yaw, pitch, roll):
    """camera-from-car rotation, same convention as scripts/quality/apex_kpi/camproj.rot
    (car axes right, down, forward; pitch>0 tilts the camera down, yaw>0 to the right, roll>0 clockwise)."""
    cy_, sy = math.cos(yaw), math.sin(yaw)
    Ry = np.array([[cy_, 0, -sy], [0, 1, 0], [sy, 0, cy_]])
    cp, sp = math.cos(pitch), math.sin(pitch)
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    cr, sr = math.cos(roll), math.sin(roll)
    Rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return Rz @ Rx @ Ry


def pixel_to_ground(u, v, f, h, pitch, yaw=0.0, roll=0.0, cx=640.0, cy=360.0):
    """Flat-ground intersection of the pixel ray. Returns (lateral_right_m, forward_m) relative to the camera
    foot point, in the car frame (right, forward). NaN above the horizon."""
    u = np.asarray(u, float); v = np.asarray(v, float)
    ray_c = np.stack([(u - cx) / f, (v - cy) / f, np.ones_like(u)], -1)
    R = R_cam(yaw, pitch, roll)            # cam <- car
    ray_car = ray_c @ R                    # car-frame (right, down, forward) = R^T ray
    down = ray_car[..., 1]
    lam = np.where(down > 1e-6, h / np.maximum(down, 1e-9), np.nan)
    return ray_car[..., 0] * lam, ray_car[..., 2] * lam


def ground_to_pixel(x_right, z_fwd, f, h, pitch, yaw=0.0, roll=0.0, cx=640.0, cy=360.0):
    P = np.stack([np.asarray(x_right, float), np.full(np.shape(x_right), h, float), np.asarray(z_fwd, float)], -1)
    pc = P @ R_cam(yaw, pitch, roll).T
    return cx + f * pc[..., 0] / pc[..., 2], cy + f * pc[..., 1] / pc[..., 2]
