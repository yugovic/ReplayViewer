"""Charts for the JOIN step (numbers only; no video or audio pixels). Needs matplotlib (pipeline/.venv-sr).
Reads event_table.json + summary.json next to this file and writes join_*.png."""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
E = json.loads((HERE / 'event_table.json').read_text(encoding='utf-8'))
S = json.loads((HERE / 'summary.json').read_text(encoding='utf-8'))
SESS = ['fuji_aim_01', 'fuji_aim_2020_07_30']
LAB = {'fuji_aim_01': '7/29 wet', 'fuji_aim_2020_07_30': '7/30 dry'}
COL = {'fuji_aim_01': '#2a78d6', 'fuji_aim_2020_07_30': '#eb6834'}
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
plt.rcParams.update({'font.size': 9, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2, 'xtick.color': INK2, 'ytick.color': INK2,
                     'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6, 'axes.facecolor': SURF, 'figure.facecolor': SURF,
                     'axes.spines.top': False, 'axes.spines.right': False, 'font.family': ['Yu Gothic', 'Meiryo', 'DejaVu Sans']})
rng = np.random.default_rng(0)


def jitter(n, w=0.12):
    return rng.uniform(-w, w, n)


def save(fig, name):
    fig.savefig(HERE / name, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('wrote', name)


# 1. time base -------------------------------------------------------------------------------
fig, ax = plt.subplots(1, 2, figsize=(10, 3.6), gridspec_kw={'width_ratios': [1.3, 1]})
pl = S['timeBase']['perLap']
x0 = 0
ticks, tl = [], []
for s in SESS:
    laps = [k for k in pl[s] if k.isdigit()]
    for lap in laps:
        d = pl[s][lap]
        xx = x0 + int(lap)
        for key, mk, lab in (('videoYaw', 'o', 'video yaw vs gyro'), ('dataTrack', 's', 'data track + calib.'), ('kerbAudio', '^', 'kerb audio onsets')):
            if d.get(key) is not None:
                ax[0].plot(xx, d[key], mk, ms=6, mfc='none' if key != 'videoYaw' else COL[s], mec=COL[s], mew=1.4,
                           label=lab if (s == SESS[1] and lap == '1') else None)
        ticks.append(xx)
        tl.append(f"{LAB[s][:4]}\nL{lap}")
    x0 += len(laps) + 1
ax[0].set_xticks(ticks, tl, fontsize=7)
ax[0].set_ylabel('offset L (s): true lap t = overlay-mapped t + L')
ax[0].set_title('Video-to-logger offset per lap (T2)', loc='left', color=INK, fontsize=10)
ax[0].legend(frameon=False, fontsize=7, loc='lower right')
ax[0].set_ylim(0, 0.35)
ev = E['timeBaseKerbEvents']
for i, (variant, lab) in enumerate((('none', 'no offset\n(overlay timer only)'), ('adopted', 'with per-lap L'))):
    for s in SESS:
        v = [x['residualS'][variant].get('t2ImuOnset50_minus_audioOnset50') for x in ev
             if x['session'] == s and x['audioConfidence'] in ('high', 'medium')]
        v = [a for a in v if a is not None]
        ax[1].plot(np.full(len(v), i) + jitter(len(v)) + (0.0 if s == SESS[1] else 0.25), v, 'o', ms=5, color=COL[s], alpha=0.85,
                   label=LAB[s] if i == 0 else None)
    allv = [x['residualS'][variant].get('t2ImuOnset50_minus_audioOnset50') for x in ev if x['audioConfidence'] in ('high', 'medium')]
    allv = [a for a in allv if a is not None]
    m = float(np.median(allv))
    ax[1].plot([i - 0.25, i + 0.25], [m, m], color=INK, lw=2)
    ax[1].text(i + 0.3, m, f'median {m:+.3f} s', va='center', fontsize=8, color=INK)
ax[1].axhline(0, color=INK2, lw=0.8)
ax[1].set_xticks([0, 1], ['no offset\n(overlay timer only)', 'with per-lap L'])
ax[1].set_xlim(-0.5, 1.9)
ax[1].set_ylabel('IMU onset minus kerb-audio onset (s)')
ax[1].set_title('Kerb strikes: logger vs video sound', loc='left', color=INK, fontsize=10)
ax[1].legend(frameon=False, fontsize=7, loc='upper right')
save(fig, 'join_timebase.png')

# 2. along-track by event family ----------------------------------------------------------------
fams = [('contactFirstBlock', 'Kerb contact onset\n(IMU, camera-free)'),
        ('turnInHeading50', 'Turn-in, heading rate\n(gyro, same rule)'),
        ('videoTipSpecFovCamera', 'Clipping point, video\n(spec-FOV camera f*h 889)'),
        ('videoTipNominal', 'Clipping point, video\n(T3 nominal camera f*h 1218)'),
        ('videoKerbEnds', 'Kerb block ends, video\n(nominal camera)')]
rows = {
    'contactFirstBlock': [(x['session'], x['block'].get('dSFirst')) for x in E['kerbContacts'] if x['primary']],
    'turnInHeading50': [(x['session'], x['dSHeading50']) for x in E['turnInAndTelemetryClip'] if x['qualityGood']],
    'videoTipSpecFovCamera': [(x['session'], x['cameraVariants'].get('fh889', {}).get('dS')) for x in E['clippingPointsVideo'] if not x['suspectFeature']],
    'videoTipNominal': [(x['session'], x['dS']) for x in E['clippingPointsVideo'] if not x['suspectFeature']],
    'videoKerbEnds': [(x['session'], x['dS']) for x in E['kerbEndsVideo'] if not x['suspectFeature']],
}
fig, ax = plt.subplots(figsize=(9, 4.6))
for i, (f, lab) in enumerate(fams):
    y = len(fams) - 1 - i
    for s in SESS:
        v = np.array([d for ss, d in rows[f] if ss == s and d is not None], float)
        vc = np.clip(v, -15, 15)
        ax.plot(vc, np.full(len(v), y) + jitter(len(v), 0.16) + (0.18 if s == SESS[0] else -0.04), 'o', ms=4.5, color=COL[s], alpha=0.75,
                label=LAB[s] if i == 0 else None)
    allv = [d for _, d in rows[f] if d is not None]
    m = float(np.median(allv))
    ax.plot([m, m], [y - 0.3, y + 0.38], color=INK, lw=2)
    ax.text(15.6, y, f'median {m:+.1f} m  (n={len(allv)})', va='center', fontsize=8, color=INK)
ax.axvline(0, color=INK2, lw=0.9)
ax.set_yticks(range(len(fams)), [lab for _, lab in fams][::-1], fontsize=8)
ax.set_xlim(-15, 15)
ax.set_xlabel('along-track discrepancy dS (m): 3DCG minus real car, + = 3DCG ahead (values beyond +-15 m drawn at the edge)')
ax.set_title('Where the 3DCG car is along the track at each truth event', loc='left', color=INK, fontsize=10)
ax.legend(frameon=False, fontsize=8, loc='lower left')
save(fig, 'join_along_track.png')

# 3. lateral at kerb contacts ---------------------------------------------------------------------
order = ['k13', 'k1', 'k15', 'k3', 'kDunlopR', 'k7', 'k20', 'k21', 'k9', 'k10', 'k23', 'k24']
fig, ax = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={'width_ratios': [1.4, 1]})
for s in SESS:
    xs, ys = [], []
    for x in E['kerbContacts']:
        if x['session'] != s or not x['primary'] or x['kerb'] not in order:
            continue
        xs.append(order.index(x['kerb']))
        ys.append(x['block']['gapAtOnset'])
    ax[0].plot(np.array(xs) + jitter(len(xs)) + (0.15 if s == SESS[0] else -0.05), ys, 'o', ms=5, color=COL[s], alpha=0.85, label=LAB[s])
