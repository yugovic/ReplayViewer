"""HTML report for task D (opt-in kerb-contact registration candidate, BACKLOG GPS-REG-15).

Reads evaluation.json (eval_kerb_registration.py) and the candidate
public/data/races/fuji_aim_2020_07_30/gps_registration_kerb.json and writes a self-contained
report.html (Japanese) next to the evaluation. All numbers in the text come from those files.

  python scripts/quality/apex_kpi/report_kerb_registration.py [--dir DIR]
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_DIR = ROOT / 'artifacts/improvement-eval-2026-10-02/after/registration-candidate'
CANDIDATE = ROOT / 'public/data/races/fuji_aim_2020_07_30/gps_registration_kerb.json'

METHOD_JA = {
    'raw': '元GPS（補正なし）',
    'shipped': '現行の配信値（道路境界・周ごと）',
    'trackOnlySession': '道路境界のみ・セッション1つ（新しい縁石区分）',
    'trackOnlySessionPlusLap': '道路境界のみ・セッション＋周の小さなずれ',
    'candidateInSample (not held out)': '候補（学習に使ったラベルで採点・参考）',
    'candidateLOKO': '候補・縁石を1つずつ外す交差検証',
    'candidateLOKO_sessionOnly': '候補・縁石外し（セッション値のみ）',
    'candidateLOLO': '候補・周を1つずつ外す交差検証',
    'candidateLOLO_sessionOnly': '候補・周外し（セッション値のみ）',
}


def esc(v) -> str:
    return html.escape(str(v))


def f2(v, nd=2, sign=False):
    if v is None:
        return '—'
    return f'{v:+.{nd}f}' if sign else f'{v:.{nd}f}'


def ci_text(ci, nd=0):
    return f'[{ci[0]:+.{nd}f}, {ci[1]:+.{nd}f}]'


def excludes_zero(ci) -> bool:
    return ci[0] > 0 or ci[1] < 0


def table(headers, rows, cls='') -> str:
    h = ''.join(f'<th>{esc(x)}</th>' for x in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>'


def contra_list(m) -> str:
    if not m['contradictions']:
        return '—'
    return '、'.join(f'L{c[0]} {c[1]} {"強" if c[2] == "strong" else "無"} {c[3]:+.2f}' for c in m['contradictions'])


def build(ev: dict, cand: dict) -> str:
    H = ev['heldOut']
    bs = ev['bootstrapByKerb']
    b_ship = bs['candidateLOKO_minus_shipped']['allLabels']
    b_ship_na = bs['candidateLOKO_minus_shipped']['withoutAnomalyLabels']
    b_trk = bs['candidateLOKO_minus_trackOnlySession']['allLabels']
    loko, lolo, ship = H['candidateLOKO']['allLabels'], H['candidateLOLO']['allLabels'], H['shipped']['allLabels']
    trk, raw = H['trackOnlySession']['allLabels'], H['raw']['allLabels']
    loko_na, lolo_na, ship_na = (H['candidateLOKO']['withoutAnomalyLabels'], H['candidateLOLO']['withoutAnomalyLabels'],
                                 H['shipped']['withoutAnomalyLabels'])
    tol = ev['toleranceSensitivity']
    cf = ev['courseFixShippedRegistration']
    cg, v3 = cf['cgKerbLines']['allLabels'], cf['kerbZonesV3']['allLabels']
    fr = ev['feasibleRegionSensitivity']
    dflt = fr['combos']['strongMax0_silentMin-0.4']
    sess_t15 = dflt['tol0.15']['session']
    bl = ev['beyondLimit']
    sign = ev['trackOnlySignArtifact']
    wet = ev['wet0729']
    so = ev['sessionOffsets']
    sig_ship_c = excludes_zero(b_ship['contradictionDiffCI95'])
    sig_ship_a = excludes_zero(b_ship['aucDiffCI95'])
    sig_trk_c = excludes_zero(b_trk['contradictionDiffCI95'])
    sig_trk_a = excludes_zero(b_trk['aucDiffCI95'])
    tol_rows = [(k.replace('tol', ''), v['candidateLOKO']['all'], v['shipped']['all']) for k, v in tol.items()]
    worse_tols = [t for t, c, s in tol_rows if c > s]

    verdict = (
        f'<p><b>判定：道路境界だけの推定よりは明確に良くなったが、現行の配信値に対する改善は小さく、確定的ではない。'
        f'現行の置き換えはせず、<code>?gps=kerb</code>の選択式候補にとどめるのが妥当。</b></p>'
        '<ul>'
        f'<li>縁石を1つずつ外す交差検証（59ラベル）：矛盾 {loko["contradictionCount"]}件・AUC {f2(loko["auc"], 3)}。'
        f'現行の配信値は {ship["contradictionCount"]}件・{f2(ship["auc"], 3)}。'
        f'縁石単位ブートストラップの差：矛盾 {b_ship["contradictionDiff"]:+d}件 {ci_text(b_ship["contradictionDiffCI95"])}'
        f'（{"有意" if sig_ship_c else "有意でない"}）、AUC {b_ship["aucDiff"]:+.3f} {ci_text(b_ship["aucDiffCI95"], 3)}'
        f'（{"0を含まない" if sig_ship_a else "0を含む"}）。</li>'
        f'<li>周を1つずつ外す交差検証：矛盾 {lolo["contradictionCount"]}件・AUC {f2(lolo["auc"], 3)}。'
        f'矛盾数は現行（{ship["contradictionCount"]}件）より<b>多い</b>。L5は自分の周のラベルがないと合わない（周ごとの誤差が残る）。</li>'
        f'<li>道路境界のみ・セッション1つ（新しい縁石区分）との差：矛盾 {b_trk["contradictionDiff"]:+d}件 '
        f'{ci_text(b_trk["contradictionDiffCI95"])}、AUC {b_trk["aucDiff"]:+.3f} {ci_text(b_trk["aucDiffCI95"], 3)}'
        f'（{"どちらも有意" if (sig_trk_c and sig_trk_a) else "一部のみ有意"}）。</li>'
        f'<li>許容幅による逆転：許容幅 {", ".join(worse_tols) if worse_tols else "なし"} m では候補の矛盾数が現行より多い。'
        '改善は許容幅0.15 mの採点に依存する。</li>'
        f'<li>コースの修正だけ（現行の配信値のまま縁石線を差し替え）：矛盾 {cg["contradictionCount"]}→{v3["contradictionCount"]}件、'
        f'AUC {f2(cg["auc"], 3)}→{f2(v3["auc"], 3)}。ダンロップ右の{cf["cgKerbLines"]["allLabels"]["unmeasurable"]}件が測れるようになった（どちらも整合）。'
        '矛盾数は変わらない。</li>'
        f'<li>道路境界のはみ出し（現行と同じ測り方）：候補 {bl["candidate"]["total"]["secondsBeyondLimit"]:.1f} 秒・最大 '
        f'{bl["candidate"]["total"]["maxBeyondLimitMeters"]:.2f} m、現行 {bl["shipped"]["total"]["secondsBeyondLimit"]:.1f} 秒・'
        f'{bl["shipped"]["total"]["maxBeyondLimitMeters"]:.2f} m。候補は道路境界への当てはまりを少し譲る。</li>'
        f'<li>7/29は強い接触が{wet["strongLabels"]}件しかないため候補を作らない（スクリプトが拒否する）。</li>'
        '</ul>')

    # held-out table
    order = ['raw', 'shipped', 'trackOnlySession', 'trackOnlySessionPlusLap', 'candidateLOKO', 'candidateLOKO_sessionOnly',
             'candidateLOLO', 'candidateLOLO_sessionOnly', 'candidateInSample (not held out)']
    rows = []
    for k in order:
        a, b = H[k]['allLabels'], H[k]['withoutAnomalyLabels']
        rows.append([esc(METHOD_JA.get(k, k)), f'{a["contradictionCount"]}', f2(a['auc'], 3), f2(a['withinKerbAuc'], 3),
                     f'{b["contradictionCount"]}', f2(b['auc'], 3), esc(contra_list(a))])
    held_table = table(['方式', '矛盾（59件）', 'AUC', '同じ縁石内AUC', '矛盾（異常区間を除く55件）', 'AUC（55件）', '矛盾の内訳（周・縁石・強/無・g m）'],
                       rows, 'num')

    # bootstrap table
    brows = []
    for key, name in (('candidateLOKO_minus_shipped', '候補（縁石外し）− 現行'),
                      ('candidateLOKO_minus_trackOnlySession', '候補（縁石外し）− 道路境界のみ（セッション）'),
                      ('candidateLOKO_minus_trackOnlySessionPlusLap', '候補（縁石外し）− 道路境界のみ（＋周）'),
                      ('candidateLOKO_minus_raw', '候補（縁石外し）− 元GPS')):
        for sk, sname in (('allLabels', '59件'), ('withoutAnomalyLabels', '55件')):
            if sk not in bs[key]:
                continue
            b = bs[key][sk]
            brows.append([esc(name), sname, f'{b["contradictionDiff"]:+d}', ci_text(b['contradictionDiffCI95']),
                          f'{b["pContradictionDiffGE0"]:.3f}', f'{b["aucDiff"]:+.3f}', ci_text(b['aucDiffCI95'], 3),
                          f'{b["pAucDiffLE0"]:.3f}'])
    boot_table = table(['比較', '採点', '矛盾の差', '95%区間', 'P(差≥0)', 'AUCの差', '95%区間', 'P(差≤0)'], brows, 'num')

    # anomaly treatment
    arows = []
    for k, name in (('exclude', '除外（採用）'), ('anomalyDownweight', '重み0.25'), ('anomalyInclude', 'そのまま含める')):
        v = ev['anomalyTreatment'][k]
        arows.append([name, f'({v["session"][0]:+.3f}, {v["session"][1]:+.3f})',
                      f'{v["laps"]["2"][0]:+.3f}, {v["laps"]["2"][1]:+.3f}',
                      f'{v["LOKO"]["allLabels"]["contradictionCount"]}', f2(v['LOKO']['allLabels']['auc'], 3),
                      f'{v["LOKO"]["withoutAnomalyLabels"]["contradictionCount"]}',
                      f'{v["LOLO"]["allLabels"]["contradictionCount"]}', esc(contra_list(v['LOKO']['allLabels']))])
    anom_table = table(['L2終盤ラベルの扱い', 'セッション補正 (東, 南) m', 'L2の補正 m', '縁石外し 矛盾（59件）', 'AUC',
                        '矛盾（55件）', '周外し 矛盾', '縁石外しの矛盾内訳'], arows, 'num')

    # sensitivity
    srows = []
    for k, v in ev['sensitivity'].items():
        p = v['params']
        srows.append([esc(k), esc(p['loss']), f'{p["label_weight"]:g}', f'{p["strong_max_gap"]:g}', f'{p["silent_min_gap"]:g}',
                      f'{p["sigma_lap"]:g}', f'({v["session"][0]:+.3f}, {v["session"][1]:+.3f})', f2(v['maxLapShiftVsDefaultM'], 3),
                      f'{v["LOKO"]["allLabels"]["contradictionCount"]}', f2(v['LOKO']['allLabels']['auc'], 3),
                      f'{v["LOKO"]["withoutAnomalyLabels"]["contradictionCount"]}'])
    sens_table = table(['設定', '損失', '重み', '強の上限 m', '無の下限 m', 'σ周 m', 'セッション補正', '既定からの最大移動 m',
                        '縁石外し 矛盾（59件）', 'AUC', '矛盾（55件）'], srows, 'num')
    trows = [[t, str(v['candidateLOKO']['all']), str(v['candidateLOLO']['all']), str(v['shipped']['all']),
              str(v['trackOnlySession']['all']), str(v['raw']['all']),
              str(v['candidateLOKO']['withoutAnomaly']), str(v['shipped']['withoutAnomaly'])]
             for t, v in ((k.replace('tol', ''), v) for k, v in tol.items())]
    tol_table = table(['許容幅 m', '候補 縁石外し', '候補 周外し', '現行', '道路境界のみ', '元GPS', '候補 縁石外し（55件）', '現行（55件）'],
                      trows, 'num')

    # feasible widths
    def wtxt(r):
        if r.get('empty'):
            return '空'
        w = f'{r.get("majorWidthM", r["eastWidthM"]):.2f}×{r.get("minorWidthM", r["southWidthM"]):.2f}'
        return w + ('（窓の端まで＝下限）' if r.get('touchesWindow') else '')
    frows = []
    for key, e in fr['combos'].items():
        for t in ('tol0', 'tol0.15'):
            x = e[t]
            frows.append([esc(key.replace('strongMax', '強≤').replace('_silentMin', '・無≥')), t.replace('tol', ''),
                          wtxt(x['session']), wtxt(x['sessionWithAnomalyLabels']), wtxt(x['sessionAndTrack']),
                          ' / '.join(wtxt(x['laps'][n]).replace('（窓の端まで＝下限）', '+') for n in sorted(x['laps']))])
    feas_table = table(['しきい値 m', '許容 m', 'セッション（異常区間除く）', 'セッション（異常区間含む）', 'セッション∩道路境界',
                        '周ごと L1/L2/…/L6（+＝窓の端まで）'], frows, 'num small')

    cfr = cand.get('feasibleRegion') or {}
    lap_feas = []
    for n, e in (cfr.get('laps') or {}).items():
        lap_feas.append([f'L{n}', wtxt(e['trackNearOptimal']), wtxt(e['labelFeasible_tol0']), wtxt(e['trackAndLabel_tol0']),
                         wtxt(e['labelFeasible_tol0.15']), wtxt(e['trackAndLabel_tol0.15'])])
    if cfr.get('session'):
        e = cfr['session']
        lap_feas.insert(0, ['セッション', wtxt(e['trackNearOptimal']), wtxt(e['labelFeasible_tol0']), wtxt(e['trackAndLabel_tol0']),
                            wtxt(e['labelFeasible_tol0.15']), wtxt(e['trackAndLabel_tol0.15'])])
    cand_feas_table = table(['', '道路境界のほぼ最適', 'ラベル可能域（許容0）', '両方（許容0）', 'ラベル可能域（許容0.15）', '両方（許容0.15）'],
                            lap_feas, 'num')

    # course fix
    crows = []
    for k, name in (('cgKerbLines', '旧CGの縁石線（ダンロップ右なし）'), ('courseV2', '第3版の縁石区分 v2（ダンロップ右は仮定）'),
                    ('kerbZonesV3', '新しい縁石区分 v3（検出値）')):
        v = cf.get(k)
        if not isinstance(v, dict):
            crows.append([esc(name), '—', '—', '—', '—', '—', esc(v)])
            continue
        a, b = v['allLabels'], v['withoutAnomalyLabels']
        crows.append([esc(name), f'{a["n"]}', f'{a["unmeasurable"]}', f'{a["contradictionCount"]}', f2(a['auc'], 3),
                      f'{b["contradictionCount"]} / {f2(b["auc"], 3)}', f2(a['strongMedianG'], 2)])
    course_table = table(['縁石線', '測れたラベル', '測れない', '矛盾', 'AUC', '矛盾/AUC（55件）', '強い接触のg中央値 m'], crows, 'num')
    ch = cf['gMinChangeV3MinusCg']
    big = sorted(ch.items(), key=lambda kv: -abs(kv[1]['median']))[:4]
    course_note = '、'.join(f'{k} {v["median"]:+.2f} m' for k, v in big)

    # beyond limit
    lrows = []
    for k, name in (('raw', '元GPS'), ('shipped', '現行'), ('trackOnlySession', '道路境界のみ（セッション）'),
                    ('trackOnlySessionPlusLap', '道路境界のみ（＋周）'), ('candidate', '候補')):
        v = bl[k]
        lrows.append([name] + [f'{v[str(n)]["secondsBeyondLimit"]:.1f}' for n in range(1, 7)] +
                     [f'{v["total"]["secondsBeyondLimit"]:.1f}', f'{v["total"]["maxBeyondLimitMeters"]:.2f}'])
    beyond_table = table(['', 'L1 秒', 'L2', 'L3', 'L4', 'L5', 'L6', '合計 秒', '最大 m'], lrows, 'num')

    # anomaly evidence
    ae = ev['anomalyEvidence']
    l2_dev = [p for p in ae['passes'] if p['lap'] == 2 and p['kerb'] in ('k21', 'k9', 'k10', 'k23', 'k24')]
    l2_txt = '、'.join(f'{p["kerb"]} ステア{p.get("steerDevFromOtherLapsS", 0):+.2f}s/横G{p.get("accyDevFromOtherLapsS", 0):+.2f}s'
                      for p in l2_dev)
    dev_laps = sorted({d['lap'] for d in ae['passesDeviating']})

    # labels used
    lab_rows = []
    for l in cand['labelsUsed']:
        lab_rows.append([f'L{l["lap"]}', esc(l['kerb']), '強' if l['class'] == 'strong' else '無', f2(l.get('peakRollRms'), 2),
                         '使用' if l['used'] else '除外', f2(l.get('gBlockMinAtFit'), 2, True), esc(l.get('reason') or '')])
    labels_table = table(['周', '縁石', '分類', 'ピーク', 'あてはめ', '候補でのg m', '理由'], lab_rows, 'num small')

    lap_rows = []
    ship_off = ev['offsets']['shipped']
    for l in cand['laps']:
        n = str(l['lap'])
        lap_rows.append([f'L{n}', f'({l["offsetMeters"][0]:+.3f}, {l["offsetMeters"][1]:+.3f})',
                         f'({ship_off[n][0]:+.3f}, {ship_off[n][1]:+.3f})',
                         f'{((l["offsetMeters"][0] - ship_off[n][0]) ** 2 + (l["offsetMeters"][1] - ship_off[n][1]) ** 2) ** 0.5:.2f}',
                         f'{l["labelsUsed"]["strong"]}/{l["labelsUsed"]["silent"]}', esc('、'.join(l['flags']) or '—')])
    lap_table = table(['周', '候補 (東, 南) m', '現行 (東, 南) m', '差 m', '使ったラベル 強/無', '警告'], lap_rows, 'num')

    ls = cand['labelSummary']
    used_txt = f'強い接触{ls["strongUsed"]}件・振動なし{ls["silentUsed"]}件'
    at = ev['anomalyTreatment']
    best = min(at.items(), key=lambda kv: kv[1]['LOKO']['allLabels']['contradictionCount'])[0]
    best_ja = {'exclude': '除外', 'anomalyDownweight': '重み0.25', 'anomalyInclude': 'そのまま含める'}[best]
    sess = [v['session'] for v in at.values()]
    spread = max(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a in sess for b in sess)
    t07 = tol['tol0.7']
    tol07 = (f'許容幅0.7 mでは候補（縁石外し）{t07["candidateLOKO"]["all"]}件・現行{t07["shipped"]["all"]}件・'
             f'道路境界のみ{t07["trackOnlySession"]["all"]}件・元GPS{t07["raw"]["all"]}件で、補正どうしの差はほぼなくなる')
    chg = cf['labelVerdictChangesCgToV3']
    course_changes = ('、'.join(f'L{c["lap"]} {c["kerb"]}（{"測れない" if c["contradictionCg"] is None else ("矛盾" if c["contradictionCg"] else "整合")}'
                               f'→{"矛盾" if c["contradictionV3"] else "整合"}）' for c in chg) or 'なし')
    lapf = (cand.get('feasibleRegion') or {}).get('laps', {})
    ok_laps = [n for n, e in lapf.items() if not e['trackAndLabel_tol0'].get('empty')]
    tiny = [f'L{n}' for n, e in lapf.items() if not e['trackAndLabel_tol0'].get('empty') and e['trackAndLabel_tol0']['points'] < 3]
    lapfeas_txt = (f'周ごとに見ると、道路境界とラベル（許容0）の両方を満たす範囲は{len(ok_laps)}/{len(lapf)}周にある'
                   + (f'（{"・".join(tiny)}はほぼ1点）' if tiny else ''))
    sess_trk_empty = all(e[t]['sessionAndTrack'].get('empty') for e in fr['combos'].values() for t in ('tol0', 'tol0.15'))
    sesstrk_txt = ('道路境界のほぼ最適な範囲とは、どのしきい値・許容幅でも重ならない' if sess_trk_empty
                   else '道路境界のほぼ最適な範囲と一部の設定で重なる')
    rl_rows = []
    sens = ev['sensitivity']
    for name, v, loss in (('除外・Huber（採用）', at['exclude'], 'huber'), ('含める・Huber', at['anomalyInclude'], 'huber'),
                          ('含める・soft-L1', sens.get('anomalyInclude_loss_soft_l1'), 'soft_l1'),
                          ('含める・二乗', sens.get('anomalyInclude_loss_squared'), 'squared')):
        if not v:
            continue
        l2 = v['laps']['2']
        rl_rows.append([name, f'({v["session"][0]:+.3f}, {v["session"][1]:+.3f})', f'({l2[0]:+.3f}, {l2[1]:+.3f})',
                        f'{v["LOKO"]["allLabels"]["contradictionCount"]}', f2(v['LOKO']['allLabels']['auc'], 3),
                        f'{v["LOKO"]["withoutAnomalyLabels"]["contradictionCount"]}', esc(contra_list(v['LOKO']['allLabels']))])
    robust_table = table(['L2終盤ラベル・損失', 'セッション補正 m', 'L2の補正 m', '縁石外し 矛盾（59件）', 'AUC', '矛盾（55件）', '内訳'],
                         rl_rows, 'num')
    pr = sign.get('probes', {})
    probe_ja = {'v3ReportTrackOnlyA': '第3版の道路境界のみ推定 A', 'v3ReportTrackOnlyB': '第3版の道路境界のみ推定 B',
                'v3ReportLabelFit': '第3版のラベル込み推定', 'shippedSession': '現行のセッション値', 'east+0.4_south-2.9': '格子点 (+0.4, −2.9)'}
    probe_rows = [[esc(probe_ja.get(k, k)), f'({v["offset"][0]:+.3f}, {v["offset"][1]:+.3f})', str(v['spuriousSamples']),
                   f2(v['worstBaseGapM'], 2, True), f2(v['baseCost'], 2), f2(v['robustCost'], 2)] for k, v in pr.items()]
    probe_table = table(['補正値', '(東, 南) m', '誤判定の標本数', '従来の符号での最悪のg m', '道路境界コスト（従来）', '同（改めた符号）'],
                        probe_rows, 'num')
    lolo_l5 = ev['loloSessionOffsets'].get('5')
    lolo_laps = sorted({c[0] for c in H['candidateLOLO']['withoutAnomalyLabels']['contradictions']})
    lolo_txt = (f'周を外す交差検証で異常区間以外の矛盾が出たのは{"・".join(f"L{n}" for n in lolo_laps) or "なし"}。'
                + (f'L5を外すとセッション補正は ({lolo_l5[0]:+.3f}, {lolo_l5[1]:+.3f}) m になり、候補（全周）より南へ'
                   f'{abs(lolo_l5[1] - so["candidate"][1]):.2f} m 動く。L5のラベルがセッション値を北へ引いている。' if lolo_l5 else ''))
    lu = {(l['lap'], l['kerb']): l for l in cand['labelsUsed']}
    k21, k24 = lu.get((2, 'k21')), lu.get((2, 'k24'))
    inc24 = next((c[3] for c in at['anomalyInclude']['LOKO']['allLabels']['contradictions'] if c[0] == 2 and c[1] == 'k24'), None)
    l2resid = (f'候補の位置でのk21（強い接触なのに g={k21["gBlockMinAtFit"]:+.2f} m）とk24（振動なしなのに g={k24["gBlockMinAtFit"]:+.2f} m'
               + (f'、L2終盤のラベルもあてはめに含めると {inc24:+.2f} m' if inc24 is not None else '') + '）'
               if k21 and k24 else 'k21とk24')
    bsh, bca = bl['shipped']['total'], bl['candidate']['total']
    beyond_txt = (f'合計で候補 {bca["secondsBeyondLimit"]:.1f} 秒・最大 {bca["maxBeyondLimitMeters"]:.2f} m、現行 {bsh["secondsBeyondLimit"]:.1f} 秒・'
                  f'最大 {bsh["maxBeyondLimitMeters"]:.2f} m（元GPS {bl["raw"]["total"]["secondsBeyondLimit"]:.1f} 秒）。'
                  '候補はラベルとの整合のために道路境界への当てはまりを少し譲る。')

    data = {
        'rows': {k: ev['heldOutRows'][k] for k in ('raw', 'shipped', 'trackOnlySession', 'candidateLOKO', 'candidateLOLO')},
        'names': {k: METHOD_JA[k] for k in ('raw', 'shipped', 'trackOnlySession', 'candidateLOKO', 'candidateLOLO')},
        'grid': fr['gridForPlot'],
        'sessions': {'shipped': so['shipped'], 'trackOnly': so['trackOnlySession'], 'candidate': so['candidate']},
        'laps': {'shipped': ev['offsets']['shipped'], 'candidate': {str(l['lap']): l['offsetMeters'] for l in cand['laps']}},
        'loko': ev['lokoSessionOffsets'],
    }
    blob = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')

    return TEMPLATE.replace('%%VERDICT%%', verdict).replace('%%HELD%%', held_table).replace('%%BOOT%%', boot_table) \
        .replace('%%ANOM%%', anom_table).replace('%%SENS%%', sens_table).replace('%%TOL%%', tol_table) \
        .replace('%%FEAS%%', feas_table).replace('%%CANDFEAS%%', cand_feas_table).replace('%%COURSE%%', course_table) \
        .replace('%%COURSENOTE%%', esc(course_note)).replace('%%BEYOND%%', beyond_table).replace('%%LABELS%%', labels_table) \
        .replace('%%LAPS%%', lap_table).replace('%%DATA%%', blob) \
        .replace('%%SESS_CAND%%', f'({so["candidate"][0]:+.3f}, {so["candidate"][1]:+.3f})') \
        .replace('%%SESS_TRK%%', f'({so["trackOnlySession"][0]:+.3f}, {so["trackOnlySession"][1]:+.3f})') \
        .replace('%%SESS_SHIP%%', f'({so["shipped"][0]:+.3f}, {so["shipped"][1]:+.3f})') \
        .replace('%%FEAS_SESS%%', wtxt(sess_t15)) \
        .replace('%%MINVIOL%%', f'{dflt["minWorstViolationSessionM"]:.3f}') \
        .replace('%%MINVIOL_ANOM%%', f'{dflt["minWorstViolationSessionWithAnomalyM"]:.3f}') \
        .replace('%%SIGN_BASE%%', f'({sign["baseSignSession"][0]:+.3f}, {sign["baseSignSession"][1]:+.3f})') \
        .replace('%%SIGN_ROB%%', f'({sign["robustSignSession"][0]:+.3f}, {sign["robustSignSession"][1]:+.3f})') \
        .replace('%%SIGN_SPUR%%', esc('、'.join(f'L{s["lap"]} {s["samples"]}点（最悪 {s["worstBaseGapM"]:+.2f} m、{s["stations"][0]:.0f} m付近）'
                                               for s in sign['spuriousSamplesAtBaseOptimum']) or 'なし')) \
        .replace('%%SIGN_HELD%%', f'{sign["baseSignHeldOut"]["allLabels"]["contradictionCount"]}件・AUC {f2(sign["baseSignHeldOut"]["allLabels"]["auc"], 3)}') \
        .replace('%%L2DEV%%', esc(l2_txt)).replace('%%DEVLAPS%%', esc('、'.join(f'L{n}' for n in dev_laps) or 'なし')) \
        .replace('%%WET%%', esc(f'強 {wet["strongLabels"]}件（{wet["strongKerbs"]}縁石）・無 {wet["silentLabels"]}件、必要条件 強≥{wet["required"]["strongLabels"]}件かつ≥{wet["required"]["strongKerbs"]}縁石')) \
        .replace('%%UNRES%%', esc('、'.join(f'L{u["lap"]} {u["kerb"]} {"強" if u["class"] == "strong" else "無"} g={u["gBlockMin"]:+.2f} m'
                                           f'{"（異常区間・あてはめ外）" if u["anomaly"] else ""}' for u in cand['unresolvedLabels']) or 'なし')) \
        .replace('%%LOKOSPREAD%%', esc(f'東 {ev["lokoSessionSpread"]["east"][0]:+.3f}〜{ev["lokoSessionSpread"]["east"][1]:+.3f} m、'
                                       f'南 {ev["lokoSessionSpread"]["south"][0]:+.3f}〜{ev["lokoSessionSpread"]["south"][1]:+.3f} m')) \
        .replace('%%LOLO_NA%%', f'{lolo_na["contradictionCount"]}件（現行 {ship_na["contradictionCount"]}件）') \
        .replace('%%LOKO_NA%%', f'{loko_na["contradictionCount"]}件・AUC {f2(loko_na["auc"], 3)}（現行 {ship_na["contradictionCount"]}件・{f2(ship_na["auc"], 3)}）') \
        .replace('%%BSHIP_NA%%', f'矛盾 {b_ship_na["contradictionDiff"]:+d} {ci_text(b_ship_na["contradictionDiffCI95"])}、AUC {b_ship_na["aucDiff"]:+.3f} {ci_text(b_ship_na["aucDiffCI95"], 3)}') \
        .replace('%%RAWC%%', str(raw['contradictionCount'])).replace('%%TRKC%%', str(trk['contradictionCount'])) \
        .replace('%%USED%%', esc(used_txt)).replace('%%ANOM_BEST%%', esc(best_ja)).replace('%%ANOM_SPREAD%%', f'{spread:.3f}') \
        .replace('%%TOL07%%', esc(tol07)).replace('%%COURSE_CHANGES%%', esc(course_changes)) \
        .replace('%%LAPFEAS%%', esc(lapfeas_txt)).replace('%%SESSTRK%%', esc(sesstrk_txt)).replace('%%ROBUST%%', robust_table) \
        .replace('%%PROBES%%', probe_table).replace('%%L2RESID%%', esc(l2resid)).replace('%%LOLOTXT%%', esc(lolo_txt)).replace('%%BEYONDTXT%%', esc(beyond_txt))


TEMPLATE = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>縁石接触補正候補の評価</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f9f9f7; --surface-1: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a;
  --wash1: rgba(42,120,214,0.18); --wash2: rgba(235,104,52,0.22);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #0d0d0d; --surface-1: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70;
    --wash1: rgba(57,135,229,0.25); --wash2: rgba(217,89,38,0.28);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #0d0d0d; --surface-1: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70;
  --wash1: rgba(57,135,229,0.25); --wash2: rgba(217,89,38,0.28);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--ink);
  font: 15px/1.65 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 36px 0 8px; padding-top: 8px; border-top: 1px solid var(--grid); }
h3 { font-size: 15px; margin: 20px 0 6px; }
p, li { color: var(--ink); }
.sub { color: var(--ink-2); margin: 0 0 16px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin: 12px 0; }
.tw { overflow-x: auto; margin: 8px 0; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; white-space: nowrap; }
table.num td { font-variant-numeric: tabular-nums; }
table.small { font-size: 12px; }
code { font-size: 12.5px; background: var(--grid); padding: 1px 4px; border-radius: 4px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--ink-2); margin: 6px 0; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart { width: 100%; position: relative; }
.chart svg { width: 100%; height: auto; display: block; }
.tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 8px; font-size: 12px; color: var(--ink-2); box-shadow: 0 2px 8px rgba(0,0,0,0.12);
  display: none; white-space: nowrap; z-index: 2; }
.tip b { color: var(--ink); font-size: 13px; }
.note { color: var(--ink-2); font-size: 13px; }
details summary { cursor: pointer; color: var(--ink-2); }
</style>
</head>
<body>
<main>
<h1>縁石接触ラベルを使ったGPS補正の候補（7/30）の評価</h1>
<p class="sub">BACKLOG GPS-REG-15・2026-10-02。ビューワーでは <code>?gps=kerb</code> のときだけ読み込む選択式の候補で、現行の補正（gps_registration.json）は置き換えない。</p>

<h2>要旨</h2>
<div class="card">%%VERDICT%%</div>

<h2>何をしたか</h2>
<ul>
<li><b>候補の作り方</b>：セッション共通の平行移動と、周ごとの小さなずれ（σ 0.25 m）を、道路境界の制約と縁石接触ラベルで同時に決める。ラベルの項は、内側タイヤ外端と赤白ブロックの始まりとの距離gに対するヒンジ（強い接触：g≤0、振動なし：g≥−0.40 m）で、<b>頑健な損失（Huber、δ 0.15 m）</b>にした。1つの矛盾したラベルが補正を引きずらない。</li>
<li><b>使ったラベルを全件出力に記録</b>した（下の表）。%%USED%%を使い、L2の3250〜3900 m（既知のGPS異常区間）の4件は<b>あてはめから外して警告</b>とした（吸収しない）。</li>
<li>候補のセッション補正は %%SESS_CAND%% m（東, 南）。道路境界のみのセッション推定は %%SESS_TRK%%、現行のセッション値は %%SESS_SHIP%%。</li>
<li>あてはめ後もラベルに反する所：%%UNRES%%。</li>
<li>道路境界の距離の符号の取り方を改めた（下の「見つかった問題」）。</li>
</ul>

<h2>交差検証の結果</h2>
<p>gは公開済みのKPIコード（build_apex_kpi.kpi）で計算した。<b>矛盾</b>＝強い接触なのに g＞+0.15 m、または振動なしなのに g＜−0.55 m。<b>AUC</b>＝強い接触のgが振動なしのgより小さい確率。縁石を外す交差検証では、外した縁石のラベルを全周であてはめから除いた。周を外す交差検証では、その周のラベルを除き、その周の補正は道路境界とセッション値だけで決めた。</p>
%%HELD%%
<p class="note">%%LOLOTXT%%</p>
<p class="note">異常区間のラベルを除いた55件でも、縁石外しは %%LOKO_NA%%、周外しは %%LOLO_NA%%。元GPSは%%RAWC%%件、道路境界のみ（セッション）は%%TRKC%%件。</p>

<h3>図1　外したラベルでのg（周×縁石ごと）</h3>
<div class="legend"><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--s1)"/></svg>強い接触（gは0以下であるべき）</span><span><svg width="12" height="12"><rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="var(--s2)"/></svg>振動なし（gは−0.40 m以上であるべき）</span><span>縦線：矛盾の境目（+0.15 m／−0.55 m）</span></div>
<div class="chart" id="strip"></div>
<p class="note">右端の数字は各方式の矛盾数。点にマウスを合わせると周・縁石・gが出る（値は上の表と下のラベル表にもある）。</p>

<h3>縁石単位のブートストラップ（10,000回）</h3>
%%BOOT%%
<p class="note">異常区間のラベルを除いた場合の候補−現行：%%BSHIP_NA%%。</p>

<h2>補正の位置と「ラベルで許される範囲」</h2>
<p>セッション補正を1つの平行移動とみなしたとき、全ラベル（異常区間を除く）を許容0.15 m以内で満たす範囲は %%FEAS_SESS%% m（長軸×短軸）。%%SESSTRK%%。許容0では空で、どの平行移動でも最悪のラベルが %%MINVIOL%% m はみ出す（異常区間を含めると %%MINVIOL_ANOM%% m）。%%LAPFEAS%%。</p>
<div class="legend"><span><svg width="14" height="12"><rect x="1" y="1" width="12" height="10" fill="var(--wash1)"/></svg>道路境界のほぼ最適な範囲（セッション）</span><span><svg width="14" height="12"><rect x="1" y="1" width="12" height="10" fill="var(--wash2)"/></svg>ラベルで許される範囲（許容0.15 m）</span><span><svg width="12" height="12"><path d="M6 1 L11 6 L6 11 L1 6 Z" fill="var(--s1)"/></svg>道路境界のみのセッション値</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--s2)"/></svg>候補（大：セッション、小：周、白抜き：縁石外しのセッション値）</span><span><svg width="12" height="12"><rect x="1.5" y="1.5" width="9" height="9" fill="var(--s3)"/></svg>現行（大：セッション、小：周）</span></div>
<div class="chart" id="plane"></div>
<p class="note">横軸は東向き、縦軸は南向き（下が南）。縁石を1つずつ外したときのセッション値の幅：%%LOKOSPREAD%%。</p>

<h3>候補ファイルに記録した範囲の幅（m、長軸×短軸）</h3>
%%CANDFEAS%%
<p class="note">道路境界のほぼ最適＝道路境界のコストが最小値＋0.05（周ごと）以内。範囲は補正値の格子（0.05 m）で数えた。</p>

<h3>しきい値と許容幅による変化（m、長軸×短軸）</h3>
%%FEAS%%

<h2>感度</h2>
<h3>L2終盤（3250〜3900 m）の扱い</h3>
%%ANOM%%
<p class="note">含めるとL2の周補正がk21を満たす向きに動き、k24の振動なしが矛盾になる（符号が逆の誤差）。縁石外しの矛盾数が最も少ないのは「%%ANOM_BEST%%」。3通りのセッション値の差は最大 %%ANOM_SPREAD%% m。</p>
<h3>頑健な損失の効果（L2終盤のラベルをあてはめに含めた場合）</h3>
%%ROBUST%%
<p class="note">異常区間を除外した既定の設定では、Huber・soft-L1・二乗の差は0.02 m以内で、矛盾数も同じ（感度の表）。頑健な損失が効くのは、矛盾するラベルがあてはめに入ったときである。</p>
<h3>重み・しきい値・損失・周のずれの大きさ（縁石外し）</h3>
%%SENS%%
<h3>矛盾の許容幅</h3>
%%TOL%%
<p class="note">絶対値は少なくとも±0.7 mの幅で示す。%%TOL07%%。</p>

<h2>コースの修正だけの効果（現行の配信値のまま）</h2>
%%COURSE%%
<p class="note">縁石線の差し替えで大きく動いたのは %%COURSENOTE%%（gの中央値の変化、v3−旧CG）。ラベルの判定が変わったのは %%COURSE_CHANGES%%。</p>

<h2>道路境界のはみ出し（現行と同じ測り方）</h2>
%%BEYOND%%
<p class="note">タイヤが道路境界（geometry.json）より0.05 m以上外に出た時間。%%BEYONDTXT%%</p>

<h2>見つかった問題：境界の段差で距離の符号が反転する</h2>
<p>縁石区分を道路境界に組み込むと、ダンロップ右の縁石の終わり（2997 m）で外側の端が道路端に戻る所に、進行方向に直交する1.5 mの段ができる。従来の <code>Boundary.signed_gap</code> は最寄りの線分の向きで符号を決めるため、この段の近くでは道路の3.5 m内側の点が「3.5 m外に出た」と判定される。候補と比較用の道路境界のみの推定では、符号を道路の内向き法線で決める方式に改めた（距離は同じ。現行の配信値の位置では全標本で従来と一致）。</p>
<ul>
<li>従来の符号で道路境界のみのセッション推定：%%SIGN_BASE%%、改めた符号：%%SIGN_ROB%%。従来の符号での最適点での誤判定：%%SIGN_SPUR%%。従来の符号の推定を外したラベルで採点すると %%SIGN_HELD%%。</li>
<li>符号の誤判定が起きる補正値の例（全6周の合計）：</li>
</ul>
%%PROBES%%
<ul>
<li>今回の評価では、道路境界のみの推定の最適点は誤判定の影響をほとんど受けていない（差は数cm）。誤判定は最適点から外れた補正値で起き、コストを2倍以上にして探索を乱しうる。</li><li>現行の配信値（gps_registration.json）は縁石区分を組み込まない道路境界で推定しているので、この問題の影響は受けない（従来の符号でも段差が小さい）。</li><li>第3版の道路境界のみ推定A (+0.41, −2.87) でも現在の縁石区分では誤判定が起きる。第3版も同じ方式で縁石区分（v2）を組み込んでいたので、その推定も影響を受けた可能性がある（未検証）。</li>
<li>同じ縁石区分の道路境界を使う <code>build_apex_kpi.py</code> の立ち上がり外側の距離（exitOuterGapM）にも同じ問題が起こりうる（今回は範囲外のため未修正）。</li>
</ul>

<h2>GPS異常区間の裏づけ（簡易検査）</h2>
<p>ロガーの横Gとステア（GPS-REG-08で時刻を合わせたもの）とGPSの横加速度の時間ずれを、縁石の通過ごとに測った。L2終盤の通過の、他の周の同じ縁石との差：%%L2DEV%%。0.15 s以上ずれた通過は %%DEVLAPS%% にあり、L2ではない。<b>この簡易検査ではL2の異常は再現しない</b>。横GはGPS-REG-08でGPSとの相互相関により合わせたので独立な証拠ではない。L2の警告は、%%L2RESID%%という符号が逆の残差と、第3版の証拠（ステアの戻し、3580〜3600 mの振動）に基づく。異常区間の判定基準は別課題（GPS異常区間の警告）として残る。</p>

<h2>7/29（雨）は候補を作らない</h2>
<p>%%WET%%。「タイヤが赤白ブロックに届いている（道路の内側へ離れすぎていない）」側の制約を与えるのは強い接触だけである。1件では、振動なしのラベルが「乗りすぎていない」側から押すだけで、補正は道路境界の平らな範囲に残り、外して検証できる強い接触もない（縁石外しのAUCが定義できない）。スクリプトは終了コード3で拒否する。</p>

<h2>候補の周ごとの補正</h2>
%%LAPS%%
<details><summary>使ったラベルの一覧（出力の labelsUsed と同じ）</summary>
%%LABELS%%
</details>

<h2>限界</h2>
<ul>
<li>ラベルのしきい値は7/30のデータで決めたもので、強さは速度と標本化に依存する。縁石以外の振動（ブレーキのロック等）は除いていない。</li>
<li>縁石線は2019年のオルソ画像由来（位置合わせ誤差0.2〜0.3 m、2020年とは未照合）。絶対値の幅は±0.7 m以上とする。</li>
<li>アンテナ位置は未測定（車体中心と仮定）。</li>
<li>縁石は14か所しかなく、ブートストラップの区間は粗い。</li>
<li>ビューワーのKPIパネル（apex_kpi.json）は現行の補正で計算されている。<code>?gps=kerb</code> のときも数値は現行の補正のものになる。</li>
</ul>

<h2>再現</h2>
<ul>
<li>候補：<code>pipeline/register_gps_kerb_contact.py --track fuji --race fuji_aim_2020_07_30 --surface dry --vehicle-profile public/data/vehicles/mazda2-dj.json</code></li>
<li>評価：<code>scripts/quality/apex_kpi/eval_kerb_registration.py</code>（コース比較の旧v2線だけ環境変数 APEX_WORK の course_v2_kerbs.json を使う）</li>
<li>本ページ：<code>scripts/quality/apex_kpi/report_kerb_registration.py</code>。数値は evaluation.json と gps_registration_kerb.json から生成。</li>
</ul>
</main>
<div class="tip" id="tip"></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
(function () {
  const D = JSON.parse(document.getElementById('data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const tip = document.getElementById('tip');
  function el(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function showTip(evt, lines) {
    tip.textContent = '';
    lines.forEach((ln, i) => {
      const row = document.createElement('div');
      if (i === 0) { const b = document.createElement('b'); b.textContent = ln; row.appendChild(b); }
      else row.textContent = ln;
      tip.appendChild(row);
    });
    tip.style.display = 'block';
    const x = evt.pageX + 12, y = evt.pageY + 12;
    tip.style.left = Math.min(x, window.scrollX + document.documentElement.clientWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = y + 'px';
  }
  function hideTip() { tip.style.display = 'none'; }
  function hit(node, lines) {
    node.setAttribute('tabindex', '0');
    node.addEventListener('pointermove', e => showTip(e, lines));
    node.addEventListener('pointerleave', hideTip);
    node.addEventListener('focus', e => { const r = node.getBoundingClientRect();
      showTip({ pageX: r.right + window.scrollX, pageY: r.top + window.scrollY }, lines); });
    node.addEventListener('blur', hideTip);
  }

  // ---------- figure 1: strip plot of held-out g ----------
  (function strip() {
    const keys = ['raw', 'shipped', 'trackOnlySession', 'candidateLOKO', 'candidateLOLO'];
    const W = 1000, rowH = 64, top = 16, left = 250, right = 70, bottom = 40;
    const H = top + rowH * keys.length + bottom;
    const x0 = -3, x1 = 7;
    const sx = v => left + (Math.max(x0, Math.min(x1, v)) - x0) / (x1 - x0) * (W - left - right);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '外したラベルでのgの分布' });
    for (let t = x0; t <= x1; t += 1) {
      el('line', { x1: sx(t), x2: sx(t), y1: top, y2: top + rowH * keys.length, stroke: 'var(--grid)', 'stroke-width': 1 }, svg);
      const lb = el('text', { x: sx(t), y: H - 18, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--muted)' }, svg);
      lb.textContent = (t > 0 ? '+' : '') + t;
    }
    const ax = el('text', { x: (left + W - right) / 2, y: H - 2, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink-2)' }, svg);
    ax.textContent = 'g（m、内側タイヤ外端と赤白ブロックの始まり、マイナス＝乗っている）';
    [[0.15, '+0.15'], [-0.55, '−0.55']].forEach(([v, s]) => {
      el('line', { x1: sx(v), x2: sx(v), y1: top - 6, y2: top + rowH * keys.length, stroke: 'var(--ink-2)', 'stroke-width': 1 }, svg);
      const t = el('text', { x: sx(v) + 3, y: top - 2, 'font-size': 11, fill: 'var(--ink-2)' }, svg); t.textContent = s;
    });
    keys.forEach((k, i) => {
      const yc = top + rowH * i + rowH / 2;
      el('line', { x1: left, x2: W - right, y1: top + rowH * (i + 1), y2: top + rowH * (i + 1), stroke: 'var(--grid)', 'stroke-width': 1 }, svg);
      const name = el('text', { x: left - 10, y: yc + 4, 'text-anchor': 'end', 'font-size': 12.5, fill: 'var(--ink)' }, svg);
      name.textContent = D.names[k];
      let nc = 0;
      (D.rows[k] || []).forEach((r, j) => {
        if (r.g === null) return;
        const strong = r.cls === 'strong';
        const bad = strong ? r.g > 0.15 : r.g < -0.55;
        if (bad) nc++;
        const jitter = ((j * 37) % 11 - 5) * 1.6;
        const y = yc + (strong ? -11 : 11) + jitter * 0.6;
        const x = sx(r.g);
        const g = el('g', {}, svg);
        el('circle', { cx: x, cy: y, r: 12, fill: 'transparent' }, g);
        if (strong) el('circle', { cx: x, cy: y, r: 4.5, fill: 'var(--s1)', stroke: 'var(--surface-1)', 'stroke-width': 2 }, g);
        else el('rect', { x: x - 4.5, y: y - 4.5, width: 9, height: 9, rx: 1.5, fill: 'var(--s2)', stroke: 'var(--surface-1)', 'stroke-width': 2 }, g);
        if (bad) el('circle', { cx: x, cy: y, r: 8, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 1.5 }, g);
        hit(g, [`g ${r.g >= 0 ? '+' : ''}${r.g.toFixed(2)} m`, `L${r.lap} ${r.kerb}・${strong ? '強い接触' : '振動なし'}`,
                (r.anomaly ? 'GPS異常区間（L2終盤）・' : '') + (bad ? '矛盾' : '整合')]);
      });
      const c = el('text', { x: W - right + 10, y: yc + 4, 'font-size': 12.5, fill: 'var(--ink)' }, svg);
      c.textContent = `矛盾 ${nc}`;
    });
    document.getElementById('strip').appendChild(svg);
  })();

  // ---------- figure 2: offset plane ----------
  (function plane() {
    const G = D.grid, n = G.n, st = G.step;
    const ex0 = G.origin[0], sz0 = G.origin[1], span = (n - 1) * st;
    const W = 640, pad = 56, S = W - 2 * pad;
    const H = W;
    const sx = e => pad + (e - ex0) / span * S;
    const sy = z => pad + (z - sz0) / span * S;     // +south downwards
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '補正の位置とラベルで許される範囲' });
    for (let k = 0; k <= 8; k++) {
      const e = ex0 + k * span / 8, z = sz0 + k * span / 8;
      el('line', { x1: sx(e), x2: sx(e), y1: pad, y2: pad + S, stroke: 'var(--grid)', 'stroke-width': 1 }, svg);
      el('line', { x1: pad, x2: pad + S, y1: sy(z), y2: sy(z), stroke: 'var(--grid)', 'stroke-width': 1 }, svg);
      if (k % 2 === 0) {
        const a = el('text', { x: sx(e), y: pad + S + 18, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)' }, svg);
        a.textContent = e.toFixed(1);
        const b = el('text', { x: pad - 8, y: sy(z) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)' }, svg);
        b.textContent = z.toFixed(1);
      }
    }
    const xl = el('text', { x: pad + S / 2, y: H - 14, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink-2)' }, svg);
    xl.textContent = '東向きの補正 (m)';
    const yl = el('text', { x: 14, y: pad + S / 2, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink-2)',
      transform: `rotate(-90 14 ${pad + S / 2})` }, svg);
    yl.textContent = '南向きの補正 (m)';
    const cell = S / (n - 1);
    function cells(list, fill) {
      list.forEach(idx => {
        const i = Math.floor(idx / n), j = idx % n;
        el('rect', { x: sx(ex0 + i * st) - cell / 2, y: sy(sz0 + j * st) - cell / 2, width: cell + 0.3, height: cell + 0.3, fill }, svg);
      });
    }
    cells(G.cells.trackNearOptimal || [], 'var(--wash1)');
    cells(G.cells['labelFeasible_tol0.15'] || [], 'var(--wash2)');
    function marker(kind, p, size, lines, hollow) {
      const g = el('g', {}, svg);
      const x = sx(p[0]), y = sy(p[1]);
      el('circle', { cx: x, cy: y, r: Math.max(12, size + 6), fill: 'transparent' }, g);
      const col = kind === 'cand' ? 'var(--s2)' : kind === 'ship' ? 'var(--s3)' : 'var(--s1)';
      const common = { fill: hollow ? 'var(--surface-1)' : col, stroke: hollow ? col : 'var(--surface-1)', 'stroke-width': 2 };
      if (kind === 'cand') el('circle', Object.assign({ cx: x, cy: y, r: size }, common), g);
      else if (kind === 'ship') el('rect', Object.assign({ x: x - size, y: y - size, width: 2 * size, height: 2 * size }, common), g);
      else el('path', Object.assign({ d: `M${x} ${y - size * 1.3} L${x + size * 1.3} ${y} L${x} ${y + size * 1.3} L${x - size * 1.3} ${y} Z` }, common), g);
      hit(g, lines);
    }
    const fmt = p => `(${p[0] >= 0 ? '+' : ''}${p[0].toFixed(3)}, ${p[1] >= 0 ? '+' : ''}${p[1].toFixed(3)}) m`;
    Object.entries(D.loko).forEach(([k, p]) => marker('cand', p, 3.5, [fmt(p), `縁石 ${k} を外したセッション値`], true));
    Object.entries(D.laps.shipped).forEach(([k, p]) => marker('ship', p, 3.5, [fmt(p), `現行 L${k}`]));
    Object.entries(D.laps.candidate).forEach(([k, p]) => marker('cand', p, 4, [fmt(p), `候補 L${k}`]));
    marker('ship', D.sessions.shipped, 6, [fmt(D.sessions.shipped), '現行のセッション値']);
    marker('trk', D.sessions.trackOnly, 6, [fmt(D.sessions.trackOnly), '道路境界のみのセッション値']);
    marker('cand', D.sessions.candidate, 7, [fmt(D.sessions.candidate), '候補のセッション値']);
    document.getElementById('plane').appendChild(svg);
  })();
})();
</script>
</body>
</html>
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', type=Path, default=DEFAULT_DIR)
    ap.add_argument('--candidate', type=Path, default=CANDIDATE)
    args = ap.parse_args()
    ev = json.loads((args.dir / 'evaluation.json').read_text(encoding='utf-8'))
    cand = json.loads(args.candidate.read_text(encoding='utf-8'))
    out = args.dir / 'report.html'
    out.write_text(build(ev, cand), encoding='utf-8')
    print('->', out)
    (args.dir / 'summary.json').write_text(json.dumps(summary(ev, cand), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('->', args.dir / 'summary.json')
    return 0


def summary(ev: dict, cand: dict) -> dict:
    """Headline numbers for the orchestrator (all copied from evaluation.json / the candidate)."""
    H, bs, cf = ev['heldOut'], ev['bootstrapByKerb'], ev['courseFixShippedRegistration']

    def m(x):
        return {'contradictions': x['contradictionCount'], 'auc': x['auc'], 'withinKerbAuc': x['withinKerbAuc']}
    return {
        'task': 'D (BACKLOG GPS-REG-15): opt-in kerb-contact registration candidate for 7/30',
        'candidate': {'file': 'public/data/races/fuji_aim_2020_07_30/gps_registration_kerb.json',
                      'sessionOffsetMeters': cand['sessionOffsetMeters'],
                      'lapOffsetsMeters': {str(l['lap']): l['offsetMeters'] for l in cand['laps']},
                      'labelSummary': cand['labelSummary'], 'unresolvedLabels': cand['unresolvedLabels']},
        'heldOutAllLabels59': {k: m(H[k]['allLabels']) for k in ('raw', 'shipped', 'trackOnlySession', 'trackOnlySessionPlusLap',
                                                                 'candidateLOKO', 'candidateLOLO')},
        'heldOutWithoutAnomaly55': {k: m(H[k]['withoutAnomalyLabels']) for k in ('raw', 'shipped', 'trackOnlySession',
                                                                                  'candidateLOKO', 'candidateLOLO')},
        'bootstrapCandidateLOKOminusShipped': bs['candidateLOKO_minus_shipped'],
        'bootstrapCandidateLOKOminusTrackOnlySession': bs['candidateLOKO_minus_trackOnlySession'],
        'toleranceSensitivity': ev['toleranceSensitivity'],
        'courseFixShippedRegistration': {k: m(cf[k]['allLabels']) | {'unmeasurable': cf[k]['allLabels']['unmeasurable']}
                                         for k in ('cgKerbLines', 'courseV2', 'kerbZonesV3') if isinstance(cf.get(k), dict)},
        'beyondLimitTotal': {k: ev['beyondLimit'][k]['total'] for k in ('raw', 'shipped', 'trackOnlySession', 'candidate')},
        'feasibleRegionSessionTol015': ev['feasibleRegionSensitivity']['combos']['strongMax0_silentMin-0.4']['tol0.15']['session'],
        'wet0729': ev['wet0729'],
        'verdict': ('better than the track-only fits (significant), marginal vs the shipped registration (contradictions '
                    'n.s., AUC CI just above 0; worse under leave-one-lap-out and at tolerance 0.10 m): keep as the opt-in '
                    '?gps=kerb candidate, do not replace gps_registration.json'),
    }


if __name__ == '__main__':
    raise SystemExit(main())
