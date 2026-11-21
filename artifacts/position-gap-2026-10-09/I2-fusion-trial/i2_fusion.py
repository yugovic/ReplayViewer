"""I2 fusion trial: offline GNSS + IMU smoother (EKF forward + RTS backward) for the Fuji AiM laps.

State (viewer local frame: +x east, +z south; course chi = atan2(vz, vx), increasing in right turns):
    [px, pz, psi, v, b, beta, ex, ez], course chi = psi + beta
      px, pz   true antenna/vehicle-centre position (registration translation of the lap applied)
      psi      heading integrated from the scaled yaw gyro (random walk q_chi_deg)
      v        speed (m/s), integrated from the longitudinal accelerometer
      b        yaw-rate bias (rad/s, random walk)
      beta     side-slip-like course offset, Gauss-Markov (sigma_beta_deg = 0 disables it)
      ex, ez   slowly varying GPS position error (first-order Gauss-Markov; sigma_gm = 0 disables it)
Inputs (logger clock -> lap t with the lap's channel shift, as the shipped channels):
    YawRate (50 Hz) x session gyro scale, InlineAcc (50 Hz)
Measurements:
    GPS position (10 Hz, native XRK lat/lng, NOT the shipped 5-point moving average) + lap registration offset
    GPS speed (10 Hz), rear wheel-speed mean / session scale (20 Hz, skipped when the ECU value saturates)
Time base: lap t = (XRK GPS timecode - tc0) / 1000 (shipped lap JSON), IMU/ECU t = (logger ms - tc0)/1000 + shift.

All inputs except the XRK are repo files; the XRK channel dump lives in I2_WORK (local only).
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, asdict, replace
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts/quality/apex_kpi'))
import register_gps_to_track as base  # noqa: E402
import build_apex_kpi as K  # noqa: E402

G = 9.80665
DT = 0.02
WORK = Path(os.environ.get('I2_WORK', ''))
WS_SATURATION_KMH = 199.0


@dataclass(frozen=True)
class Params:
    sigma_white: float = 0.30      # m, GPS white position noise
    sigma_gm: float = 0.0          # m, GPS slow error (Gauss-Markov) std; 0 = white-only model
    tau_gm: float = 20.0           # s
    q_chi_deg: float = 1.0         # deg / sqrt(s), heading (psi) random walk: gyro noise / scale residual
    sigma_beta_deg: float = 0.0    # deg, side-slip-like course offset (Gauss-Markov); 0 = off
    tau_beta: float = 1.0          # s
    q_v: float = 0.5               # m/s^2 / sqrt(Hz), speed process noise (accel bias, grade)
    q_b_deg: float = 0.02          # deg/s / sqrt(s), course-rate bias random walk
    q_pos: float = 0.05            # m / sqrt(s), position random walk (lever arm / unmodelled)
    sigma_gps_speed: float = 0.15  # m/s
    sigma_ws: float = 0.30         # m/s
    use_ws: bool = True
    imu_dt: float = 0.0            # s, extra IMU delay on top of the lap channel shift
    rate_source: str = 'gyro'      # 'gyro' | 'accy' (course rate = a_y / v) | 'blend'


@dataclass
class Calib:
    gyro_scale: float
    gyro_bias_deg: float       # deg/s in course-rate units (after scaling), initial bias
    ws_scale: float            # raw rear wheel speed / true speed
    accy_scale: float = 1.0
    accy_bias_g: float = 0.0
    laps: tuple = ()


class Session:
    def __init__(self, race: str):
        self.race = race
        self.z = dict(np.load(WORK / f'xrk_{race}.npz'))
        self.lapmap = json.loads(str(self.z['lapMapping']))
        self.origin, self.geo = K.context()
        self.offsets, self.reg_path = K.registration_offsets(race)
        self.laps = K.race_laps(race)
        self.lapdoc = {lap: json.loads(K.lap_path(race, lap).read_text(encoding='utf-8')) for lap in self.laps}
        g = self.z
        self.gps_t = g['GPS Latitude_t']
        self.gps_xy = base.to_local(g['GPS Latitude'], g['GPS Longitude'], self.origin)
        self.gps_v = np.interp(self.gps_t, g['GPS Speed_t'], g['GPS Speed'])
        self.gps_acc = np.interp(self.gps_t, g['GPS_Position_Accuracy_t'], g['GPS_Position_Accuracy'])

    def shift(self, lap: int) -> float:
        cta = self.lapdoc[lap]['meta'].get('channel_time_alignment') or {}
        s = cta.get('shiftSeconds')
        return float(s) if isinstance(s, (int, float)) else 0.0

    def tc0(self, lap: int) -> float:
        return float(self.lapmap[str(lap)]['tc0Ms'])

    def lap_T(self, lap: int) -> float:
        return float(self.lapdoc[lap]['t'][-1])


def chan(S: Session, name: str, tc0: float, shift: float, tgrid: np.ndarray) -> np.ndarray:
    t = (S.z[name + '_t'] - tc0) / 1000.0 + shift
    return np.interp(tgrid, t, S.z[name])


# ------------------------------------------------------------------ calibration (session, from GPS)
def gps_course_rate(S: Session, lap: int, t50: np.ndarray):
    tc0 = S.tc0(lap)
    T = S.lap_T(lap)
    m = (S.gps_t >= tc0 - 3000) & (S.gps_t <= tc0 + 1000 * T + 3000)
    tg = (S.gps_t[m] - tc0) / 1000.0
    xy = S.gps_xy[m]
    cx, cz = CubicSpline(tg, xy[:, 0]), CubicSpline(tg, xy[:, 1])
    vx, vz, ax, az = cx(t50, 1), cz(t50, 1), cx(t50, 2), cz(t50, 2)
    sp = np.hypot(vx, vz)
    return (vx * az - vz * ax) / np.maximum(sp, 1.0) ** 2, sp


def calibrate(S: Session, laps, imu_dt: float = 0.0) -> Calib:
    """Gyro scale/bias (course-rate units) and rear wheel-speed scale by regression on the given laps."""
    Y, A, AY, ratios = [], [], [], []
    for lap in laps:
        T = S.lap_T(lap)
        t50 = np.arange(0.0, T, DT)
        cr, sp = gps_course_rate(S, lap, t50)
        sh, tc0 = S.shift(lap) + imu_dt, S.tc0(lap)
        w = np.radians(chan(S, 'YawRate', tc0, sh, t50))
        ay = chan(S, 'LateralAcc', tc0, sh, t50) * G
        gv = np.interp(t50, (S.gps_t - tc0) / 1000.0, S.gps_v)
        ws = (chan(S, 'ECU_WS_RL', tc0, sh, t50) + chan(S, 'ECU_WS_RR', tc0, sh, t50)) / 2
        ok = (ws < WS_SATURATION_KMH) & (gv > 10)
        ratios.append(ws[ok] / 3.6 / gv[ok])
        Y.append(cr)
        A.append(w)
        AY.append(np.stack([ay / np.maximum(gv, 5.0), np.ones_like(ay) / np.maximum(gv, 5.0)], 1))
    y, a = np.concatenate(Y), np.concatenate(A)
    cf = np.linalg.lstsq(np.stack([a, np.ones_like(a)], 1), y, rcond=None)[0]
    ay_cf = np.linalg.lstsq(np.concatenate(AY), y, rcond=None)[0]
    return Calib(gyro_scale=float(cf[0]), gyro_bias_deg=float(-np.degrees(cf[1])),
                 ws_scale=float(np.median(np.concatenate(ratios))), accy_scale=float(ay_cf[0]),
                 accy_bias_g=float(-ay_cf[1] / ay_cf[0] / G), laps=tuple(laps))


# ------------------------------------------------------------------ inputs
@dataclass
class LapInputs:
    lap: int
    t: np.ndarray            # grid (lap t), index i0 = lap t 0.0
    i0: int
    n_lap: int               # samples of the lap grid (np.arange(0, T, DT))
    rate: np.ndarray         # course-rate input (rad/s, scaled; bias handled by state b)
    ax: np.ndarray           # m/s^2
    ws: np.ndarray           # m/s or nan
    gk: np.ndarray           # GPS grid index
    gdt: np.ndarray          # GPS time - grid time (s)
    gxy: np.ndarray          # GPS position + registration offset
    gv: np.ndarray           # GPS speed
    gt: np.ndarray           # GPS lap t
    ay: np.ndarray           # lateral accelerometer (m/s^2), evaluation only
    steer: np.ndarray        # deg, evaluation only


def lap_inputs(S: Session, lap: int, calib: Calib, p: Params, pad: float = 10.0) -> LapInputs:
    T = S.lap_T(lap)
    n_lap = len(np.arange(0.0, T, DT))
    npad = int(round(pad / DT))
    idx = np.arange(-npad, n_lap + npad)
    t = idx * DT
    tc0, sh = S.tc0(lap), S.shift(lap) + p.imu_dt
    w = np.radians(chan(S, 'YawRate', tc0, sh, t)) * calib.gyro_scale
    ay = chan(S, 'LateralAcc', tc0, sh, t) * G
    ax = chan(S, 'InlineAcc', tc0, sh, t) * G
    steer = chan(S, 'ECU_STEER_ANG', tc0, sh, t)
    gt_all = (S.gps_t - tc0) / 1000.0
    m = (gt_all >= t[0]) & (gt_all <= t[-1])
    gt, gxy, gv = gt_all[m], S.gps_xy[m] + S.offsets.get(lap, np.zeros(2)), S.gps_v[m]
    gk = np.clip(np.round((gt - t[0]) / DT).astype(int), 0, len(t) - 1)
    gdt = gt - t[gk]
    # wheel speed at its own 20 Hz samples -> nearest grid step
    wt = (S.z['ECU_WS_RL_t'] - tc0) / 1000.0 + sh
    wraw = (S.z['ECU_WS_RL'] + np.interp(S.z['ECU_WS_RL_t'], S.z['ECU_WS_RR_t'], S.z['ECU_WS_RR'])) / 2
    mw = (wt >= t[0]) & (wt <= t[-1]) & (wraw < WS_SATURATION_KMH)
    ws = np.full(len(t), np.nan)
    ws[np.clip(np.round((wt[mw] - t[0]) / DT).astype(int), 0, len(t) - 1)] = wraw[mw] / 3.6 / calib.ws_scale
    if p.rate_source == 'gyro':
        rate = w
    else:
        gv_grid = np.interp(t, gt, gv)
        r_acc = calib.accy_scale * (ay - calib.accy_bias_g * G) / np.maximum(gv_grid, 5.0)
        rate = r_acc if p.rate_source == 'accy' else 0.5 * (w + r_acc)
    return LapInputs(lap, t, npad, n_lap, rate, ax, ws, gk, gdt, gxy, gv, gt, ay, steer)


# ------------------------------------------------------------------ EKF + RTS
NX = 8
IX, IZ, IPSI, IV, IB, IBETA, IEX, IEZ = range(NX)


def course(xs: np.ndarray) -> np.ndarray:
    return xs[..., IPSI] + xs[..., IBETA]


def gps_point(xs: np.ndarray, dtm) -> np.ndarray:
    """Predicted true position at the GPS fix time (grid state + v * dtm along the course)."""
    c = course(xs)
    return np.stack([xs[..., IX] + xs[..., IV] * np.cos(c) * dtm, xs[..., IZ] + xs[..., IV] * np.sin(c) * dtm], -1)


@dataclass
class Result:
    xs: np.ndarray       # smoothed states (n, NX)
    Ps_diag: np.ndarray  # smoothed variances (n, NX)
    xf: np.ndarray       # filtered states
    nll: float           # GPS position innovation negative log-likelihood (forward filter)
    n_pos: int
    innov: np.ndarray    # (m, 2) forward-filter GPS position innovations (used measurements)
    innov_k: np.ndarray  # grid indices of those


def run(L: LapInputs, p: Params, calib: Calib, gps_mask: np.ndarray | None = None, smooth: bool = True) -> Result:
    """State [px, pz, psi, v, b, beta, ex, ez]; course = psi + beta.
    psi: integrated scaled gyro (random walk q_chi_deg); beta: side-slip-like course offset, Gauss-Markov
    (sigma_beta_deg, tau_beta; 0 = off); ex/ez: GPS slow error, Gauss-Markov (sigma_gm, tau_gm; 0 = off)."""
    n, nx = len(L.t), NX
    use_gm = p.sigma_gm > 0
    use_beta = p.sigma_beta_deg > 0
    phi = math.exp(-DT / p.tau_gm) if use_gm else 0.0
    phib = math.exp(-DT / p.tau_beta) if use_beta else 0.0
    sb = math.radians(p.sigma_beta_deg)
    q = np.zeros(nx)
    q[IX] = q[IZ] = p.q_pos ** 2 * DT
    q[IPSI] = math.radians(p.q_chi_deg) ** 2 * DT
    q[IV] = p.q_v ** 2 * DT
    q[IB] = math.radians(p.q_b_deg) ** 2 * DT
    q[IBETA] = sb ** 2 * (1 - phib ** 2) if use_beta else 1e-14
    q[IEX] = q[IEZ] = (p.sigma_gm ** 2 * (1 - phi ** 2)) if use_gm else 1e-14
    Q = np.diag(q)
    Rp = np.eye(2) * p.sigma_white ** 2
    use = np.ones(len(L.gk), bool) if gps_mask is None else gps_mask
    by_k = {}
    for j in range(len(L.gk)):
        by_k.setdefault(int(L.gk[j]), []).append(j)
    d0 = L.gxy[1] - L.gxy[0]
    x = np.zeros(nx)
    x[IX], x[IZ] = L.gxy[0]
    x[IPSI] = math.atan2(d0[1], d0[0])
    x[IV] = L.gv[0]
    x[IB] = math.radians(calib.gyro_bias_deg)
    x[IX] -= x[IV] * L.gdt[0] * math.cos(x[IPSI])
    x[IZ] -= x[IV] * L.gdt[0] * math.sin(x[IPSI])
    P = np.diag([1.0, 1.0, math.radians(5) ** 2, 1.0, math.radians(0.3) ** 2,
                 sb ** 2 if use_beta else 1e-14,
                 p.sigma_gm ** 2 if use_gm else 1e-14, p.sigma_gm ** 2 if use_gm else 1e-14])
    XF = np.zeros((n, nx)); PF = np.zeros((n, nx, nx)); XP = np.zeros((n, nx)); PP = np.zeros((n, nx, nx))
    FF = np.zeros((n, nx, nx))
    nll, npos = 0.0, 0
    innov, innov_k = [], []
    I = np.eye(nx)
    for k in range(n):
        if k > 0:
            ch = x[IPSI] + x[IBETA]
            c, s = math.cos(ch), math.sin(ch)
            F = I.copy()
            F[IX, IPSI] = F[IX, IBETA] = -x[IV] * s * DT
            F[IX, IV] = c * DT
            F[IZ, IPSI] = F[IZ, IBETA] = x[IV] * c * DT
            F[IZ, IV] = s * DT
            F[IPSI, IB] = -DT
            F[IBETA, IBETA] = phib
            F[IEX, IEX] = F[IEZ, IEZ] = phi
            xn = x.copy()
            xn[IX] += x[IV] * c * DT
            xn[IZ] += x[IV] * s * DT
            xn[IPSI] += (L.rate[k - 1] - x[IB]) * DT
            xn[IV] += L.ax[k - 1] * DT
            xn[IBETA] *= phib
            xn[IEX] *= phi
            xn[IEZ] *= phi
            x = xn
            P = F @ P @ F.T + Q
            FF[k] = F
        XP[k] = x; PP[k] = P
        for j in by_k.get(k, ()):
            dtm = L.gdt[j]
            if use[j]:
                ch = x[IPSI] + x[IBETA]
                c, s = math.cos(ch), math.sin(ch)
                H = np.zeros((2, nx))
                H[0, IX] = H[1, IZ] = 1.0
                H[0, IPSI] = H[0, IBETA] = -x[IV] * s * dtm
                H[0, IV] = c * dtm
                H[1, IPSI] = H[1, IBETA] = x[IV] * c * dtm
                H[1, IV] = s * dtm
                if use_gm:
                    H[0, IEX] = H[1, IEZ] = 1.0
                h = np.array([x[IX] + x[IV] * c * dtm + x[IEX], x[IZ] + x[IV] * s * dtm + x[IEZ]])
                nu = L.gxy[j] - h
                Sm = H @ P @ H.T + Rp
                Si = np.linalg.inv(Sm)
                Kg = P @ H.T @ Si
                x = x + Kg @ nu
                P = (I - Kg @ H) @ P
                nll += 0.5 * (nu @ Si @ nu + math.log(np.linalg.det(Sm)) + 2 * math.log(2 * math.pi))
                npos += 1
                innov.append(nu)
                innov_k.append(k)
            Ss = P[IV, IV] + p.sigma_gps_speed ** 2
            Kg = P[:, IV] / Ss
            x = x + Kg * (L.gv[j] - x[IV])
            P = P - np.outer(Kg, P[IV, :])
        if p.use_ws and np.isfinite(L.ws[k]):
            Ss = P[IV, IV] + p.sigma_ws ** 2
            Kg = P[:, IV] / Ss
            x = x + Kg * (L.ws[k] - x[IV])
            P = P - np.outer(Kg, P[IV, :])
        P = 0.5 * (P + P.T)
        XF[k] = x; PF[k] = P
    innov = np.asarray(innov)
    innov_k = np.asarray(innov_k)
    if not smooth:
        return Result(XF, np.einsum('kii->ki', PF), XF, nll, npos, innov, innov_k)
    XS = XF.copy(); PS = PF.copy()
    for k in range(n - 2, -1, -1):
        C = PF[k] @ FF[k + 1].T @ np.linalg.inv(PP[k + 1])
        XS[k] = XF[k] + C @ (XS[k + 1] - XP[k + 1])
        PS[k] = PF[k] + C @ (PS[k + 1] - PP[k + 1]) @ C.T
    return Result(XS, np.einsum('kii->ki', PS), XF, nll, npos, innov, innov_k)


# ------------------------------------------------------------------ paths for the KPI
class FusedPaths:
    """Same interface as build_apex_kpi.LapPaths for kpi(): t50, W50, st50, lap_start_offset, pass_mask."""

    def __init__(self, S: Session, lap: int, L: LapInputs, res: Result, limits, heading: str = 'state'):
        sl = slice(L.i0, L.i0 + L.n_lap)
        self.t50 = L.t[sl]
        xy = res.xs[sl, 0:2]
        self.xy = xy
        if heading == 'state':
            chi = course(res.xs[sl])
            hv = np.stack([np.cos(chi), np.sin(chi)], 1)
        elif heading == 'body':
            chi = res.xs[sl, IPSI]
            hv = np.stack([np.cos(chi), np.sin(chi)], 1)
        else:  # derivative of the smoothed path (as LapPaths)
            hv = np.gradient(xy, DT, axis=0)
            hv /= np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
        self.hv = hv
        right = np.stack([-hv[:, 1], hv[:, 0]], 1)
        geo = S.geo
        hl = geo['wheelbaseMeters'] / 2
        hf = (geo['frontTrackMeters'] + geo['frontTyreWidthMeters']) / 2
        hr = (geo['rearTrackMeters'] + geo['rearTyreWidthMeters']) / 2
        self.W50 = np.stack([xy + hl * hv - hf * right, xy + hl * hv + hf * right,
                             xy - hl * hv - hr * right, xy - hl * hv + hr * right], 1)
        idx, _ = limits.lateral(xy)
        self.st50 = limits.dist[idx]
        self.lap_start_offset = float(S.lapdoc[lap]['meta'].get('first_sample_after_lap_start_seconds') or 0.0)

    pass_mask = K.LapPaths.pass_mask
    pass_window = K.LapPaths.pass_window


def params_dict(p: Params) -> dict:
    return asdict(p)


__all__ = ['Params', 'Calib', 'Session', 'calibrate', 'lap_inputs', 'run', 'FusedPaths', 'DT', 'G', 'replace']
