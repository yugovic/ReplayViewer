# Build the LOCAL-ONLY comparison page docs/fuji-event-comparison-2026-10-22.html
#
#   pipeline/.venv-sr/Scripts/python.exe -I artifacts/event-comparison-2026-10-22/scripts/build_page.py
#
# Reads events.json, the image manifests (frames/manifest.json, cg/manifest_onboard_vpfix.json for the
# onboard 3DCG, cg/manifest.json for the chase 3DCG, schematic/schematics.json) and
# verify/scale_sensitivity.json, and writes a
# self-contained HTML page (CSS/JS inline) that references every image by RELATIVE path
# (../artifacts/event-comparison-2026-10-22/...). No image is embedded or copied.
# Onboard video frames are LOCAL REFERENCE ONLY: both the page and the image folder are
# gitignored (.gitignore lines 33-34) and must never be published or uploaded.
import html
import json
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent                                   # artifacts/event-comparison-2026-10-22
REPO = BASE.parent.parent                            # D:/00_Dev/ReplayViewer
OUT = REPO / 'docs' / 'fuji-event-comparison-2026-10-22.html'
REL = '../artifacts/event-comparison-2026-10-22/'    # from docs/ to the image folder

import math

plan = json.loads((BASE / 'events.json').read_text(encoding='utf-8'))
frames_m = json.loads((BASE / 'frames' / 'manifest.json').read_text(encoding='utf-8'))
# 3DCG images come from TWO capture runs (review round 1, R1-06): the chase images from the first run
# (cg/manifest.json, 19:45 JST; its onboard entries are superseded) and the onboard images from the
# vanishing-point run (cg/manifest_onboard_vpfix.json, capture_cg_vpfix.mjs: pitch AND yaw solved so the car
# forward axis lands on the T3 video vanishing point; the 20:41 pitch-only run is kept in cg/superseded/).
CHASE_MANIFEST = 'cg/manifest.json'
ONBOARD_MANIFEST = 'cg/manifest_onboard_vpfix.json'
chase_m = json.loads((BASE / CHASE_MANIFEST).read_text(encoding='utf-8'))
onb_m = json.loads((BASE / ONBOARD_MANIFEST).read_text(encoding='utf-8'))
sch_m = json.loads((BASE / 'schematic' / 'schematics.json').read_text(encoding='utf-8'))
scale_s = json.loads((BASE / 'verify' / 'scale_sensitivity.json').read_text(encoding='utf-8'))
_vp = json.loads((REPO / 'artifacts' / 'kerb-event-truth-2026-10-22' / 'T3-video-kerb-passages' / 'vp_calib.json').read_text(encoding='utf-8'))['summary']
EV = plan['events']


