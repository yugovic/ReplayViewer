# Review round 3 (R3-01 / R3-02): add round3Recheck to artifacts/event-comparison-2026-10-22/verify.json.
#   python -I artifacts/review-loop-2026-10-22/round-3/comparison-page-verify-provenance.py
# Records the events.json sha256 change (only 7 notesJa strings) so that frames/manifest.json, which keeps the
# pre-patch sha256, stays explicitly traceable. Keeps the file's own serialization (indent 1, CRLF, no final newline).
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
REPO = Path(__file__).resolve().parents[3]
BASE = REPO / 'artifacts' / 'event-comparison-2026-10-22'
VJ = BASE / 'verify.json'
raw = VJ.read_bytes()
v = json.loads(raw.decode('utf-8'))
assert json.dumps(v, ensure_ascii=False, indent=1).replace('\n', '\r\n').encode('utf-8') == raw, 'serialization would change'

ev_sha = hashlib.sha256((BASE / 'events.json').read_bytes()).hexdigest()
frames = json.loads((BASE / 'frames' / 'manifest.json').read_text(encoding='utf-8'))
sch = json.loads((BASE / 'schematic' / 'schematics.json').read_text(encoding='utf-8'))
PREV = 'f8bc079d38cc75ec28fee2a3cea0814286b20468a8a4027e1ed9873f98d055d0'
assert frames['events']['sha256'] == PREV and sch['events']['sha256'] == ev_sha != PREV, (frames['events'], sch['events'], ev_sha)

v['round3Recheck'] = {
    'date': '2026-10-22',
    'finding': ('R3-01 (review round 3): the retracted k1 claim ("about 0.15 s late on every 7/30 lap") survived in the 7 k1 '
                'top-view PNGs (build_schematics.py note) and in events.json notesJa (build_events.py); the page only hid it '
                'through build_page.py NOTE_MAP. R3-02: the page summary reported the image check for 7/30 only.'),
    'eventsJson': {
        'previousSha256': PREV,
        'newSha256': ev_sha,
        'change': ('notesJa of the 7 events contact-0730-L1-k1, -L2-k1, -L3-k1, -L4-k1, -L5-k1-1, -L5-k1-2, -L6-k1 replaced '
                   'in place (string replacement; every other field and byte unchanged, asserted by a field-by-field '
                   'comparison) with build_events.py K1_NOTE_JA: 5 of 6 laps late by 0.09-0.36 s, L6 0.12 s early, '
                   'L5 counted at its first vibration (0.41 s early at the second). Numbers = discrepancy.dtFirstBlock.'),
        'patchScript': 'artifacts/review-loop-2026-10-22/round-3/comparison-page-patch-events.py',
        'builderDryRun': ('scripts/build_events.py (with the new K1_NOTE_JA and its data assertions) run into tmp/round3-impl/dryrun/out: '
                          'output equals the patched events.json except "generated".'),
        'framesManifest': ('frames/manifest.json still records previousSha256: the frames depend only on the timing block, '
                           'which did not change, so they were NOT re-extracted. build_page.py asserts this chain.'),
    },
    'schematics': {
        'run': 'scripts/build_schematics.py --only <7 k1 ids>, then a full run (106 events, 0 failures) to refresh schematics.json',
        'changedPngs': 7,
        'unchangedPngs': ('the other 99 event PNGs and legend_top.png are byte-identical to the 19:55 files '
                          '(sha256 before/after in tmp/round3-impl/)'),
        'note': ('k1 card note now built from events.json and asserted equal to: 注: k1は3DCGのブロック線の先端形状で乗った瞬間の'
                 '距離が大きく出る（7/30は6周中5周で0.09〜0.36秒遅れ、L6は0.12秒早い。個別確認待ち）'),
        'manifestEventsSha256': sch['events']['sha256'],
    },
    'page': ('R3-02: the summary paragraph adds the 7/29 clipping result (9 usable of 14, corrected closer 4, uncorrected '
             'closer 5, inconclusive), recomputed by build_page.py from round2Recheck.adversarialRerun0729 metrics.json with '
             'the pair rule and asserted against countsPairRule.clipping0729; qa_page.mjs checks the sentence and the '
             'absence of the retracted k1 wording.'),
}
VJ.write_bytes(json.dumps(v, ensure_ascii=False, indent=1).replace('\n', '\r\n').encode('utf-8'))
print('verify.json round3Recheck written; events sha', ev_sha)
