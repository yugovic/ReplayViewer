"""T3 camera ground model parameterised by the image vanishing point.

Pinhole, no lens distortion (not calibrated; a limitation). Car frame (right, down, forward); camera at height h above
the local road plane (flat-road assumption), 0.55 m behind the car reference point and 0 m lateral (camera-profile.json
values; the lateral mount offset is unknown and cancels in between-lap differences).
For a chosen focal length f the pitch and yaw are solved so that the car forward direction projects onto the measured
vanishing point (u_vp, v_vp); roll is kept from camera-profile.json. Lateral ground distances then depend on h and only
weakly on f (through cos(pitch)); forward distances scale with f*h.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import fsolve

import common as C


@dataclass
class Cam:
    f: float
    h: float
    v_vp: float
    u_vp: float
    roll: float           # rad, camproj convention (Rz after Rx after Ry)
    fwd: float = -0.55    # camera position along the car forward axis relative to the car reference point (m)
    right: float = 0.0
    cx: float = 640.0
    cy: float = 360.0
    pitch: float = 0.0
    yaw: float = 0.0

    def __post_init__(self):
        def res(p):
            pitch, yaw = p
            pc = C.R_cam(yaw, pitch, self.roll) @ np.array([0.0, 0.0, 1.0])
            return [self.cx + self.f * pc[0] / pc[2] - self.u_vp, self.cy + self.f * pc[1] / pc[2] - self.v_vp]
        p0 = [math.atan((self.cy - self.v_vp) / self.f), math.atan((self.u_vp - self.cx) / self.f)]
        self.pitch, self.yaw = (float(x) for x in fsolve(res, p0))
        self.R = C.R_cam(self.yaw, self.pitch, self.roll)

    def label(self):
        return f'f{self.f:.0f}_h{self.h:.2f}'

    def project(self, X, D):
        """car-frame ground point (X right, D forward of the CAMERA foot point) -> pixel (u, v), depth z."""
        X = np.asarray(X, float); D = np.asarray(D, float)
        P = np.stack([X, np.full(X.shape, self.h), D], -1)
        pc = P @ self.R.T
        z = pc[..., 2]
        return self.cx + self.f * pc[..., 0] / z, self.cy + self.f * pc[..., 1] / z, z

    def ground(self, u, v):
        """pixel -> car-frame ground point (X right, D forward of the camera foot point); NaN above the horizon."""
        u = np.asarray(u, float); v = np.asarray(v, float)
        ray = np.stack([(u - self.cx) / self.f, (v - self.cy) / self.f, np.ones(np.broadcast(u, v).shape)], -1)
        rc = ray @ self.R      # = R^T ray
        lam = np.where(rc[..., 1] > 1e-6, self.h / np.maximum(rc[..., 1], 1e-9), np.nan)
        return rc[..., 0] * lam, rc[..., 2] * lam

    def px_per_m_lateral(self, v):
        return (np.asarray(v, float) - self.v_vp) * math.cos(self.pitch) / self.h


def profile_cam():
    p = C.cam_profile()
    c = Cam(f=p['f'], h=p['h'], v_vp=0, u_vp=0, roll=p['roll'])
    c.pitch, c.yaw = p['pitch'], p['yaw']
    c.R = C.R_cam(c.yaw, c.pitch, c.roll)
    pc = c.R @ np.array([0, 0, 1.0])
    c.u_vp, c.v_vp = c.cx + c.f * pc[0] / pc[2], c.cy + c.f * pc[1] / pc[2]
    return c