def iso_epoch(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def vp_aim(u, v, f=711.0, cx=640.0, cy=360.0, roll_deg=0.0):
    """pitch/yaw (deg) putting the car forward axis on pixel (u, v); scene convention Rz(roll) Rx(pitch) Ry(-yaw)
    (= T3 common.R_cam), q = Rz^T r = (-sin yaw, -sin pitch cos yaw, cos pitch cos yaw)."""
    r = ((u - cx) / f, (v - cy) / f, 1.0)
    cr, sr = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    q = (cr * r[0] + sr * r[1], -sr * r[0] + cr * r[1], r[2])
    n = math.sqrt(sum(x * x for x in q))
    q = [x / n for x in q]
    return math.degrees(math.atan2(-q[1], q[2])), math.degrees(math.asin(-q[0]))


# ---------------------------------------------------------------- checks on inputs
assert len(EV) == 106 and plan['total'] == 106, len(EV)
assert len({e['id'] for e in EV}) == len(EV)
assert frames_m['summary']['failed'] == 0 and frames_m['summary']['images'] == 212
# chase: first run, chase entries only
assert chase_m['summary']['failures'] == 0 and chase_m['summary']['chase'] == 106
chase_caps = [c for c in chase_m['captures'] if c['camera'] == 'chase']
assert len(chase_caps) == 106 and all(c['cameraModeUsed'] == 'chase' for c in chase_caps)
# onboard: vanishing-point run
S_ON = onb_m['summary']
assert S_ON['captures'] == 106 and S_ON['onboardActive'] == 106 and S_ON['failures'] == 0, S_ON
assert S_ON['pageErrors'] == 0 and S_ON['unexpectedBadResponses'] == 0 and S_ON['maxVpErrorPx720'] <= 0.05, S_ON
assert all(c['cameraModeUsed'] == 'onboard' and c['onboardActive'] and not c['problems'] for c in onb_m['captures'])
ONB_PROF = {}
for race, sess in onb_m['sessions'].items():
    p = sess['profile']
    assert p['f'] == 711 and p['upMeters'] == 1.25, p
    pe, ye = vp_aim(_vp[race]['u_vp_median'], _vp[race]['v_vp_median'], p['f'], p['cx'], p['cy'], p['rollDeg'])
    assert abs(p['pitchDeg'] - pe) < 1e-3 and abs(p['yawDeg'] - ye) < 1e-3, (race, p, pe, ye)
    ONB_PROF[race] = p
assert set(ONB_PROF) == {'fuji_aim_01', 'fuji_aim_2020_07_30'}
# the files on disk must come from these runs (mtime inside each run's time window, 5 s slack)
for caps, man in ((chase_caps, chase_m), (onb_m['captures'], onb_m)):
    t_end = iso_epoch(man['generated'])
    t_start = iso_epoch(man['started']) if 'started' in man else t_end - man['summary']['elapsedSeconds']
    for c in caps:
        mt = (BASE / c['file']).stat().st_mtime
        assert t_start - 5 <= mt <= t_end + 5, (c['file'], mt, t_start, t_end)
frame_files = {i['file'] for i in frames_m['images']}
cg_files = {c['file'] for c in chase_caps} | {c['file'] for c in onb_m['captures']}
sch_files = {s['file'] for s in sch_m['schematics']}
# camera-scale sensitivity of the image check (R1-07): 7/30 clipping, f*h 590 / 889 / 1209
SC = {k: v['pair'] for k, v in scale_s['scales'].items()}
SCC = {k: v['earlyControl'] for k, v in scale_s['scales'].items()}
assert set(SC) == {'0.83', '1.25', '1.70'} and all(v['n'] == 30 for v in SC.values()), SC
assert SC['1.25']['fixedCloser'] == 24 and SC['1.25']['rawCloser'] == 6, SC   # = first run (verify.json 24/30)


def files_for(eid):
    return {
        'fixed': f'frames/{eid}_video_fixed.jpg',
        'raw': f'frames/{eid}_video_raw.jpg',
        'onboard': f'cg/{eid}_cg_onboard.jpg',
        'chase': f'cg/{eid}_cg_chase.jpg',
        'top': f'schematic/{eid}_top.png',
    }


for e in EV:
    f = files_for(e['id'])
    assert f['fixed'] in frame_files and f['raw'] in frame_files, e['id']
    assert f['onboard'] in cg_files and f['chase'] in cg_files, e['id']
    assert f['top'] in sch_files, e['id']
    for p in f.values():
        assert (BASE / p).is_file(), p
    t = e['timing']
    assert abs(t['captureSeekSeconds'] - e['truthTime']['tStar']) < 1e-9
assert (BASE / 'schematic' / 'legend_top.png').is_file()

esc = html.escape
TYPE_JA = {'kerbContact': '縁石に乗った瞬間', 'clipping': 'クリッピングポイント', 'turnIn': '切り始め'}
TYPE_ORD = {'kerbContact': 0, 'clipping': 1, 'turnIn': 2}
TYPE_CLS = {'kerbContact': 't-contact', 'clipping': 't-clip', 'turnIn': 't-turn'}
DATE_KEY = {'2020-07-29': '0729', '2020-07-30': '0730'}
DATE_JA = {'0729': '7/29（ウェット）', '0730': '7/30（ドライ）'}
DATE_SHORT = {'0729': '7/29', '0730': '7/30'}
CONF_JA = {'high': '高', 'medium': '中', 'low': '低'}
LBASIS_JA = {
    'transientVideoYawVsGyro': '測定（映像の回転とジャイロの突き合わせ）',
    'dataTrackLoggerClockPlusCalibration': '推定（ロガーの時計と7/30の較正から）',
}
LALT_JA = {
    'videoYaw': '映像の回転',
    'dataTrackLOLO': 'ロガーの時計（その周を外して較正）',
    'sessionConstLOLO': 'その日の一定値（その周を外して）',
    'kerbAudio': '縁石の音',
    'kerbShake': '映像の揺れ',
}
MINUS = '\u2212'


def sgn(x, nd=2):
    s = f'{abs(x):.{nd}f}'
    if float(s) == 0:
        return '±' + s
    return ('+' if x > 0 else MINUS) + s


def fmt_lap(T):
    m = int(T // 60)
    return f'{m}:{T - 60 * m:06.3f}'


def fmt_timer(T):
    m = int(T // 60)
    return f'{m}:{T - 60 * m:05.2f}'


def fmt_vid(p):
    m = int(p // 60)
    return f'{m}:{p - 60 * m:05.2f}'


def along_plain(dS):
    if abs(dS) < 0.05:
        return '同じ瞬間、3DCGと本物はほぼ同じ位置'
    return f'同じ瞬間、3DCGは本物より {abs(dS):.1f} m ' + ('前（先）' if dS > 0 else '後ろ')


def dt_plain(dt):
    if abs(dt) < 0.005:
        return 'ほぼ同時'
    return f'{abs(dt):.2f} 秒' + ('遅い' if dt > 0 else '早い')


def lat_plain_contact(d):
    if abs(d) < 0.005:
        return '3DCGのタイヤもちょうど縁石の線の上'
    if d > 0:
        return f'3DCGのタイヤはまだ縁石の {abs(d):.2f} m 手前（乗っていない）'
    return f'3DCGのタイヤはすでに縁石に {abs(d):.2f} m 入っている'


def lat_plain_clip(d):
    if abs(d) < 0.005:
        return '縁石までの横の距離はほぼ同じ'
    if d > 0:
        return f'3DCGの方が縁石から {abs(d):.2f} m 遠い（コース中央寄り）'
    return f'3DCGの方が {abs(d):.2f} m 縁石寄り'


NOTE_MAP = [
    ('7/29のL', '7/29の時刻補正は推定値です（妥当な幅は0.17〜0.30秒）。検証役が映像の回転から求めた値はこれより0.04〜0.10秒大きく、その場合は本当の瞬間が補正後のコマより1〜3コマ（最大で約0.1秒）前になります。音で確かめた2件では逆に0.03〜0.05秒小さい値でした。'),
    ('specFovCamera', '前後・横のずれの大きさは、校正していないカメラの尺度に左右されます（ここでは仕様の画角に合わせた f 711 px・高さ1.25 m の値）。正解の時刻そのものは画面上の出来事なので、カメラに左右されません。'),
    ('k1（コカ', 'コカ・コーラ（k1）は縁石の先端の形のため、3DCGのタイヤと縁石の線が接触の始まりで急に近づき（約12 m/秒）、乗った瞬間の距離が大きく出ます。評価では、7/30の全周で3DCGの接触が約0.15秒遅いと指摘しており、個別の確認待ちです。'),
]


def plain_notes(e):
    out = []
    for n in e.get('notesJa', []):
        hit = [ja for key, ja in NOTE_MAP if key in n]
        assert len(hit) == 1, (e['id'], n)
        out.append(hit[0])
    return out


# eye-checks of the overlay LAP timer (by eye on the extracted frames)
eye = {}
for e in EV:
    for c in e.get('timerEyeCheck', []):
        v = 'fixed' if c['frame'].startswith('corrected') else 'raw'
        eye.setdefault(e['id'], []).append((v, c['readTimer'], c['readMinusExpectedS']))
for c in frames_m['eyeChecks']['checks']:
    name = Path(c['file']).name
    eid, v = name.rsplit('_video_', 1)
    v = v.replace('.jpg', '')
    eye.setdefault(eid, []).append((v, c['read'].split(' ', 2)[-1], c['readMinusExpectedS']))

# course order of kerbs = median lap time of their events
kerb_t = {}
for e in EV:
    kerb_t.setdefault(e['kerb'], []).append(e['truthTime']['tStar'])
KERB_ORDER = sorted(kerb_t, key=lambda k: statistics.median(kerb_t[k]))
KERB_NAME = {e['kerb']: e['kerbName'] for e in EV}


def metrics(e):
    """(along m or None, along s or None, lateral m or None, primary |value|)"""
    d = e['discrepancy']
    if e['type'] == 'kerbContact':
        along = d['dSFirstBlock']
        dt = d['dtFirstBlock']
        lat = d['dLatBlock']
        primary = abs(lat)
    elif e['type'] == 'clipping':
        s = d['specFovCamera']
        along, dt, lat = s['dS'], s['dt'], s['dLat']
        primary = abs(along)
    else:
        along, dt, lat = d['dSHeading50'], d['dtHeading50'], None
        primary = abs(along)
    return along, dt, lat, primary


def title_text(e):
    dk = DATE_KEY[e['date']]
    return f"{DATE_SHORT[dk]} {e['lap']}周目 {e['kerbName']}（{e['kerb']}）{TYPE_JA[e['type']]}"


def img_tag(path, w, h, alt, cls=''):
    c = f' class="{cls}"' if cls else ''
    return (f'<img{c} src="{esc(REL + path)}" width="{w}" height="{h}" loading="lazy" '
            f'decoding="async" alt="{esc(alt)}">')


def guide(label):
    # row 276 of the 720-row video = 38.333 % of the image height (also of the 960x540 3DCG onboard image)
    return f'<i class="guide" aria-hidden="true"><span>{esc(label)}</span></i>'


GUIDE_VIDEO = guide('判定の高さ（動画の276行目）')
GUIDE_CG = guide('判定の高さ（同じ276行目）')


def card(e):
    eid = e['id']
    t = e['timing']
    d = e['discrepancy']
    dk = DATE_KEY[e['date']]
    f = files_for(eid)
    along, dt, lat, primary = metrics(e)
    v_ms = t['gpsSpeedKmhAtT'] / 3.6
    shift_m = t['L'] * v_ms
    is_clip = e['type'] == 'clipping'
    title = f"{DATE_JA[dk]} {e['lap']}周目 {e['kerbName']}"
    est = t['LLabel'] == 'estimated'

    # ---- what the truth is
    if e['type'] == 'kerbContact':
        vc = e['videoConfirmation']
        conf = '（動画の音・揺れでも確認）' if vc['confirmed'] else '（動画の音・揺れでははっきり確認できず、ロガーのみ）'
        truth = (f"正解：ロガーの上下G・ロール方向の振動が立ち上がった時刻{conf}。確度 {CONF_JA[e['confidence']]}。"
                 '車載の映像では、縁石は車体の下やダッシュボードの陰になり、乗った瞬間そのものは写りません。比べるのは3DCGの追従カメラと上から見た図です')
    elif is_clip:
        truth = '正解：動画の決まった高さ（276行目）で、内側の縁石の縁が画面の中央に最も近づいたコマの時刻に、動画の時刻補正を足したもの'
    else:
        truth = '正解：ジャイロで、車の向きの変化が50%に達した時刻（3DCGの向きと同じ決め方）'

    # ---- figures
    vid_alt = f"実際の車載映像のコマ（{title_text(e)}）"
    fixed_cap = (f"動画 {fmt_vid(t['correctedFramePts'])}・LAP表示 "
                 f"{fmt_timer(t['correctedFramePts'] - t['lapStartPtsOverlay'])} 前後")
    raw_cap = (f"動画 {fmt_vid(t['uncorrectedFramePts'])}・LAP表示 "
               f"{fmt_timer(t['uncorrectedFramePts'] - t['lapStartPtsOverlay'])} 前後・本物は約 {shift_m:.1f} m 先")
    vid_img = (f'<img class="vid" src="{esc(REL + f["fixed"])}" data-fixed="{esc(REL + f["fixed"])}" '
               f'data-raw="{esc(REL + f["raw"])}" width="960" height="540" loading="lazy" decoding="async" '
               f'alt="{esc(vid_alt)}">')
    g = GUIDE_VIDEO if is_clip else ''
    g_cg = GUIDE_CG if is_clip else ''
    gc = ' has-guide' if is_clip else ''
    if e['type'] == 'kerbContact':
        top_cap = '青＝3DCGの車、橙の点線＝推定の本物（3DCGを横にずらして縁石の線に合わせた位置）。線と車体は目安'
    elif is_clip:
        top_cap = '青＝3DCGの車、橙＝ずれの分だけ動かした推定の本物、○＝動画から見た縁石の位置（目安）'
    else:
        top_cap = '青＝3DCGの車、青の破線＝3DCGの向きが50%に達する位置、▲＝ステアの切り始め'
    figs = f'''<div class="figs">
<figure><button type="button" class="imgbtn v-video{gc}" aria-label="拡大：実際の映像">{vid_img}{g}</button>
<figcaption><b class="only-fixed">実際の映像（補正後）</b><b class="only-raw">実際の映像（補正前・LAP表示合わせ）</b><span class="only-fixed mono">{esc(fixed_cap)}</span><span class="only-raw mono">{esc(raw_cap)}</span></figcaption></figure>
<figure><button type="button" class="imgbtn{gc}" aria-label="拡大：3DCG 車載カメラ">{img_tag(f["onboard"], 960, 540, "3DCG 車載カメラ（" + title_text(e) + "）")}{g_cg}</button>
<figcaption><b>3DCG 車載カメラ</b><span>推定カメラ f 711 px・高さ1.25 m（下向きと左右の向きは動画の消失点に合わせた値）。自車は描いていない</span></figcaption></figure>
<figure><button type="button" class="imgbtn" aria-label="拡大：3DCG 追従カメラ">{img_tag(f["chase"], 960, 540, "3DCG 追従カメラ（" + title_text(e) + "）")}</button>
<figcaption><b>3DCG 追従カメラ</b><span>車の後ろ上方から</span></figcaption></figure>
<figure><button type="button" class="imgbtn schem" aria-label="拡大：上から見た図">{img_tag(f["top"], 900, 420, "上から見た図（" + title_text(e) + "）")}</button>
<figcaption><b>上から見た図</b><span>{esc(top_cap)}</span></figcaption></figure>
</div>'''

    # ---- numbers
    if e['type'] == 'kerbContact':
        if along is not None:
            a_v = f'{sgn(along, 1)} m'
            a_s = f'{sgn(dt)} 秒'
            a_p = (f"3DCGで縁石に乗るのは{dt_plain(dt)} → {along_plain(along)}。"
                   '浅い角度で近づくため、前後は大きく出やすい')
        else:
            gmin = d['cgMinGapDuringTruthContactBlock']
            assert gmin > 0, eid
            a_v, a_s = '—', ''
            a_p = f'3DCGでは、本物が乗っている間も縁石の線に乗らない（最も近づいて {gmin:.2f} m 手前）。前後のずれは出せない'
        l_v = f'{sgn(lat)} m'
        l_p = f"{lat_plain_contact(lat)}。前輪 {sgn(d['dLatBlockFront'])}／後輪 {sgn(d['dLatBlockRear'])} m"
        l_k = '横のずれ（タイヤと縁石の線の距離。本当は0）'
    elif is_clip:
        a_v, a_s = f'{sgn(along, 1)} m', f'{sgn(dt)} 秒'
        a_p = f'3DCGで縁石の先端が同じ画面の高さに来るのは{dt_plain(dt)} → {along_plain(along)}'
        l_v = f'{sgn(lat)} m'
        l_p = f'{lat_plain_clip(lat)}。カメラの仮定に左右される目安'
        l_k = '横のずれ（縁石までの横の距離の差）'
    else:
        a_v, a_s = f'{sgn(along, 1)} m', f'{sgn(dt)} 秒'
        a_p = f'3DCGの向きが50%に達するのは{dt_plain(dt)} → {along_plain(along)}'
        l_v, l_p = '—', '切り始めには横の正解がない'
        l_k = '横のずれ'
    a_cls = '' if along is not None else ' na'
    l_cls = '' if lat is not None else ' na'
    lab = '測定' if not est else '推定'
    nums = f'''<div class="nums">
<div class="{a_cls.strip()}"><div class="k">前後のずれ</div><div class="v">{a_v}{f' <small>{a_s}</small>' if a_s else ''}</div><div class="s">{esc(a_p)}</div></div>
<div class="{l_cls.strip()}"><div class="k">{esc(l_k)}</div><div class="v">{l_v}</div><div class="s">{esc(l_p)}</div></div>
<div><div class="k">速度</div><div class="v">{t['gpsSpeedKmhAtT']:.0f} <small>km/h</small></div><div class="s">正解の時刻のGPS速度</div></div>
<div><div class="k">動画の時刻補正</div><div class="v">{t['L']:.3f} <small>秒（{lab}）</small></div><div class="s">補正前の画像は {t['L']:.2f} 秒あと＝約 {shift_m:.1f} m 先の映像</div></div>
</div>'''

    notes = plain_notes(e)
    notes_html = ''
    if notes:
        notes_html = '<ul class="cnotes">' + ''.join(f'<li>{esc(n)}</li>' for n in notes) + '</ul>'

    # ---- details
    rows = [
        ('正解の時刻（ビューアの再生時刻）', f"{e['truthTime']['tStar']:.3f} 秒"),
        ('ビューアの表示（ラップ時計）', f"{fmt_lap(t['hudLapClockSeconds'])}（{t['hudLapClockSeconds']:.3f} 秒）"),
        ('正解の決め方', e['eventJa']),
        ('動画のコマ（補正後）', f"{fmt_vid(t['correctedFramePts'])}・{t['correctedFrameIndex']}コマ目"
                          f"（狙った時刻との差 {sgn(t['correctedFramePts'] - t['correctedPts'], 3)} 秒）"),
        ('動画のコマ（補正前）', f"{fmt_vid(t['uncorrectedFramePts'])}・{t['uncorrectedFrameIndex']}コマ目"
                          f"（本物はビューアの {t['uncorrectedFrameShowsTrueViewerT']:.3f} 秒の位置）"),
        ('動画のLAP表示（目安）', f"LAP {t['overlayLap']}（ロガーの周番号＋1）："
                            f"補正後 {fmt_timer(t['correctedFramePts'] - t['lapStartPtsOverlay'])}"
                            f"／補正前 {fmt_timer(t['uncorrectedFramePts'] - t['lapStartPtsOverlay'])}"),
        ('動画の時刻補正', f"{t['L']:.3f} 秒・{LBASIS_JA[t['LBasis']]}"),
    ]
    alts = [f"{LALT_JA[k]} {v:.3f}" for k, v in t['LAlternatives'].items() if v is not None and k in LALT_JA]
    assert all(k in LALT_JA for k in t['LAlternatives']), t['LAlternatives']
    if alts:
        rows.append(('時刻補正の別の求め方', '・'.join(alts) + ' 秒'))
    for v, read, diff in eye.get(eid, []):
        rows.append((f"LAP表示を目で読んだ結果（{'補正後' if v == 'fixed' else '補正前'}）",
                     f"{read}（予想との差 {sgn(diff, 3)} 秒。表示の更新は約0.05秒ごと）"))
    if e['type'] == 'kerbContact':
        vc = e['videoConfirmation']
        rows.append(('動画での確認', f"縁石の音 {CONF_JA[vc['audioConfidence']]}・映像の揺れ {CONF_JA[vc['shakeConfidence']]}"))
        rows.append(('振動の山・終わり', f"{e['truthTime']['truthPeakT']:.3f} 秒・{e['truthTime']['truthOffsetT']:.3f} 秒"))
        if d['cgContactStartTBlock'] is not None:
            rows.append(('3DCGで縁石の線に乗っている時間', f"{d['cgContactStartTBlock']:.3f}〜{d['cgContactEndTBlock']:.3f} 秒"))
        if d['dSFirstPaint'] is not None:
            rows.append(('白線を基準にした場合（参考）', f"前後 {sgn(d['dSFirstPaint'], 1)} m（{sgn(d['dtFirstPaint'])} 秒）・横 {sgn(d['dLatPaint'])} m"))
    elif is_clip:
        n = d['nominalCamera']
        o = d['overlayOnlyNominal']
        rows.append(('名目のカメラ（f 967 px・高さ1.26 m）の場合', f"前後 {sgn(n['dS'], 1)} m（{sgn(n['dt'])} 秒）・横 {sgn(n['dLat'])} m"))
        rows.append(('時刻補正なし・名目のカメラ（以前の比較資料の見え方）', f"前後 {sgn(o['dS'], 1)} m（{sgn(o['dt'])} 秒）"))
        te = d['telemetry']
        rows.append(('正解の時刻 − ヨーレートの山／最低速度', f"{sgn(te['truthTipMinusYawRatePeak'])} 秒／{sgn(te['truthTipMinusMinSpeed'])} 秒"))
    else:
        tt = e['truthTime']
        rows.append(('ステアの切り始め', f"{tt['steerOnsetT']:.3f} 秒"))
        rows.append(('3DCGの向きが50%に達する時刻', f"{tt['cgDrawnYawT50']:.3f} 秒"))
        rows.append(('見た目の切り始め（向き10%）の差（参考）',
                     f"3DCG − 平滑化しないジャイロ {sgn(d['cgT10MinusImuRawT10'])} 秒。3DCGは前後0.6秒の区間で向きを出すため早めに出やすい"))
    rows.append(('ファイル名の元', eid))
    det = ''.join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in rows)

    est_chip = '<span class="chip s-borderline">時刻補正は推定</span>' if est else ''
    data = (f'data-date="{dk}" data-lap="{e["lap"]}" data-kerb="{esc(e["kerb"])}" data-type="{e["type"]}" '
            f'data-tord="{TYPE_ORD[e["type"]]}" data-t="{e["truthTime"]["tStar"]:.3f}" '
            f'data-along="{abs(along) if along is not None else -1:.3f}" '
            f'data-lat="{abs(lat) if lat is not None else -1:.3f}" data-primary="{primary:.3f}" '
            f'data-variant="fixed"')
    return f'''<article class="ev" id="{esc(eid)}" tabindex="-1" {data}>
<div class="evhead">
<div class="evtitle"><span class="tchip {TYPE_CLS[e['type']]}">{TYPE_JA[e['type']]}</span><h3>{esc(title)} <span class="kid">{esc(e['kerb'])}・{esc(e['sideJa'])}側の縁石</span></h3><span class="lt mono">ラップ {fmt_lap(t['hudLapClockSeconds'])}</span>{est_chip}</div>
<div class="vtwrap"><span class="vtl">映像</span><div class="seg vt" role="group" aria-label="この瞬間の映像の時刻"><button type="button" data-v="fixed" aria-pressed="true">補正後</button><button type="button" data-v="raw" aria-pressed="false">補正前</button></div></div>
</div>
<p class="evdef">{esc(truth)}</p>
{figs}
{nums}
{notes_html}
<details><summary>細かい値</summary><div class="scroll"><table class="det">{det}</table></div></details>
</article>'''


# ---------------------------------------------------------------- summary numbers
def typ(vals):
    return statistics.median([abs(v) for v in vals]) if vals else None


summary = []
for ty in ('kerbContact', 'clipping', 'turnIn'):
    evs = [e for e in EV if e['type'] == ty]
    by = {dk: [e for e in evs if DATE_KEY[e['date']] == dk] for dk in ('0729', '0730')}
    kind = '横' if ty == 'kerbContact' else '前後'
    stats = []
    for dk in ('0730', '0729'):
        vals = []
        for e in by[dk]:
            a, _, l, _ = metrics(e)
            vals.append(l if ty == 'kerbContact' else a)
        stats.append((dk, len(vals), typ(vals)))
    worst = sorted(evs, key=lambda e: -metrics(e)[3])[:5]
    summary.append((ty, evs, by, kind, stats, worst))

# cross-check the typical values against the 2026-10-22 evaluation page (7/30 rows)
s730 = {ty: next(s for s in st if s[0] == '0730') for ty, _, _, _, st, _ in summary}
assert abs(s730['kerbContact'][2] - 0.43) < 0.01, s730
assert abs(s730['clipping'][2] - 1.12) < 0.01, s730


def worst_line(e):
    a, _, l, _ = metrics(e)
    dk = DATE_KEY[e['date']]
    if e['type'] == 'kerbContact':
        val = f'横 {sgn(l)} m'
    else:
        val = f'前後 {sgn(a, 1)} m'
    return (f'<li><a class="jump" href="#{esc(e["id"])}">{DATE_SHORT[dk]} {e["lap"]}周目 {esc(e["kerbName"])}</a>'
            f'<span class="mono">{val}</span></li>')


tiles = []
SUB = {
    'kerbContact': '代表のずれ＝横（その瞬間のタイヤと縁石の線の距離）',
    'clipping': '代表のずれ＝前後（仕様の画角のカメラ）',
    'turnIn': '代表のずれ＝前後（向き50%の時刻差×速度）',
}
for ty, evs, by, kind, stats, worst in summary:
    st_txt = '典型 ' + '・'.join(f"{DATE_SHORT[dk]} {v:.2f} m" for dk, n, v in stats if n)
    scope = '（7/30は2周目、7/29は3周目のみ）' if ty == 'turnIn' else ''
    tiles.append(f'''<div class="sumtile">
<div class="sk"><span class="tchip {TYPE_CLS[ty]}">{TYPE_JA[ty]}</span></div>
<div class="sn mono">{len(evs)}<small> 件</small></div>
<div class="ss">7/29 {len(by['0729'])}件・7/30 {len(by['0730'])}件{scope}</div>
<div class="ss">{SUB[ty]}：{st_txt}</div>
<div class="sw">ずれの大きい5件</div>
<ol class="worst">{''.join(worst_line(e) for e in worst)}</ol>
</div>''')

# ---------------------------------------------------------------- filters
def opt(value, label):
    return f'<option value="{esc(value)}" data-label="{esc(label)}">{esc(label)}</option>'


laps = sorted({e['lap'] for e in EV})
f_date = opt('all', 'すべて') + opt('0729', '7/29（ウェット）') + opt('0730', '7/30（ドライ）')
f_lap = opt('all', 'すべて') + ''.join(opt(str(l), f'{l}周目') for l in laps)
f_kerb = opt('all', 'すべて') + ''.join(opt(k, f'{KERB_NAME[k]}（{k}）') for k in KERB_ORDER)
f_type = opt('all', 'すべて') + ''.join(opt(t, TYPE_JA[t]) for t in ('kerbContact', 'clipping', 'turnIn'))
f_sort = ''.join(f'<option value="{v}">{esc(l)}</option>' for v, l in (
    ('course', 'コース順（日・周・時刻）'),
    ('primary', 'ずれの大きい順（種類ごとの代表値）'),
    ('along', '前後のずれの大きい順'),
    ('lat', '横のずれの大きい順'),
))

order = sorted(EV, key=lambda e: (DATE_KEY[e['date']], e['lap'], e['truthTime']['tStar'], TYPE_ORD[e['type']]))
cards_html = '\n'.join(card(e) for e in order)

JST = timezone(timedelta(hours=9))
generated = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
c = plan['counts']
cbs = plan['countsBySession']


def aim_ja(race):
    p = ONB_PROF[race]
    y = p['yawDeg']
    side = '右' if y > 0 else '左'
    return f"下向き{p['pitchDeg']:.1f}°・{side}向き{abs(y):.1f}°"


AIM29, AIM30 = aim_ja('fuji_aim_01'), aim_ja('fuji_aim_2020_07_30')
assert AIM29 == '下向き13.7°・左向き0.5°' and AIM30 == '下向き12.9°・右向き1.6°', (AIM29, AIM30)
ONB_RUN = datetime.fromisoformat(onb_m['generated'].replace('Z', '+00:00')).astimezone(JST).strftime('%H:%M')
CHASE_RUN = datetime.fromisoformat(chase_m['generated'].replace('Z', '+00:00')).astimezone(JST).strftime('%H:%M')

CSS = r'''
:root {
  --bg: #f1f3f2; --surface: #fbfcfb; --ink: #14181b; --ink2: #3f474d; --muted: #6b747b;
  --line: #d3d8db; --line2: #e4e8ea; --accent: #b8352a; --kerb-white: #ffffff;
  --now: #2a78d6; --now-soft: rgba(42, 120, 214, .18);
  --good: #1d7a44; --good-bg: #e2f1e7; --warn: #8f5e00; --warn-bg: #f8eed6; --bad: #a8261d; --bad-bg: #f8e2df;
  --neutral: #59606a; --neutral-bg: #e9ebed;
  --t-contact: #b8352a; --t-clip: #2a78d6; --t-turn: #6a5acd;
  --f-display: "Zen Kaku Gothic New", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  --f-body: "Noto Sans JP", "Hiragino Sans", "Yu Gothic", "Meiryo", sans-serif;
  --f-mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #101315; --surface: #171b1e; --ink: #eceff1; --ink2: #b8c0c6; --muted: #8a939a;
  --line: #2c3237; --line2: #22282c; --accent: #e3685c; --kerb-white: #e9ecef;
  --now: #4a92ea; --now-soft: rgba(74, 146, 234, .28);
  --good: #57c286; --good-bg: #15301f; --warn: #e3a94f; --warn-bg: #33270f; --bad: #f0857c; --bad-bg: #3a1a17;
  --neutral: #a6aeb6; --neutral-bg: #24292e;
  --t-contact: #e3685c; --t-clip: #4a92ea; --t-turn: #a597f0; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #101315; --surface: #171b1e; --ink: #eceff1; --ink2: #b8c0c6; --muted: #8a939a;
  --line: #2c3237; --line2: #22282c; --accent: #e3685c; --kerb-white: #e9ecef;
  --now: #4a92ea; --now-soft: rgba(74, 146, 234, .28);
  --good: #57c286; --good-bg: #15301f; --warn: #e3a94f; --warn-bg: #33270f; --bad: #f0857c; --bad-bg: #3a1a17;
  --neutral: #a6aeb6; --neutral-bg: #24292e;
  --t-contact: #e3685c; --t-clip: #4a92ea; --t-turn: #a597f0; color-scheme: dark; }
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { background: var(--bg); color: var(--ink); font-family: var(--f-body); font-size: 15px; line-height: 1.75; margin: 0; }
.wrap { max-width: 1200px; margin: 0 auto; padding-inline: 18px; padding-block: 28px 64px; }
h1, h2, h3 { font-family: var(--f-display); text-wrap: balance; line-height: 1.35; margin: 0; }
h1 { font-size: clamp(26px, 4.6vw, 38px); }
h2 { font-size: 22px; }
p { margin: 0; max-width: 72ch; }
.mono { font-family: var(--f-mono); font-variant-numeric: tabular-nums; }
.eyebrow { font-family: var(--f-mono); font-size: 12px; letter-spacing: .08em; color: var(--muted); }
.kerbrule { height: 6px; border-radius: 1px; margin-block: 14px 18px; max-width: 220px;
  background: repeating-linear-gradient(90deg, var(--accent) 0 22px, var(--kerb-white) 22px 44px); box-shadow: inset 0 0 0 1px var(--line); }
.lede { font-size: 17px; color: var(--ink2); margin-top: 10px; }
section { margin-top: 36px; display: grid; gap: 14px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px 18px; min-width: 0; }
.note { color: var(--muted); font-size: 13px; }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 700; padding: 1px 9px; border-radius: 999px; white-space: nowrap; }
.s-borderline { color: var(--warn); background: var(--warn-bg); }
.lic { margin-top: 16px; border: 1px solid var(--warn); background: var(--warn-bg); border-radius: 8px; padding: 10px 14px; font-size: 13.5px; color: var(--ink); }
.lic b { color: var(--warn); }
.settings { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-top: 16px; }
.settings > div { min-width: 0; border-top: 3px solid var(--accent); padding-top: 8px; }
.settings h3 { font-family: var(--f-body); font-size: 14.5px; font-weight: 700; margin-bottom: 4px; }
.settings p { font-size: 13.5px; color: var(--ink2); }
.settings p.caveat { margin-top: 6px; padding: 6px 9px; border-radius: 6px; background: var(--warn-bg); color: var(--ink); font-size: 13px; }
.settings p.caveat b { color: var(--warn); }
.settings .big { font-family: var(--f-mono); font-size: 17px; color: var(--ink); display: block; margin-bottom: 2px; }
.sumgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.sumtile { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; min-width: 0; display: grid; gap: 4px; align-content: start; }
.sn { font-size: 28px; line-height: 1.2; }
.sn small { font-size: 14px; color: var(--muted); font-family: var(--f-body); }
.ss { font-size: 12.5px; color: var(--ink2); }
.sw { font-size: 12.5px; color: var(--muted); margin-top: 6px; }
ol.worst { margin: 0; padding-left: 1.5em; display: grid; gap: 2px; font-size: 13.5px; }
ol.worst li span { margin-left: 8px; color: var(--ink2); font-size: 12.5px; white-space: nowrap; }
a { color: var(--now); }
a:focus-visible, button:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid var(--now); outline-offset: 2px; }
.signs { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.signs > div { min-width: 0; border-left: 3px solid var(--now); padding-left: 12px; font-size: 13.5px; color: var(--ink2); }
.signs b { color: var(--ink); }
details summary { cursor: pointer; font-size: 13px; color: var(--ink2); }
details[open] summary { margin-bottom: 8px; }
.legend img { width: 100%; max-width: 900px; height: auto; display: block; border-radius: 6px; }

/* toolbar */
.toolbar { margin-top: 36px; background: var(--bg); border-bottom: 1px solid var(--line); padding-block: 10px; z-index: 5; display: grid; gap: 8px; }
@media (min-width: 760px) { .toolbar { position: sticky; top: 0; } }
.filters { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; }
.filters label { display: grid; gap: 1px; font-size: 12px; color: var(--muted); min-width: 0; }
@media (max-width: 560px) {
  .filters { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .filters select { width: 100%; }
  .filters label:last-child { grid-column: 1 / -1; }
}
select { font: inherit; font-size: 14px; line-height: 1.4; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 5px 8px; max-width: 100%; }
.tbrow { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; font-size: 13px; color: var(--ink2); }
.tbrow .cnt b { font-family: var(--f-mono); color: var(--ink); }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; overflow: hidden; background: var(--surface); flex: none; }
.seg button { font: inherit; font-size: 13px; line-height: 1.4; padding: 4px 12px; background: transparent; color: var(--ink2); border: 0; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg); font-weight: 700; }
.linkbtn { font: inherit; font-size: 13px; color: var(--now); background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline; }

/* cards */
#cards { display: grid; gap: 18px; margin-top: 16px; }
.ev { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px 16px; display: grid; gap: 10px; min-width: 0; scroll-margin-top: 140px; }
.ev[hidden] { display: none; }
.ev.flash { outline: 3px solid var(--now); outline-offset: 2px; }
.ev:focus { outline: none; }
.ev.flash:focus { outline: 3px solid var(--now); }
.evhead { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; }
.evtitle { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; min-width: 0; }
.evtitle h3 { font-size: 17px; }
.kid { font-family: var(--f-body); font-size: 13px; font-weight: 400; color: var(--muted); }
.lt { font-size: 13.5px; color: var(--ink2); }
.tchip { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; padding: 1px 9px 1px 7px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.tchip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.t-contact { color: var(--t-contact); } .t-clip { color: var(--t-clip); } .t-turn { color: var(--t-turn); }
.vtwrap { display: flex; align-items: center; gap: 8px; }
.vtl { font-size: 12px; color: var(--muted); }
.evdef { font-size: 13px; color: var(--ink2); }
.figs { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
@media (max-width: 680px) { .figs { grid-template-columns: minmax(0, 1fr); } }
figure { margin: 0; min-width: 0; display: grid; gap: 4px; align-content: start; }
.imgbtn { display: block; position: relative; width: 100%; aspect-ratio: 16 / 9; padding: 0; border: 0; border-radius: 6px; overflow: hidden; background: var(--neutral-bg); cursor: zoom-in; }
.imgbtn img { display: block; width: 100%; height: 100%; object-fit: contain; }
.imgbtn.schem { background: #191c20; }
.imgbtn.schem img { object-position: center top; }
.imgbtn:hover img { filter: brightness(1.06); }
.guide { position: absolute; left: 0; right: 0; top: 38.333%; border-top: 2px dashed rgba(255, 214, 10, .9); pointer-events: none; }
.guide span { position: absolute; right: 6px; bottom: 2px; font-size: 10.5px; font-style: normal; line-height: 1.3; color: #111; background: rgba(255, 214, 10, .9); padding: 0 5px; border-radius: 3px; }
figcaption { font-size: 12.5px; color: var(--ink2); display: flex; flex-wrap: wrap; gap: 0 10px; line-height: 1.55; }
figcaption b { color: var(--ink); }
figcaption .mono { font-size: 12px; }
.ev[data-variant="fixed"] .only-raw, .ev[data-variant="raw"] .only-fixed { display: none; }
.ev[data-variant="raw"] .v-video { box-shadow: 0 0 0 3px var(--warn); }
.ev[data-variant="raw"] figure:first-child figcaption b { color: var(--warn); }
.nums { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px 14px; }
.nums > div { min-width: 0; border-left: 3px solid var(--now); padding-left: 10px; }
.nums > div.na { border-left-color: var(--line); }
.nums .k { font-size: 12px; color: var(--muted); }
.nums .v { font-family: var(--f-mono); font-size: 19px; line-height: 1.35; font-variant-numeric: tabular-nums; }
.nums .v small { font-size: 12.5px; color: var(--ink2); }
.nums .s { font-size: 12.5px; color: var(--ink2); line-height: 1.6; }
ul.cnotes { margin: 0; padding-left: 1.2em; font-size: 12.5px; color: var(--muted); display: grid; gap: 2px; }
.scroll { overflow-x: auto; }
table.det { border-collapse: collapse; width: 100%; font-size: 12.5px; }
table.det th, table.det td { text-align: left; vertical-align: top; padding: 5px 8px; border-bottom: 1px solid var(--line2); }
table.det th { color: var(--muted); font-weight: 500; width: 34%; min-width: 9em; }
table.det td { overflow-wrap: anywhere; }
.empty { padding: 24px; text-align: center; color: var(--ink2); }

/* lightbox */
dialog#lb { width: min(96vw, 1500px); max-width: 96vw; max-height: 96vh; padding: 0; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); }
dialog#lb::backdrop { background: rgba(8, 10, 12, .82); }
.lbbar { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--line); }
.lbbar p { font-size: 14px; font-weight: 700; max-width: none; min-width: 0; }
.lbbtns { display: flex; flex-wrap: wrap; gap: 6px; }
.lbbtns button { font: inherit; font-size: 13px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); cursor: pointer; }
.lbbtns button[hidden] { display: none; }
.lbimg { display: flex; justify-content: center; padding: 8px; background: #0c0e10; }
.lbwrap { position: relative; display: inline-block; max-width: 100%; }
.lbwrap img { display: block; max-width: 100%; height: auto; }
#lb-cap { font-size: 12.5px; color: var(--ink2); padding: 6px 12px 10px; max-width: none; }
footer { margin-top: 48px; font-size: 12.5px; color: var(--muted); display: grid; gap: 6px; }
footer code { font-family: var(--f-mono); font-size: 12px; overflow-wrap: anywhere; }
@media (max-width: 560px) {
  body { font-size: 14.5px; }
  .ev { padding: 12px 12px 14px; }
  .nums .v { font-size: 17px; }
  table.det th { width: 40%; min-width: 0; }
}
'''

JS = r'''
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const list = $('#cards');
  const cards = $$('.ev', list);
  const F = { date: $('#f-date'), lap: $('#f-lap'), kerb: $('#f-kerb'), type: $('#f-type') };
  const sortSel = $('#f-sort');
  const countEl = $('#count');
  const empty = $('#empty');

  const matches = (c, skip) => {
    for (const k in F) {
      if (k === skip) continue;
      const v = F[k].value;
      if (v !== 'all' && c.dataset[k] !== v) return false;
    }
    return true;
  };
  function updateOptionCounts() {
    for (const k in F) {
      const pool = cards.filter(c => matches(c, k));
      for (const o of F[k].options) {
        const n = o.value === 'all' ? pool.length : pool.filter(c => c.dataset[k] === o.value).length;
        o.textContent = o.dataset.label + '（' + n + '）';
        o.disabled = n === 0 && o.value !== 'all' && !o.selected;
      }
    }
  }
  const num = (c, k) => parseFloat(c.dataset[k]);
  const course = (a, b) => a.dataset.date.localeCompare(b.dataset.date) || num(a, 'lap') - num(b, 'lap') || num(a, 't') - num(b, 't') || num(a, 'tord') - num(b, 'tord');
  const sorters = {
    course,
    primary: (a, b) => num(a, 'tord') - num(b, 'tord') || num(b, 'primary') - num(a, 'primary'),
    along: (a, b) => num(b, 'along') - num(a, 'along'),
    lat: (a, b) => num(b, 'lat') - num(a, 'lat'),
  };
  function sortCards() {
    const s = sorters[sortSel.value] || course;
    cards.slice().sort((a, b) => s(a, b) || course(a, b)).forEach(c => list.appendChild(c));
  }
  function apply() {
    let n = 0;
    for (const c of cards) { const ok = matches(c); c.hidden = !ok; if (ok) n++; }
    countEl.textContent = String(n);
    empty.hidden = n !== 0;
    updateOptionCounts();
  }
  function resetFilters() { for (const k in F) F[k].value = 'all'; apply(); }
  for (const k in F) F[k].addEventListener('change', apply);
  sortSel.addEventListener('change', sortCards);
  $$('.resetf').forEach(b => b.addEventListener('click', resetFilters));

  // ---- corrected / uncorrected video frame
  function setVariant(card, v) {
    card.dataset.variant = v;
    const img = $('img.vid', card);
    const src = v === 'raw' ? img.dataset.raw : img.dataset.fixed;
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    $$('.vt button', card).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
  }
  const gv = $$('#gv button');
  gv.forEach(b => b.addEventListener('click', () => {
    gv.forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    cards.forEach(c => setVariant(c, b.dataset.v));
    if (lb.open) showLb();
  }));

  // ---- lightbox
  const lb = $('#lb'), lbImg = $('#lb-img'), lbTitle = $('#lb-title'), lbCap = $('#lb-cap');
  const lbVar = $('#lb-var'), lbGuide = $('.guide', lb);
  let lbCard = null, lbSlots = [], lbIdx = 0, opener = null;
  function captionText(btn) {
    const cap = btn.closest('figure').querySelector('figcaption').cloneNode(true);
    const hide = lbCard.dataset.variant === 'raw' ? '.only-fixed' : '.only-raw';
    cap.querySelectorAll(hide).forEach(n => n.remove());
    return Array.from(cap.children).map(n => n.textContent).join('　');
  }
  function showLb() {
    const btn = lbSlots[lbIdx];
    const img = $('img', btn);
    lbImg.src = img.getAttribute('src');
    lbImg.alt = img.alt;
    lbTitle.textContent = $('h3', lbCard).textContent + '　' + $('.tchip', lbCard).textContent + '　' + $('.lt', lbCard).textContent;
    lbCap.textContent = captionText(btn) + '（' + (lbIdx + 1) + '/' + lbSlots.length + '）';
    const isVid = btn.classList.contains('v-video');
    if (!isVid && document.activeElement === lbVar) $('#lb-close').focus();
    lbVar.hidden = !isVid;
    lbVar.textContent = lbCard.dataset.variant === 'raw' ? '補正後に切替' : '補正前に切替';
    lbGuide.hidden = !btn.classList.contains('has-guide');
    const gl = $('.guide span', btn);
    if (gl) $('span', lbGuide).textContent = gl.textContent;
  }
  function fitLb() {
    const nw = lbImg.naturalWidth, nh = lbImg.naturalHeight;
    const aw = $('.lbimg', lb).clientWidth - 16;
    const ah = window.innerHeight * 0.96 - 150;
    if (!nw || !nh || aw <= 0) return;
    lbImg.style.width = Math.max(120, Math.floor(Math.min(aw, ah * nw / nh))) + 'px';
  }
  lbImg.addEventListener('load', fitLb);
  window.addEventListener('resize', () => { if (lb.open) fitLb(); });
  function openLb(btn) {
    opener = btn;
    lbCard = btn.closest('.ev');
    lbSlots = $$('.imgbtn', lbCard);
    lbIdx = lbSlots.indexOf(btn);
    showLb();
    if (typeof lb.showModal === 'function') lb.showModal(); else lb.setAttribute('open', '');
    fitLb();
    $('#lb-close').focus();
  }
  function closeLb() { if (lb.open) lb.close(); }
  lb.addEventListener('close', () => { if (opener) opener.focus({ preventScroll: true }); });
  lb.addEventListener('click', e => { if (e.target === lb) closeLb(); });
  document.addEventListener('keydown', e => {
    if (!lb.open) return;
    if (e.key === 'ArrowRight') { lbIdx = (lbIdx + 1) % lbSlots.length; showLb(); e.preventDefault(); }
    else if (e.key === 'ArrowLeft') { lbIdx = (lbIdx + lbSlots.length - 1) % lbSlots.length; showLb(); e.preventDefault(); }
    else if (e.key === 'Escape') { closeLb(); e.preventDefault(); }
  });
  $('#lb-close').addEventListener('click', closeLb);
  $('#lb-next').addEventListener('click', () => { lbIdx = (lbIdx + 1) % lbSlots.length; showLb(); });
  $('#lb-prev').addEventListener('click', () => { lbIdx = (lbIdx + lbSlots.length - 1) % lbSlots.length; showLb(); });
  lbVar.addEventListener('click', () => { setVariant(lbCard, lbCard.dataset.variant === 'raw' ? 'fixed' : 'raw'); showLb(); });

  list.addEventListener('click', e => {
    const b = e.target.closest('.vt button');
    if (b) { setVariant(b.closest('.ev'), b.dataset.v); return; }
    const ib = e.target.closest('.imgbtn');
    if (ib) openLb(ib);
  });

  // ---- jump links (summary) and #hash
  function reveal(id) {
    const card = id && document.getElementById(id);
    if (!card || !card.classList.contains('ev')) return;
    if (card.hidden) resetFilters();
    card.scrollIntoView({ block: 'start' });
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 1800);
    card.focus({ preventScroll: true });
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a.jump');
    if (!a) return;
    e.preventDefault();
    const id = a.getAttribute('href').slice(1);
    try { history.replaceState(null, '', '#' + id); } catch (_) { /* file:// may refuse */ }
    reveal(id);
  });
  window.addEventListener('hashchange', () => reveal(decodeURIComponent(location.hash.slice(1))));

  apply();
  sortCards();
  if (location.hash) reveal(decodeURIComponent(location.hash.slice(1)));
})();
'''

page = f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>実映像と3DCGの比較</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+JP:wght@400;500;700&family=Zen+Kaku+Gothic+New:wght@700&display=swap">
<style>
/* Local-only page: real onboard frames next to the 3DCG at the 2026-10-22 truth events.
   Images are referenced by relative path (never embedded). Tokens shared with
   docs/fuji-event-truth-evaluation-2026-10-22.html. Generated by
   artifacts/event-comparison-2026-10-22/scripts/build_page.py */
{CSS}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">ReplayViewer · Fuji Speedway · 2020-07-29 ウェット / 07-30 ドライ · 2026-10-22 · ローカル専用</div>
  <h1>実際の映像と3DCGを並べて見る</h1>
  <div class="kerbrule" aria-hidden="true"></div>
  <p class="lede">10/22の検証で正解とした瞬間（縁石に乗った瞬間・クリッピングポイント・ステアの切り始め）ごとに、車載動画のコマと、同じ時刻の3DCG（車載カメラ・追従カメラ）、上から見た図を並べました。</p>
  <p class="lic"><b>動画の画像はローカル参照のみ・公開しない。</b>車載動画は再配布の許諾がないため、このページと画像フォルダ（<span class="mono">artifacts/event-comparison-2026-10-22/</span>）はgitの管理対象外にしてあり、Artifactにも載せません。画像はこのページに埋め込まず、同じフォルダから読み込んでいます。</p>
  <div class="settings">
    <div><h3>3DCGのカメラ</h3><p><span class="big">推定カメラ f 711 px・高さ1.25 m</span>焦点距離は仕様の画角84°に合わせました。下向きと左右の向きは、車の真正面が動画の消失点（本コース直線の白線の交点）に来るように合わせ、7/29は{AIM29}、7/30は{AIM30}です（評価資料でクリッピングポイントを測ったカメラと同じ向き）。傾き・取付位置は10/03の推定値のままです。作業ツリーの未コミット機能（車載カメラ）で撮影しました。レンズの校正はしていないので、画面の端ほどゆがみの分だけ動画とずれます。</p><p class="caveat"><b>見方：</b>動画の下側はダッシュボードで隠れていますが、3DCGには路面が写っています。クリッピングポイントでは、両方に引いた黄色の点線（同じ276行目）の上で、縁石の縁がどこにあるかを見比べてください。</p></div>
    <div><h3>動画の時刻補正</h3><p><span class="big">動画は周ごとに約0.21秒補正</span>動画のLAP表示で合わせた時刻は、映像の中身より遅れています。7/30は映像の回転とジャイロから周ごとに測った0.14〜0.27秒、7/29は推定の0.17〜0.23秒（妥当な幅は0.17〜0.30秒）を足しています。補正前（LAP表示合わせ）の画像は約0.2秒あと、100 km/hなら約6 m先の映像です。</p></div>
    <div><h3>3DCGの時刻と位置</h3><p><span class="big">正解の時刻ちょうどを描画</span>公開版と同じGPSの補正（標準）で、ビューアの再生時刻を正解の時刻に合わせて描きました。動画のコマは最も近いコマです（差は1/60秒以内）。上から見た図の縁石の線は2019年のオルソ画像から引いたもので、測量値ではありません。</p></div>
  </div>
</header>

<section aria-labelledby="h-sum">
  <h2 id="h-sum">要旨</h2>
  <p>正解の瞬間{plan['total']}件（縁石に乗った瞬間 {c['kerbContact']}件・クリッピングポイント {c['clipping']}件・切り始め {c['turnIn']}件）を比べられます。上の切替で動画を「補正前」（以前の比較資料と同じLAP表示合わせ）にできます。ただし1コマずつの見比べでは、補正前後の差（3〜7 m）は3DCG自身の位置の誤差やカメラの不確かさと同じくらいで、はっきり分かる場面は一部です。別の担当が画像で照合したところ、7/30のクリッピングポイントでは{SC['1.25']['n']}件中{SC['1.25']['fixedCloser']}件で補正後の方が3DCGに近くなりました。ただしこれは、クリッピングの時刻が合うように選んだカメラの尺度（f×h≈890）を前提にした整合性の確認です。補正の向きの独立した裏付けは、縁石の音とロガーの振動、映像の回転とジャイロの突き合わせ（評価資料）にあります。尺度を変えて撮り直しても、補正後の方が近いのは{SC['0.83']['n']}件中{SC['0.83']['fixedCloser']}件（f×h≈590）・{SC['1.70']['fixedCloser']}件（f×h≈1210）で、多数は変わりませんでした。補正の大きさ（0.21秒）は画像だけでは決まりません（補正後より同じだけ早いコマでも、同じくらいよく一致します）。ずれの大きい瞬間は下のリンクから開けます。</p>
  <div class="sumgrid">
{chr(10).join(tiles)}
  </div>
  <p class="note">典型＝ずれの大きさの中央値。このページの件数から計算しています（切り始めは2周分だけなので、評価資料の全周の集計とは少し違います）。コカ・コーラ（k1）の縁石に乗った瞬間は、縁石の先端の形のため横のずれが大きく出ます。</p>
</section>

<section aria-labelledby="h-read">
  <h2 id="h-read">数字の読み方</h2>
  <div class="card signs">
    <div><b>前後のずれ（m・秒）</b><br>＋は「同じ瞬間に3DCGの車が本物より前（先）にいる」、−は「後ろ」。秒は「3DCGでその出来事が起きる時刻 − 本当に起きた時刻」で、−なら3DCGの方が早い。m は 秒×速度 です。</div>
    <div><b>横のずれ（m）</b><br>縁石に乗った瞬間：3DCGのタイヤ外側と縁石の線（判定に使うブロックの線）の距離。本当は0。＋は「3DCGではまだ乗っていない」、−は「すでに入っている」。<br>クリッピングポイント：縁石までの横の距離の差。＋は「3DCGの方が縁石から遠い」。カメラの仮定に左右される目安です。切り始めには横の正解がありません。</div>
    <div><b>補正後／補正前</b><br>補正後＝本物の車が正解の時刻にいるコマ。補正前＝動画のLAP表示どおりの時刻のコマで、本物は約0.2秒あとの位置（以前の比較資料と同じ見え方）。クリッピングポイントの動画の黄色の点線は、正解を決めた画面の高さです。3DCGの車載カメラにも同じ高さの線を引いています（下向きと左右の向きを動画の消失点に合わせたため比べられます。レンズのゆがみは校正していないので、画面の端ほど少しずれます）。</div>
  </div>
  <details class="legend"><summary>上から見た図の凡例</summary><img src="{esc(REL)}schematic/legend_top.png" width="900" height="90" loading="lazy" decoding="async" alt="上から見た図の凡例：青＝3DCGの車、黄＝タイヤの接地面、青線＝3DCGの軌跡（前後2秒）、橙の点線＝推定の本物、赤＝縁石ブロック、白＝縁石の白線"></details>
</section>

<div class="toolbar" role="region" aria-label="絞り込みと表示の切替">
  <div class="filters">
    <label>日<select id="f-date">{f_date}</select></label>
    <label>周<select id="f-lap">{f_lap}</select></label>
    <label>縁石<select id="f-kerb">{f_kerb}</select></label>
    <label>瞬間の種類<select id="f-type">{f_type}</select></label>
    <label>並び順<select id="f-sort">{f_sort}</select></label>
  </div>
  <div class="tbrow">
    <span>動画（全部）</span>
    <div class="seg" id="gv" role="group" aria-label="すべての動画の時刻"><button type="button" data-v="fixed" aria-pressed="true">補正後</button><button type="button" data-v="raw" aria-pressed="false">補正前（LAP表示合わせ）</button></div>
    <span class="cnt">表示中 <b id="count">{plan['total']}</b> / {plan['total']}件</span>
    <button type="button" class="linkbtn resetf">絞り込みを解除</button>
  </div>
</div>

<div id="cards">
{cards_html}
</div>
<p class="empty" id="empty" hidden>条件に合う瞬間はありません。<button type="button" class="linkbtn resetf">絞り込みを解除</button></p>

<footer>
  <div>周の番号はロガーの周番号です（動画のLAP表示は＋1）。動画のコマはffmpegの時刻で取り出し、6コマでLAP表示を目で読んで、予想との差が表示の更新間隔（約0.05秒）以内であることを確かめました。これは動画の時刻とLAP表示の対応の確認で、時刻補正の値そのものの確認ではありません。</div>
  <div>元データ：<code>artifacts/event-comparison-2026-10-22/events.json</code>（正解の時刻・ずれの値。元は <code>artifacts/kerb-event-truth-2026-10-22/join/event_table.json</code>）、<code>frames/manifest.json</code>、3DCGの車載カメラ <code>{ONBOARD_MANIFEST}</code>（{ONB_RUN}撮影、<code>scripts/capture_cg_vpfix.mjs</code>）、3DCGの追従カメラ <code>{CHASE_MANIFEST}</code>（{CHASE_RUN}撮影、追従カメラの分だけ使用）、<code>schematic/schematics.json</code>、カメラ尺度を変えた画像照合 <code>verify/scale_sensitivity.json</code>。作成：<code>scripts/build_page.py</code>（{generated}）。</div>
  <div>詳しい検証結果は <a href="fuji-event-truth-evaluation-2026-10-22.html">動画・Gを正解にした3DCG検証</a> を見てください。</div>
</footer>
</div>

<dialog id="lb" aria-labelledby="lb-title">
  <div class="lbbar"><p id="lb-title"></p><div class="lbbtns"><button type="button" id="lb-var" hidden>補正前に切替</button><button type="button" id="lb-prev" aria-label="前の画像">← 前</button><button type="button" id="lb-next" aria-label="次の画像">次 →</button><button type="button" id="lb-close">閉じる（Esc）</button></div></div>
  <div class="lbimg"><span class="lbwrap"><img id="lb-img" alt=""><i class="guide" aria-hidden="true" hidden><span>判定の高さ（動画の276行目）</span></i></span></div>
  <p id="lb-cap"></p>
</dialog>
<script>
{JS}
</script>
</body>
</html>
'''

OUT.write_text(page, encoding='utf-8', newline='\n')
print(f'wrote {OUT} ({len(page.encode("utf-8"))} bytes, {len(EV)} cards, {len(EV) * 4} figures)')
for ty, evs, by, kind, stats, worst in summary:
    print(ty, len(evs), stats, [e['id'] for e in worst])
