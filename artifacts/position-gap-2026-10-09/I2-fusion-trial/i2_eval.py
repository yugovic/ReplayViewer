"""I2 fusion trial, step 3: evaluate the GNSS+IMU smoother against GPS-independent evidence.

Variants (all with the SHIPPED per-lap registration translation of gps_registration.json):
  shipped        shipped lap JSON (5-point moving average of the native GPS on the 10 Hz grid), 50 Hz spline
                 -- exactly build_apex_kpi.LapPaths
  rawGps         native XRK GPS fixes, 50 Hz cubic spline (shows what the shipped moving average does)
  fusedBest      EKF+RTS, best model by GPS likelihood (i2_tune.py) -- the main candidate
  fusedWhite     best white-noise-only GPS error model by GPS likelihood
  fusedGM        best model with a Gauss-Markov (slow) GPS error state by GPS likelihood
  fusedAccy      as fusedBest but the course rate from the lateral accelerometer (a_y / v) instead of the gyro
  imuPhysical    pre-declared sensitivity: gyro heading noise 0.1 deg/sqrt(s), side-slip GM 0.5 deg / 2 s, GPS
                 white 0.3 m + GM 1.0 m / 30 s (a 'physical' prior that GPS has slow errors; worse likelihood)
  imuHeavy       pre-declared sensitivity: 0.05 deg/sqrt(s), GPS white 0.5 m + GM 1.5 m / 60 s (IMU-dominated)
  imuPhysicalAccy post-hoc diagnostic: imuPhysical with the accelerometer course rate
  lolo           fusedBest with hyper-parameters + calibration chosen without the evaluated lap

Evidence (none of it is used to choose a variant):
  (1) kerb-contact labels (public kerb_contacts.json, strong/silent) vs g = min(gBlockFront, gBlockRear) of
      build_apex_kpi.kpi() on kerb_zones.json; contradiction/AUC exactly as eval_kerb_registration.metrics()
  (2) 7/30 L2 3250-3900 m anomaly: lateral change vs shipped, GPS residual vs the fused path, k21/k24 labels,
      steer lag (NOT independent for fused paths: gyro and steer share the logger clock)
  (3) high-frequency lateral jitter, path lateral acceleration vs the lateral accelerometer (independent of the
      gyro-driven variants), heading vs the shipped path
  (4) leave-one-lap-out stability; GPS-outage bridging test (how far IMU dead reckoning holds)

Usage: python i2_eval.py [--race R ...] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import i2_fusion as F  # noqa: E402
K = F.K
base = F.base
import eval_kerb_registration as E  # noqa: E402
import register_gps_kerb_contact as R  # noqa: E402

SECTOR = (3250.0, 3900.0)
SG_WIN = 15          # 0.3 s Savitzky-Golay window at 50 Hz for path derivatives (all variants alike)


def r3(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 3)


def pct(v, ps=(50, 95, 100)):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return [r3(x) for x in np.percentile(v, ps)] if len(v) else None


class SplinePaths:
    """LapPaths-like for an arbitrary (t, xy) track (rawGps)."""

    def __init__(self, S, lap, t, xy, limits):
        T = S.lap_T(lap)
        self.t50 = np.arange(0.0, T, F.DT)
        cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
        p = np.stack([cx(self.t50), cz(self.t50)], 1)
        hv = np.stack([cx(self.t50, 1), cz(self.t50, 1)], 1)
        hv /= np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
        self.xy, self.hv = p, hv
        right = np.stack([-hv[:, 1], hv[:, 0]], 1)
        geo = S.geo
        hl = geo['wheelbaseMeters'] / 2
        hf = (geo['frontTrackMeters'] + geo['frontTyreWidthMeters']) / 2
        hr = (geo['rearTrackMeters'] + geo['rearTyreWidthMeters']) / 2
        self.W50 = np.stack([p + hl * hv - hf * right, p + hl * hv + hf * right,
                             p - hl * hv - hr * right, p - hl * hv + hr * right], 1)
        idx, _ = limits.lateral(p)
        self.st50 = limits.dist[idx]
        self.lap_start_offset = float(S.lapdoc[lap]['meta'].get('first_sample_after_lap_start_seconds') or 0.0)

    pass_mask = K.LapPaths.pass_mask
    pass_window = K.LapPaths.pass_window


def shipped_paths(S, lap, limits):
    lp = K.LapPaths(S.race, lap, limits, S.geo, S.origin, S.offsets.get(lap, (0.0, 0.0)))
    d = lp.data
    t = np.asarray(d['t'], float)
    raw = base.to_local(d['lat'], d['lng'], S.origin) + S.offsets.get(lap, np.zeros(2))
    cx, cz = CubicSpline(t, raw[:, 0]), CubicSpline(t, raw[:, 1])
    lp.xy = np.stack([cx(lp.t50), cz(lp.t50)], 1)
    hv = np.stack([cx(lp.t50, 1), cz(lp.t50, 1)], 1)
    lp.hv = hv / np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
    return lp


def raw_paths(S, lap, limits):
    tc0, T = S.tc0(lap), S.lap_T(lap)
    m = (S.gps_t >= tc0 - 3000) & (S.gps_t <= tc0 + 1000 * T + 3000)
    return SplinePaths(S, lap, (S.gps_t[m] - tc0) / 1000.0, S.gps_xy[m] + S.offsets.get(lap, np.zeros(2)), limits)


# ------------------------------------------------------------------ (1) kerb labels
def labels_for(race, zones):
    contacts = json.loads((K.ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
    anomalies = R.KNOWN_GPS_ANOMALIES.get(race, [])
    out = []
    for lap, rows in contacts['laps'].items():
        for r in rows:
            if r['class'] in ('strong', 'silent') and r['kerb'] in zones:
                out.append(dict(lap=int(lap), kerb=r['kerb'], cls=r['class'],
                                anomaly=R.anomaly_for(int(lap), zones[r['kerb']], anomalies) is not None))
    return out


def kpi_rows(paths_by_lap, zones, limits):
    rows = {}
    for lap, lp in paths_by_lap.items():
        for kid in K.INSIDE:
            if kid not in zones:
                continue
            k = K.kpi(lp, zones[kid], limits)
            if k is None:
                continue
            gs = [v for v in (k['gBlockFront'], k['gBlockRear']) if v is not None]
            rows[(lap, kid)] = dict(g=min(gs) if gs else None, gFront=k['gBlockFront'], gRear=k['gBlockRear'],
                                    apexStation=k['apexStation'])
    return rows


def label_metrics(rows, labels):
    lab_rows = []
    for lab in labels:
        r = rows.get((lab['lap'], lab['kerb']))
        g = None if r is None or r['g'] is None else round(float(r['g']), 3)
        lab_rows.append(dict(lab, g=g))
    return E.both(lab_rows), lab_rows


# ------------------------------------------------------------------ (3) path quality
def path_kinematics(xy):
    v = savgol_filter(xy, SG_WIN, 3, deriv=1, delta=F.DT, axis=0)
    a = savgol_filter(xy, SG_WIN, 3, deriv=2, delta=F.DT, axis=0)
    sp = np.maximum(np.linalg.norm(v, axis=1), 1.0)
    alat = (v[:, 0] * a[:, 1] - v[:, 1] * a[:, 0]) / sp      # + = right turn (chi increasing)
    chi = np.unwrap(np.arctan2(v[:, 1], v[:, 0]))
    return alat, chi, sp


def path_quality(lp, ref_lp, limits, ay_imu, steer):
    _, q = limits.lateral(lp.xy)
    _, q_ref = limits.lateral(ref_lp.xy)
    hp = q - uniform_filter1d(q, 51)               # > ~1 Hz lateral content
    hp2 = q - uniform_filter1d(q, 13)              # > ~4 Hz
    alat, chi, sp = path_kinematics(lp.xy)
    alat_ref, chi_ref, _ = path_kinematics(ref_lp.xy)
    ay = savgol_filter(ay_imu, SG_WIN, 3)
    sgn = np.sign(np.corrcoef(alat_ref, ay)[0, 1]) or 1.0
    ay = sgn * ay
    hd = np.degrees(np.angle(np.exp(1j * (np.arctan2(lp.hv[:, 1], lp.hv[:, 0]) - np.arctan2(ref_lp.hv[:, 1], ref_lp.hv[:, 0])))))
    hd_self = np.degrees(np.angle(np.exp(1j * (np.arctan2(lp.hv[:, 1], lp.hv[:, 0]) - chi))))
    return dict(
        lateralHpRmsAbove1HzM=r3(np.sqrt(np.mean(hp[50:-50] ** 2))),
        lateralHpRmsAbove4HzM=r3(np.sqrt(np.mean(hp2[50:-50] ** 2))),
        alatVsAccelerometerRmsMs2=r3(np.sqrt(np.mean((alat - ay)[50:-50] ** 2))),
        alatVsAccelerometerCorr=r3(np.corrcoef(alat[50:-50], ay[50:-50])[0, 1]),
        alatHpRmsAbove1HzMs2=r3(np.sqrt(np.mean((alat - uniform_filter1d(alat, 51))[50:-50] ** 2))),
        headingVsShippedDeg=pct(np.abs(hd)),
        tyreHeadingVsOwnPathCourseDeg=pct(np.abs(hd_self)),
        lateralVsShippedM=pct(np.abs(q - q_ref)),
        meanDisplacementVsShippedM=[r3(x) for x in (lp.xy - ref_lp.xy).mean(0)],
        q=q, q_ref=q_ref, alat=alat, ay=ay, sp=sp)


def best_lag(t, ref, sig, mask, span=0.6, step=0.02):
    tt = t[mask]
    best = (None, -2.0)
    for s_ in np.arange(-span, span + step / 2, step):
        y = np.interp(tt + s_, t, sig)
        c = np.corrcoef(ref[mask], y)[0, 1]
        if np.isfinite(c) and c > best[1]:
            best = (round(float(s_), 2), round(float(c), 3))
    return best


# ------------------------------------------------------------------ fused runs
def fused(S, lap, cal, p, limits, mask=None):
    L = F.lap_inputs(S, lap, cal, p)
    res = F.run(L, p, cal, gps_mask=mask(L) if mask else None)
    return L, res, F.FusedPaths(S, lap, L, res, limits)


def gps_residuals(S, lap, L, res, limits):
    """Native GPS (+offset) minus the fused smoothed position at the GPS times; lateral = track normal."""
    k, dtm = L.gk, L.gdt
    x = res.xs[k]
    pred = F.gps_point(x, dtm)
    r = L.gxy - pred
    idx, _ = limits.lateral(pred)
    lat = np.einsum('ij,ij->i', r, limits.normal[idx])
    st = limits.dist[idx]
    inlap = (L.gt >= 0) & (L.gt <= S.lap_T(lap))
    gm_lat = np.einsum('ij,ij->i', x[:, F.IEX:F.IEZ + 1], limits.normal[idx])
    return dict(t=L.gt[inlap], st=st[inlap], lat=lat[inlap], gmLat=gm_lat[inlap], r=r[inlap])


def outage_test(S, cal, p, limits, lengths=(1.0, 2.0, 4.0, 8.0)):
    """GPS positions withheld in windows of length Lw (speed/IMU kept). Error at the window centre of the
    bridged smoothed path vs the withheld GPS fix and vs the full-data smoothed path (lateral = track normal)."""
    out = {}
    for Lw in lengths:
        spacing = max(6.0, 3 * Lw)
        errs_gps, errs_full, along_full = [], [], []
        for lap in S.laps:
            Lf, rf, _ = fused(S, lap, cal, p, limits)
            T = S.lap_T(lap)
            for phase in (0.0, 0.5):
                centres = np.arange(spacing * (0.5 + phase), T - Lw, spacing)
                def mask(L, centres=centres):
                    m = np.ones(len(L.gt), bool)
                    for c in centres:
                        m &= ~((L.gt > c - Lw / 2) & (L.gt < c + Lw / 2))
                    return m
                L, r, _ = fused(S, lap, cal, p, limits, mask)
                for c in centres:
                    j = int(np.argmin(np.abs(L.gt - c)))
                    if abs(L.gt[j] - c) > 0.06:
                        continue
                    kk, dtm = L.gk[j], L.gdt[j]
                    pb = F.gps_point(r.xs[kk], dtm)
                    pf = F.gps_point(rf.xs[kk], dtm)
                    idx, _ = limits.lateral(pb[None])
                    nrm = limits.normal[idx[0]]
                    tan = np.array([nrm[1], -nrm[0]])
                    errs_gps.append(float(np.dot(L.gxy[j] - pb, nrm)))
                    errs_full.append(float(np.dot(pf - pb, nrm)))
                    along_full.append(float(np.dot(pf - pb, tan)))
        eg, ef, af = np.abs(errs_gps), np.abs(errs_full), np.abs(along_full)
        out[f'{Lw:g}s'] = dict(n=len(eg), lateralVsWithheldGpsM=pct(eg, (50, 90, 100)),
                               lateralVsFullSmoothedM=pct(ef, (50, 90, 100)),
                               alongVsFullSmoothedM=pct(af, (50, 90, 100)))
    return out


def sector_analysis(S, variants_paths, residuals, limits, quality, Ls):
    """7/30 L2 3250-3900 m: per lap, per variant."""
    out = {'definition': ('dLat = variant minus shipped lateral position (track normal, + = right of travel) in the '
                          'sector; gpsResidualLat = native GPS (+offset) minus the fused smoothed path at the fixes; '
                          'steer/accy lag: s maximising corr(path a_lat(t), channel(t+s)) (sign-corrected, +-1 s; + = channel later = GPS path leads); '
                          'NOT independent for fused paths (their course rate comes from the gyro, which shares the logger '
                          'clock with steer and the accelerometer)'),
           'laps': {}}
    for lap in S.laps:
        lp0 = variants_paths['shipped'][lap]
        st = lp0.st50
        sec = (st >= SECTOR[0]) & (st <= SECTOR[1])
        e = {}
        for name, paths in variants_paths.items():
            lp = paths[lap]
            qd = quality[name][lap]
            dq = qd['q'] - qd['q_ref']
            n = len(lp.t50)
            steer = Ls[lap].steer[Ls[lap].i0:Ls[lap].i0 + n]
            steer = steer * (np.sign(np.corrcoef(qd['alat'], steer)[0, 1]) or 1.0)
            ay = qd['ay']
            sl, rl = best_lag(lp.t50, qd['alat'], steer, sec, span=1.0), best_lag(lp.t50, qd['alat'], steer, ~sec, span=1.0)
            al, arl = best_lag(lp.t50, qd['alat'], ay, sec, span=1.0), best_lag(lp.t50, qd['alat'], ay, ~sec, span=1.0)
            row = dict(dLatMeanM=r3(dq[sec].mean()), dLatMaxAbsM=r3(np.abs(dq[sec]).max()),
                       dLatRestP95AbsM=r3(np.percentile(np.abs(dq[~sec]), 95)),
                       steerLagSectorS=sl[0], steerLagRestS=rl[0], steerLagSectorMinusRestS=None if sl[0] is None else round(sl[0] - rl[0], 2),
                       accyLagSectorS=al[0], accyLagRestS=arl[0], accyLagSectorMinusRestS=None if al[0] is None else round(al[0] - arl[0], 2),
                       accyCorrSector=al[1])
            if name in residuals:
                rr = residuals[name][lap]
                ms = (rr['st'] >= SECTOR[0]) & (rr['st'] <= SECTOR[1])
                row.update(gpsResidualLatRmsSectorM=r3(np.sqrt(np.mean(rr['lat'][ms] ** 2))),
                           gpsResidualLatRmsRestM=r3(np.sqrt(np.mean(rr['lat'][~ms] ** 2))),
                           gpsResidualLatMaxAbsSectorM=r3(np.abs(rr['lat'][ms]).max()),
                           gmErrorLatMaxAbsSectorM=r3(np.abs(rr['gmLat'][ms]).max()),
                           gmErrorLatMaxAbsRestM=r3(np.abs(rr['gmLat'][~ms]).max()))
            e[name] = row
        out['laps'][str(lap)] = e
    return out


def run_race(race, out_dir, tune_dir, outage=True):
    t0 = time.time()
    S = F.Session(race)
    zones = K.load_zones()
    limits = K.zoned_limits(base.load_limits(K.TRACK_DIR, 0.0), zones)
    tune = json.loads((tune_dir / f'tune_{race}.json').read_text(encoding='utf-8'))
    p_best = F.Params(**tune['best']['params'])
    p_white = F.Params(**tune['bestWhiteOnly']['params'])
    p_gm = F.Params(**tune['bestGM']['params'])
    cal = F.calibrate(S, S.laps, imu_dt=p_best.imu_dt)
    cal_w = F.calibrate(S, S.laps, imu_dt=p_white.imu_dt)
    cal_g = F.calibrate(S, S.laps, imu_dt=p_gm.imu_dt)
    # pre-declared IMU-trusting sensitivity variants (fixed before any label evaluation; NOT likelihood-optimal)
    p_phys = replace(p_best, q_chi_deg=0.1, sigma_beta_deg=0.5, tau_beta=2.0, sigma_white=0.3, sigma_gm=1.0, tau_gm=30.0)
    p_heavy = replace(p_best, q_chi_deg=0.05, sigma_beta_deg=0.5, tau_beta=2.0, sigma_white=0.5, sigma_gm=1.5, tau_gm=60.0)
    variants = {'fusedBest': (p_best, cal)}
    if p_white != p_best:
        variants['fusedWhite'] = (p_white, cal_w)
    variants.update({'fusedGM': (p_gm, cal_g), 'fusedAccy': (replace(p_best, rate_source='accy'), cal),
                     'imuPhysical': (p_phys, cal), 'imuHeavy': (p_heavy, cal),
                     # post-hoc diagnostic (added after the label results): same prior, accelerometer course rate
                     'imuPhysicalAccy': (replace(p_phys, rate_source='accy'), cal)})
    paths = {'shipped': {}, 'rawGps': {}}
    residuals, Ls, results = {}, {}, {}
    for lap in S.laps:
        paths['shipped'][lap] = shipped_paths(S, lap, limits)
        paths['rawGps'][lap] = raw_paths(S, lap, limits)
    for name, (p, c) in variants.items():
        paths[name], residuals[name], results[name] = {}, {}, {}
        for lap in S.laps:
            L, res, fp = fused(S, lap, c, p, limits)
            paths[name][lap] = fp
            residuals[name][lap] = gps_residuals(S, lap, L, res, limits)
            results[name][lap] = res
            if name == 'fusedBest':
                Ls[lap] = L
    # LOLO
    paths['lolo'], lolo_diff = {}, {}
    for lap in S.laps:
        pl = F.Params(**tune['lolo'][str(lap)]['params'])
        cl = F.Calib(**{**tune['calibrationLolo'][str(lap)], 'laps': tuple(tune['calibrationLolo'][str(lap)]['laps'])})
        L, res, fp = fused(S, lap, cl, pl, limits)
        paths['lolo'][lap] = fp
        d = np.linalg.norm(fp.xy - paths['fusedBest'][lap].xy, axis=1)
        lolo_diff[str(lap)] = dict(paramsSameAsFull=pl == p_best, displacementVsFullM=pct(d), params=asdict(pl),
                                   calibration=dict(gyroScale=round(cl.gyro_scale, 4), gyroBiasDeg=round(cl.gyro_bias_deg, 4),
                                                    wsScale=round(cl.ws_scale, 4)))
    labels = labels_for(race, zones)
    kerb = {}
    rows_all = {}
    for name, pb in paths.items():
        rows = kpi_rows(pb, zones, limits)
        m, lab_rows = label_metrics(rows, labels)
        kerb[name] = m
        rows_all[name] = lab_rows
    # bootstrap vs shipped (cluster by kerb), same helper as the published evaluation
    boot = {name: E.bootstrap(rows_all[name], rows_all['shipped'], B=4000)
            for name in paths if name != 'shipped'}
    # per-label g table (shipped vs variants) for changed labels
    g_table = []
    for i, lab in enumerate(labels):
        row = {'lap': lab['lap'], 'kerb': lab['kerb'], 'cls': lab['cls'], 'anomaly': lab['anomaly']}
        for name in paths:
            row[name] = rows_all[name][i]['g']
        g_table.append(row)
    # quality
    quality = {}
    qsum = {}
    for name, pb in paths.items():
        quality[name] = {}
        for lap in S.laps:
            L = Ls[lap]
            sl = slice(L.i0, L.i0 + L.n_lap)
            n = len(pb[lap].t50)
            quality[name][lap] = path_quality(pb[lap], paths['shipped'][lap], limits, L.ay[sl][:n], L.steer[sl][:n])
        keys = [k for k, v in quality[name][S.laps[0]].items() if not isinstance(v, np.ndarray)]
        qsum[name] = {'perLap': {str(lap): {k: quality[name][lap][k] for k in keys} for lap in S.laps}}
        for k in ('lateralHpRmsAbove1HzM', 'lateralHpRmsAbove4HzM', 'alatVsAccelerometerRmsMs2', 'alatVsAccelerometerCorr', 'alatHpRmsAbove1HzMs2'):
            qsum[name][k + 'Median'] = r3(np.median([quality[name][lap][k] for lap in S.laps]))
        allq = np.concatenate([np.abs(quality[name][lap]['q'] - quality[name][lap]['q_ref']) for lap in S.laps])
        qsum[name]['lateralVsShippedAllLapsM'] = pct(allq, (50, 95, 99, 100))
    sector = sector_analysis(S, paths, residuals, limits, quality, Ls) if race == 'fuji_aim_2020_07_30' else None
    # GPS residual stats overall (fused)
    resid = {name: {str(lap): dict(latRmsM=r3(np.sqrt(np.mean(rr['lat'] ** 2))), latP95AbsM=r3(np.percentile(np.abs(rr['lat']), 95)),
                                   gmLatMaxAbsM=r3(np.abs(rr['gmLat']).max()))
                    for lap, rr in residuals[name].items()} for name in residuals}
    outage_res = None
    if outage:
        outage_res = {'fusedBest': outage_test(S, cal, p_best, limits),
                      'imuPhysical': outage_test(S, cal, p_phys, limits),
                      'imuPhysicalAccy': outage_test(S, cal, replace(p_phys, rate_source='accy'), limits)}
    bias = {name: {str(lap): r3(np.degrees(np.median(results[name][lap].xs[:, F.IB]))) for lap in S.laps} for name in results}
    doc = dict(
        race=race, tool='artifacts/position-gap-2026-10-09/I2-fusion-trial/i2_eval.py', surveyTruth=False,
        inputs=dict(registration=K.rel(S.reg_path), kerbZones=K.rel(K.ZONES_PATH),
                    kerbContacts=f'public/data/races/{race}/kerb_contacts.json',
                    xrk='native XRK via env FUJI_WET_XRK / FUJI_DRY_XRK (local only; channel dump in I2_WORK)'),
        variantNllPerFix={name: r3(np.mean([results[name][lap].nll / results[name][lap].n_pos for lap in S.laps])) for name in results},
        params={'fusedBest': asdict(p_best), 'fusedGM': asdict(p_gm), 'fusedWhite': asdict(p_white),
                'imuPhysical': asdict(p_phys), 'imuHeavy': asdict(p_heavy),
                'likelihoodMeanNllPerFix': {'fusedBest': tune['best']['meanNll'], 'fusedWhite': tune['bestWhiteOnly']['meanNll'],
                                            'fusedGM': tune['bestGM']['meanNll']}},
        calibration=asdict(cal), courseRateBiasMedianDeg=bias,
        kerbLabels=kerb, kerbBootstrapVsShipped=boot, labelGTable=g_table,
        pathQuality=qsum, gpsResidualVsFused=resid, l2Sector=sector, lolo=lolo_diff, outageTest=outage_res,
        runtimeSeconds=round(time.time() - t0, 1))
    (out_dir / f'eval_{race}.json').write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
    # chart data (numbers only)
    chart = {'laps': {}}
    for lap in S.laps:
        lp0 = paths['shipped'][lap]
        item = {'st': np.round(lp0.st50[::5], 1).tolist()}
        for name in ('rawGps', 'fusedBest', 'fusedGM', 'imuPhysical', 'imuHeavy', 'fusedAccy'):
            qd = quality[name][lap]
            item['dLat_' + name] = np.round((qd['q'] - qd['q_ref'])[::5], 3).tolist()
        for name in ('fusedBest', 'imuPhysical', 'imuHeavy'):
            rr = residuals[name][lap]
            item['res_' + name] = {'st': np.round(rr['st'], 1).tolist(), 'lat': np.round(rr['lat'], 3).tolist(),
                                   'gm': np.round(rr['gmLat'], 3).tolist()}
        item['alat_shipped'] = np.round(quality['shipped'][lap]['alat'][::5], 3).tolist()
        item['alat_fusedBest'] = np.round(quality['fusedBest'][lap]['alat'][::5], 3).tolist()
        item['ay'] = np.round(quality['shipped'][lap]['ay'][::5], 3).tolist()
        item['t'] = np.round(lp0.t50[::5], 2).tolist()
        chart['laps'][str(lap)] = item
    (out_dir / f'chartdata_{race}.json').write_text(json.dumps(chart), encoding='utf-8')
    print(race, 'done', doc['runtimeSeconds'], 's')
    for name, m in kerb.items():
        a = m['allLabels']
        print(f"  {name:11s} contra {a['contradictionCount']:2d} auc {a['auc']} n {a['n']} | noAnom contra {m['withoutAnomalyLabels']['contradictionCount']} auc {m['withoutAnomalyLabels']['auc']}  {a['contradictions']}")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--race', action='append')
    ap.add_argument('--out', type=Path, default=HERE / 'out')
    ap.add_argument('--no-outage', action='store_true')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for race in a.race or ['fuji_aim_2020_07_30', 'fuji_aim_01']:
        run_race(race, a.out, a.out, outage=not a.no_outage)
    return 0


if __name__ == '__main__':
    sys.exit(main())