ax[0].axhline(0, color=INK2, lw=0.9)
ax[0].set_xticks(range(len(order)), order, fontsize=8)
ax[0].set_ylabel('CG gap to block line at the truth onset (m)\n+ = 3DCG not yet on the kerb')
ax[0].set_title('Lateral: 3DCG tyre vs kerb at the moment the car hits it', loc='left', color=INK, fontsize=10)
ax[0].legend(frameon=False, fontsize=8)
for s in SESS:
    p = [x for x in E['kerbPasses'] if x['session'] == s]
    tp = [x['cgMinGapBlock'] for x in p if x['classBlock'] == 'TP']
    fn = [x['cgMinGapBlock'] for x in p if x['classBlock'] == 'FN']
    fp = [x['cgMinGapBlock'] for x in p if x['classBlock'] == 'FP']
    off = 0.0 if s == SESS[1] else 0.25
    for i, (v, lab) in enumerate(((tp, 'both on kerb'), (fn, 'truth on, CG off'), (fp, 'CG on, truth silent'))):
        v = [a for a in v if a is not None]
        ax[1].plot(np.full(len(v), i) + jitter(len(v), 0.08) + off, np.clip(v, -2, 2.5), 'o', ms=4.5, color=COL[s], alpha=0.8)
ax[1].axhline(0, color=INK2, lw=0.9)
ax[1].set_xticks([0.1, 1.1, 2.1], ['both on kerb', 'truth on,\nCG off', 'CG on,\ntruth silent'], fontsize=8)
ax[1].set_ylabel('CG minimum gap over the pass (m)')
ax[1].set_title('Pass detection (block line)', loc='left', color=INK, fontsize=10)
save(fig, 'join_lateral_contacts.png')

