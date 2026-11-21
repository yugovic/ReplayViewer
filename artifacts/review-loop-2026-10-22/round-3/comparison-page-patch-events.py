# Review round 3, R3-01: patch the 7 k1 notesJa strings of events.json IN PLACE (no other field changes).
#   python -I artifacts/review-loop-2026-10-22/round-3/comparison-page-patch-events.py [--check]
# The new sentence is read from scripts/build_events.py (K1_NOTE_JA, via ast) so the data file and the builder agree.
import ast
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
REPO = Path(__file__).resolve().parents[3]
BASE = REPO / 'artifacts' / 'event-comparison-2026-10-22'
EV = BASE / 'events.json'
OLD = ('k1（コカ・コーラ）: 3DCGのブロック線が接触開始時に約12 m/sで近づく先端形状のため、乗った瞬間の距離が大きく出る。'
       '評価では7/30全周で3DCGの接触が約0.15秒遅いと指摘（個別確認待ち）。')
K1_IDS = ['contact-0730-L1-k1', 'contact-0730-L2-k1', 'contact-0730-L3-k1', 'contact-0730-L4-k1',
          'contact-0730-L5-k1-1', 'contact-0730-L5-k1-2', 'contact-0730-L6-k1']

tree = ast.parse((BASE / 'scripts' / 'build_events.py').read_text(encoding='utf-8'))
NEW = next(ast.literal_eval(n.value) for n in tree.body
           if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'K1_NOTE_JA' for t in n.targets))
assert NEW.startswith('k1（コカ') and '0.15秒' not in NEW and '全周で' not in NEW, NEW

raw = EV.read_bytes()
txt = raw.decode('utf-8')
before = json.loads(txt)
old_j, new_j = json.dumps(OLD, ensure_ascii=False), json.dumps(NEW, ensure_ascii=False)
n_old, n_new = txt.count(old_j), txt.count(new_j)
print(json.dumps(dict(file=str(EV.relative_to(REPO)).replace('\\', '/'), sha256=hashlib.sha256(raw).hexdigest(),
                      oldStringCount=n_old, newStringCount=n_new), ensure_ascii=False))
if '--check' in sys.argv or n_old == 0:
    assert n_old == 0 and n_new == 7, (n_old, n_new)
    print('already patched')
    sys.exit(0)
assert n_old == 7 and n_new == 0, (n_old, n_new)
out_txt = txt.replace(old_j, new_j)
after = json.loads(out_txt)
# only those 7 notesJa entries differ
exp = copy.deepcopy(before)
for e in exp['events']:
    if e['id'] in K1_IDS:
        assert e['notesJa'] == [OLD], (e['id'], e['notesJa'])
        e['notesJa'] = [NEW]
assert exp == after, 'unexpected difference'
assert sorted(e['id'] for e in after['events'] if NEW in e['notesJa']) == sorted(K1_IDS)
EV.write_bytes(out_txt.encode('utf-8'))
print(json.dumps(dict(patched=7, newSha256=hashlib.sha256(EV.read_bytes()).hexdigest(),
                      byteLengthBefore=len(raw), byteLengthAfter=len(out_txt.encode('utf-8'))), ensure_ascii=False))
