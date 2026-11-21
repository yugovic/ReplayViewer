# Review round 3 acceptance checks for the comparison page (R3-01, R3-02). Read-only.
#   python -I artifacts/review-loop-2026-10-22/round-3/comparison-page-acceptance.py
import hashlib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
REPO = Path(__file__).resolve().parents[3]
BASE = REPO / 'artifacts' / 'event-comparison-2026-10-22'
OUT = Path(__file__).resolve().parent / 'comparison-page-acceptance.json'
K1 = ['contact-0730-L1-k1', 'contact-0730-L2-k1', 'contact-0730-L3-k1', 'contact-0730-L4-k1',
      'contact-0730-L5-k1-1', 'contact-0730-L5-k1-2', 'contact-0730-L6-k1']
res, fails = {}, []


def chk(name, ok, detail=None):
    res[name] = dict(ok=bool(ok), detail=detail)
    if not ok:
        fails.append(name)


# R3-01: grep over the two builders, events.json (and the schematic manifest / page, extra)
hits = {}
for rel in ('scripts/build_schematics.py', 'scripts/build_events.py', 'events.json', 'schematic/schematics.json'):
    t = (BASE / rel).read_text(encoding='utf-8')
    hits[rel] = {'0.15秒': t.count('0.15秒'), '全周で': t.count('全周で')}
page = (REPO / 'docs' / 'fuji-event-comparison-2026-10-22.html').read_text(encoding='utf-8')
hits['docs/fuji-event-comparison-2026-10-22.html'] = {'約0.15秒': page.count('約0.15秒'), '全周で3DCG': page.count('全周で3DCG')}
chk('grepZeroHits', all(v == 0 for d in hits.values() for v in d.values()), hits)

before = dict(l.strip().split(' *', 1)[::-1] for l in (BASE / 'tmp/round3-impl/before/schematic_sha_before.txt').read_text().splitlines())
now = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BASE / 'schematic').glob('*.png'))}
k1_files = {f'{i}_top.png' for i in K1}
changed = sorted(n for n in now if now[n] != before.get(n))
fix_t = (BASE / 'events.json').stat().st_mtime
chk('sevenK1PngsChangedOthersIdentical', set(changed) == k1_files and len(now) == 107,
    dict(total=len(now), changed=changed, unchangedBytes=len(now) - len(changed)))
chk('k1PngMtimeAfterFix', all((BASE / 'schematic' / f).stat().st_mtime > fix_t for f in k1_files))

sch = json.loads((BASE / 'schematic/schematics.json').read_text(encoding='utf-8'))
ev_sha = hashlib.sha256((BASE / 'events.json').read_bytes()).hexdigest()
k1_notes = {r['id']: r['textJa'].get('note') for r in sch['schematics'] if r['id'] in K1}
chk('schematicsManifest', sch['checks']['failures'] == [] and sch['checks']['n'] == 106 and sch['events']['sha256'] == ev_sha
    and len(set(k1_notes.values())) == 1 and '0.09〜0.36秒遅れ' in next(iter(k1_notes.values())),
    dict(n=sch['checks']['n'], failures=sch['checks']['failures'], note=next(iter(k1_notes.values()))))

ev = json.loads((BASE / 'events.json').read_text(encoding='utf-8'))
k1n = {e['id']: e['notesJa'] for e in ev['events'] if e['id'] in K1}
chk('eventsNotesJa', all(len(v) == 1 and v[0].startswith('k1（コカ') and '0.09〜0.36秒' in v[0] for v in k1n.values()) and len(k1n) == 7)

v = json.loads((BASE / 'verify.json').read_text(encoding='utf-8'))
r3 = v['round3Recheck']['eventsJson']
frames = json.loads((BASE / 'frames/manifest.json').read_text(encoding='utf-8'))
chk('provenanceChain', r3['newSha256'] == ev_sha and frames['events']['sha256'] == r3['previousSha256'], r3)

# R3-02: summary sentence, numbers from verify.json
adv = v['round2Recheck']['adversarialRerun0729']['countsPairRule']['clipping0729']
m = re.match(r'(\d+) vs (\d+) of (\d+) usable', adv)
fx, rw, us = m.groups()
sent = f'照合に使えるのが14件中{us}件しかなく、補正後が近いのは{fx}件・補正前が近いのは{rw}件で、判定できません'
summary = re.search(r'<h2 id="h-sum">要旨</h2>\s*<p>(.*?)</p>', page, re.S).group(1)
chk('summary0729Sentence', sent in summary and '7/29のクリッピングポイントは' in summary, dict(fromVerifyJson=dict(fixed=fx, raw=rw, usable=us)))

qa = json.loads((BASE / 'page-qa.json').read_text(encoding='utf-8'))
p = qa['images']['probe']
chk('qaPage', qa['status'] == 'passed' and p['loaded'] == p['uniqueUrls'] == 531 and qa['structure']['clippingGuides'] == 110
    and qa['console']['errors'] == [] and qa['console']['pageErrors'] == [] and qa['structure']['staleK1Text'] == []
    and qa['structure']['summary0729'] is True,
    dict(generated=qa['generated'], status=qa['status'], images=f"{p['loaded']}/{p['uniqueUrls']}",
         guides=qa['structure']['clippingGuides'], consoleErrors=len(qa['console']['errors'])))

out = dict(kind='review-loop-acceptance', round=3, area='comparison-page', passed=not fails, failures=fails, checks=res)
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(dict(passed=not fails, failures=fails), ensure_ascii=False))