# 4. camera scale --------------------------------------------------------------------------------
cs = S['cameraSensitivityClippingPoint']
fig, ax = plt.subplots(figsize=(7.5, 3.8))
for s in SESS:
    pts = sorted((v['fTimesH'], v['dt']['median']) for k, v in cs[s].items()
                 if isinstance(v, dict) and v.get('fTimesH') and v.get('dt', {}).get('median') is not None and k != 'nominal')
    ax.plot([p[0] for p in pts], [p[1] for p in pts], '-o', color=COL[s], lw=2, ms=5, label=f'{LAB[s]}: video clipping point')
c = S['alongTrack']['contactFirstBlock']['fuji_aim_2020_07_30']['dt']
ax.axhspan(c['q25'], c['q75'], color='#d9d8d3', alpha=0.6, lw=0)
ax.axhline(c['median'], color=INK2, lw=1.2)
ax.text(905, c['median'] + 0.01, f"camera-free kerb-contact onset, 7/30:\nmedian {c['median']:+.3f} s (band = IQR)", fontsize=7.5, color=INK2, va='bottom')
ax.axhline(0, color=INK2, lw=0.6)
for fh, lab in ((889, 'spec FOV 84 deg\nf711 h1.25'), (1218, 'T3 nominal\nf967 h1.26')):
    ax.axvline(fh, color=INK2, lw=0.8)
    ax.text(fh + 8, -0.33, lab, fontsize=7.5, color=INK2)
ax.set_xlabel('camera scale f*h (px m) -> forward distance seen by image row 276')
ax.set_ylabel('dt (s): 3DCG minus truth')
ax.set_title('Clipping-point timing depends on the unknown camera scale', loc='left', color=INK, fontsize=10)
ax.legend(frameon=False, fontsize=8, loc='upper right')
ax.set_ylim(-0.36, 0.2)
save(fig, 'join_camera_scale.png')

# 5. LOLO corrections ----------------------------------------------------------------------------
C = S['corrections']
items = [
    ('Video offset L per lap\n(on kerb-audio residual, s)', C['C1_videoTimeOffset']['results']['none']['pooled'], C['C1_videoTimeOffset']['results']['dataTrackLOLO']['pooled'], 's'),
    ('Global along-track shift\n(on contact dS, m)', C['C2_alongTrackOffset_fromContacts']['onContacts']['pooled']['before'], C['C2_alongTrackOffset_fromContacts']['onContacts']['pooled']['after'], 'm'),
    ('Along-track shift fitted to\nnominal-camera tips (on contact dS, m)', C['C2b_alongTrackOffset_fromVideoTips']['onContacts']['pooled']['before'], C['C2b_alongTrackOffset_fromVideoTips']['onContacts']['pooled']['after'], 'm'),
    ('Global lateral shift toward kerb\n(on contact gap, m)', C['C3_lateral']['towardKerb']['onContactGapAtOnset']['pooled']['before'], C['C3_lateral']['towardKerb']['onContactGapAtOnset']['pooled']['after'], 'm'),
    ('Per-kerb lateral shift\n(on contact gap, m)', C['C7_perKerbLateral']['onContactGapAtOnset']['pooled']['before'], C['C7_perKerbLateral']['onContactGapAtOnset']['pooled']['after'], 'm'),
]
fig, ax = plt.subplots(figsize=(8.5, 3.8))
for i, (lab, b, a, u) in enumerate(items):
    y = len(items) - 1 - i
    bb, aa = b['medianAbs'], a['medianAbs']
    ax.annotate('', xy=(aa, y), xytext=(bb, y), arrowprops=dict(arrowstyle='->', color=INK2, lw=1.2))
    ax.plot(bb, y, 'o', ms=7, color='#9b9a95')
    ax.plot(aa, y, 'o', ms=7, color=INK)
    ax.text(max(aa, bb) * 1.08 + 0.02, y, f'{bb:.2f} -> {aa:.2f} {u}  (median |.|, n={b["n"]})', va='center', fontsize=8, color=INK)
ax.set_yticks(range(len(items)), [it[0] for it in items][::-1], fontsize=8)
ax.set_xscale('log')
ax.set_xlim(0.03, 30)
ax.set_xlabel('median absolute discrepancy, before (grey) -> after (black), leave-one-lap-out')
ax.set_title('Candidate corrections tested on held-out laps', loc='left', color=INK, fontsize=10)
save(fig, 'join_corrections_lolo.png')
