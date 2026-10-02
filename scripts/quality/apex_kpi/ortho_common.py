"""Shared, read-only access to the Fuji orthophoto and CG study geometry for kerb-zone work.

Source imagery: VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm (CC BY 4.0, Shizuoka Prefecture),
pipeline/cache/shizuoka_ortho/native_mosaic.png. Never Google tiles.
Pixel mapping follows scripts/quality/build_fuji_cg.py: the bbox describes outer pixel edges (Web Mercator
rows), bilinear array indices subtract 0.5 on both axes. Track local frame: x east, z south (metres).
"""
from __future__ import annotations

import json
import math

import numpy as np
import cv2
from PIL import Image
from scipy.ndimage import map_coordinates, gaussian_filter1d

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from paths import ROOT  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
SOURCE = ROOT / 'pipeline/cache/shizuoka_ortho/native_mosaic.png'
META = json.loads(SOURCE.with_name('native_mosaic_meta.json').read_text())
TRACK = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text())
GEO_PATH = ROOT / 'public/data/tracks/fuji/cg_study/geometry.json'
GEO = json.loads(GEO_PATH.read_text())
ROAD = np.array(GEO['road'], float)          # station, cx, cz, nx, nz, offL, offR, lx, lz, rx, rz
STEP = float(GEO['stationStep'])
KERBS = GEO['curbs']                           # rows: station, inX, inZ, outX, outZ, inOff, outOff, cgRed, observed
INSIDE = [1, 3, 7, 9, 10, 13, 15, 18, 19, 20, 21, 23, 24]

_B = META['bbox']; _O = TRACK['origin']
_NORTH = np.arcsinh(np.tan(np.radians(_B['maxLat'])))
_SOUTH = np.arcsinh(np.tan(np.radians(_B['minLat'])))
_RGB = None


def rgb() -> np.ndarray:
    global _RGB
    if _RGB is None:
        print('loading ortho...', flush=True)
        _RGB = np.array(Image.open(SOURCE).convert('RGB'))
    return _RGB


def pixel(x, z):
    """Track-local XZ (m) -> raster edge coordinates (px, py)."""
    x = np.asarray(x, float); z = np.asarray(z, float)
    W, H = META['imageWidth'], META['imageHeight']
    lat = _O['lat'] - z / 111320
    lng = _O['lng'] + x / (111320 * math.cos(math.radians(_O['lat'])))
    return ((lng - _B['minLng']) / (_B['maxLng'] - _B['minLng']) * W,
            (_NORTH - np.arcsinh(np.tan(np.radians(lat)))) / (_NORTH - _SOUTH) * H)


def metres_per_pixel() -> float:
    W = META['imageWidth']
    return (_B['maxLng'] - _B['minLng']) / W * 111320 * math.cos(math.radians(_O['lat']))


def road_row(s: float) -> np.ndarray:
    return ROAD[int(round(s / STEP))]


class Patch:
    """Local crop of the ortho in Lab (float, L 0-100) with bilinear sampling in world XZ."""

    def __init__(self, cx: float, cz: float, half: float = 70.):
        img = rgb(); H, W = img.shape[:2]
        px, py = pixel([cx - half, cx + half], [cz + half, cz - half])
        self.x0 = int(max(0, math.floor(min(px)) - 4)); self.x1 = int(min(W, math.ceil(max(px)) + 4))
        self.y0 = int(max(0, math.floor(min(py)) - 4)); self.y1 = int(min(H, math.ceil(max(py)) + 4))
        self.rgb = img[self.y0:self.y1, self.x0:self.x1]
        self.lab = cv2.cvtColor(self.rgb.astype(np.float32) / 255., cv2.COLOR_RGB2LAB)

    def sample(self, x, z, arr=None):
        arr = self.lab if arr is None else arr
        px, py = pixel(x, z)
        px = px - .5 - self.x0; py = py - .5 - self.y0
        return np.stack([map_coordinates(arr[..., c], [py, px], order=1, mode='nearest')
                         for c in range(arr.shape[-1])], axis=-1)


def patch_for(points: np.ndarray, margin: float = 12.) -> Patch:
    p = np.asarray(points, float)
    cx, cz = (p[:, 0].min() + p[:, 0].max()) / 2, (p[:, 1].min() + p[:, 1].max()) / 2
    half = max(p[:, 0].max() - p[:, 0].min(), p[:, 1].max() - p[:, 1].min()) / 2 + margin
    return Patch(cx, cz, half)


def smooth_polyline(xy: np.ndarray, sigma_pts: float) -> np.ndarray:
    """Gaussian smoothing of a polyline with point-reflection padding, so the end points do not shrink
    back along the line (mode='nearest' would pull them inward by ~sigma)."""
    xy = np.asarray(xy, float)
    if sigma_pts <= 0 or len(xy) < 3:
        return xy.copy()
    pad = int(min(len(xy) - 1, 4 * sigma_pts + 1))
    head = 2 * xy[0] - xy[pad:0:-1]
    tail = 2 * xy[-1] - xy[-2:-pad - 2:-1]
    p = np.vstack([head, xy, tail])
    s = np.column_stack([gaussian_filter1d(p[:, 0], sigma_pts, mode='nearest'),
                         gaussian_filter1d(p[:, 1], sigma_pts, mode='nearest')])
    return s[pad:pad + len(xy)]


def unit_normals(xy: np.ndarray, sigma_pts: float = 4.) -> np.ndarray:
    """Left-hand normals (-tz, tx) of a smoothed polyline (unit)."""
    s = smooth_polyline(xy, sigma_pts)
    t = np.gradient(s, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    return np.column_stack([-t[:, 1], t[:, 0]])


def inward_normals(xy: np.ndarray, stations: np.ndarray, side: str, sigma_pts: float = 4.) -> np.ndarray:
    """Unit normals of the polyline oriented toward the road centre (checked against the road normal)."""
    n = unit_normals(xy, sigma_pts)
    ref = np.array([road_row(s)[3:5] for s in stations], float)
    ref /= np.maximum(np.linalg.norm(ref, axis=1), 1e-9)[:, None]
    ref = ref if side == 'left' else -ref       # left edge sits at negative road offset
    flip = np.sign(np.sum(n * ref, axis=1)); flip[flip == 0] = 1
    return n * flip[:, None]
