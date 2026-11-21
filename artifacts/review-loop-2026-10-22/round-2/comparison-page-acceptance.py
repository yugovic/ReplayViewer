"""Round 2 acceptance checks for area comparison-page (R2-01, R2-05). Numbers only. Run from the repo root."""
import json, os, re, sys
from datetime import datetime
from pathlib import Path
R = Path(__file__).resolve().parents[3]
B = R / 'artifacts' / 'event-comparison-2026-10-22'
out = {}
m = json.loads((B / 'cg' / 'manifest_onboard_vpfix.json').read_text(encoding='utf-8'))
p29, p30 = m['sessions']['fuji_aim_01']['profile'], m['sessions']['fuji_aim_2020_07_30']['profile']
ts = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
a, b = ts(m['started']), ts(m['generated'])
files = sorted(B.joinpath('cg').glob('*_cg_onboard.jpg'))
inwin = [f.name for f in files if a <= f.stat().st_mtime <= b]
out['R2-01'] = {
    'pitch0729': p29['pitchDeg'], 'yaw0729': p29['yawDeg'], 'pitch0730': p30['pitchDeg'], 'yaw0730': p30['yawDeg'],
    'profilesEqual': p29 == p30, 'manifestVanishingPointSession': m.get('vanishingPointSession'),
    'sessionVanishingPointSession': {k: v.get('vanishingPointSession') for k, v in m['sessions'].items()},
    'failures': m['summary']['failures'], 'onboardActive': m['summary']['onboardActive'], 'captures': m['summary']['captures'],
    'onboardJpgs': len(files), 'mtimeInsideRunWindow': len(inwin), 'runWindow': [m['started'], m['generated']],
}
bp = (B / 'scripts' / 'build_page.py').read_text(encoding='utf-8')
out['R2-01']['buildAssertsCommonAim'] = all(x in bp for x in (
    "assert onb_m.get('vanishingPointSession') == VP_SESSION", "assert p['pitchDeg'] == 12.8807 and p['yawDeg'] == 1.6383",
    "assert ONB_PROF['fuji_aim_01'] == ONB_PROF['fuji_aim_2020_07_30']", "assert AIM29 == AIM30 == '下向き12.9°・右向き1.6°'"))
html = (R / 'docs' / 'fuji-event-comparison-2026-10-22.html').read_text(encoding='utf-8')
out['R2-01']['page'] = {'staleAim': html.count('下向き13.7°・左向き0.5°'), 'stale13_7': html.count('13.7°'), 'staleLeft0_5': html.count('左向き0.5°'),
                        'statesBothDays0730': html.count('両日とも7/30の値を使っています'), 'commonAim': html.count('下向き12.9°・右向き1.6°'),
                        'captionCommonAim': html.count('7/30の動画の消失点に合わせた値。両日共通')}
q = json.loads((B / 'page-qa.json').read_text(encoding='utf-8'))
out['R2-01']['qa'] = {'status': q['status'], 'failures': q['failures'], 'images': f"{q['images']['probe']['loaded']}/{q['images']['probe']['uniqueUrls']}",
                      'clippingGuides': q['structure']['clippingGuides'], 'guideSummary': q['structure']['guideSummary'],
                      'staleAim29Text': q['structure']['staleAim29Text'], 'commonAimText': q['structure']['commonAimText'],
                      'onboardCaptionsCommonAim': q['structure']['onboardCaptionsCommonAim'],
                      'qaFileMtimeAfterPage': (B / 'page-qa.json').stat().st_mtime > (R / 'docs' / 'fuji-event-comparison-2026-10-22.html').stat().st_mtime}
v = json.loads((B / 'verify.json').read_text(encoding='utf-8'))
t = v['verdictSummary']['check1_wholeSetMetric']
out['R2-05'] = {'hasCondition': 'under the assumed camera (f*h 889' in t, 'hasCounts': '19/30, 24/30, 23/30 at f*h 590/889/1209' in t,
                'oldPhraseGone': 'So the images support the direction of the correction' not in t,
                'independentSupport': 'kerb audio vs IMU and video yaw vs gyro' in t}
ok = (out['R2-01']['pitch0729'] == 12.8807 and out['R2-01']['yaw0729'] == 1.6383 and out['R2-01']['profilesEqual']
      and out['R2-01']['manifestVanishingPointSession'] == 'fuji_aim_2020_07_30' and out['R2-01']['failures'] == 0
      and out['R2-01']['onboardActive'] == 106 and out['R2-01']['mtimeInsideRunWindow'] == 106 == out['R2-01']['onboardJpgs']
      and out['R2-01']['buildAssertsCommonAim'] and out['R2-01']['page']['staleAim'] == 0 and out['R2-01']['page']['statesBothDays0730'] == 1
      and q['status'] == 'passed' and not q['failures'] and out['R2-01']['qa']['images'] == '531/531' and q['structure']['clippingGuides'] == 110
      and all(out['R2-05'].values()))
out['allAcceptancePassed'] = ok
print(json.dumps(out, ensure_ascii=False, indent=1))
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
