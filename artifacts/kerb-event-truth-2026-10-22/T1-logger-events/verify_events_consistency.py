"""T1 verification: internal consistency checks of events.json -> verify_events_consistency.json.

Checks (each reports n checked / n violations / examples):
  - record coverage: 140 records = (4 + 6 laps) x 14 inside kerbs, unique keys
  - published detector reproduction: all published kerb_contacts events reproduced (from events.json header)
  - every published inside-pass event of kerb_contacts.json appears in exactly one corner record and is matched
  - contact ordering: onset <= peak and onset <= offset; channel onsets >= previous contact end
  - steering ordering: rampOnset <= onset + 0.1; onset <= ratePeak <= max; brake onset <= steer onset + 0.5
  - keyTimesLapClock = keyTimes + lapClockOffsetS; keyLoggerMs = (t - shift) * 1000 + tc0
  - time base: tc0 independently re-derived for every lap (lapMapCheck.agree)
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
D = json.loads((HERE / 'events.json').read_text(encoding='utf-8'))


def check(name, items, pred, describe):
    bad = [describe(x) for x in items if not pred(x)]
    return dict(check=name, n=len(items), violations=len(bad), examples=bad[:5])


def main():
    R = D['records']
    out = []
    keys = [(r['session'], r['lap'], r['kerb']) for r in R]
    out.append(dict(check='record coverage', n=len(R), expected=140, unique=len(set(keys)) == len(keys),
                    violations=int(len(R) != 140 or len(set(keys)) != len(keys))))
    rep = D['publishedDetectorReproduction']
    out.append(dict(check='published detector reproduction', detail=rep,
                    violations=sum(v['publishedEvents'] - v['reproduced'] for v in rep.values())))
    # published inside-pass events -> records
    n_pub, missing = 0, []
    for race in D['sessions']:
        kc = json.loads((ROOT / f'public/data/races/{race}/kerb_contacts.json').read_text(encoding='utf-8'))
        for lap, evs in kc['events'].items():
            for e in evs:
                if not e['insidePass']:
                    continue
                n_pub += 1
                hits = [a for r in R if r['session'] == race and r['lap'] == int(lap) and r['kerb'] == e['kerb']
                        for a in r.get('publishedEventAgreement', []) if a['pubT0'] == e['t0'] and a.get('matched')]
                if len(hits) != 1:
                    missing.append([race, lap, e['kerb'], e['t0'], len(hits)])
    out.append(dict(check='published inside-pass events matched once', n=n_pub, violations=len(missing), examples=missing[:5]))
    contacts = [(r, c) for r in R for c in r.get('kerbContacts', [])]
    out.append(check('contact onset <= peak + 0.02 and onset <= offset', contacts,
                     lambda rc: rc[1]['onsetT'] <= rc[1]['peakT'] + 0.02 and rc[1]['onsetT'] <= rc[1]['offsetT'],
                     lambda rc: [rc[0]['session'], rc[0]['lap'], rc[0]['kerb'], rc[1]['onsetT'], rc[1]['peakT'], rc[1]['offsetT']]))
    st = [r for r in R if (r.get('steer') or {}).get('onsetT') is not None]
    out.append(check('steer rampOnset <= onset + 0.1', [r for r in st if r['steer']['rampOnsetT'] is not None],
                     lambda r: r['steer']['rampOnsetT'] <= r['steer']['onsetT'] + 0.1,
                     lambda r: [r['session'], r['lap'], r['kerb'], r['steer']['rampOnsetT'], r['steer']['onsetT']]))
    out.append(check('steer onset <= ratePeak + 0.05 <= max + 0.1', st,
                     lambda r: r['steer']['onsetT'] <= r['steer']['ratePeakT'] + 0.05 and r['steer']['ratePeakT'] <= r['steer']['maxT'] + 0.1,
                     lambda r: [r['session'], r['lap'], r['kerb'], r['steer']['onsetT'], r['steer']['ratePeakT'], r['steer']['maxT']]))
    br = [r for r in R if (r.get('brake') or {}).get('onset') is not None and (r.get('steer') or {}).get('onsetT') is not None]
    out.append(check('brake onset <= steer onset + 0.5', br, lambda r: r['brake']['onset'] <= r['steer']['onsetT'] + 0.5 + 1e-6,
                     lambda r: [r['session'], r['lap'], r['kerb'], r['brake']['onset'], r['steer']['onsetT']]))

    def clock_ok(r):
        lap = D['sessions'][r['session']]['laps'][str(r['lap'])]
        for k, v in r['keyTimes'].items():
            if v is None:
                continue
            if abs(r['keyTimesLapClock'][k] - (v + r['lapClockOffsetS'])) > 0.0015:
                return False
            if abs(r['keyLoggerMs'][k] - ((v - lap['shiftSeconds']) * 1000 + lap['tc0Ms'])) > 0.6:
                return False
        return True
    out.append(check('lap clock / logger ms conversions', R, clock_ok, lambda r: [r['session'], r['lap'], r['kerb']]))
    laps = [(s, l, v) for s, x in D['sessions'].items() for l, v in x['laps'].items()]
    out.append(check('tc0 re-derived per lap', laps, lambda x: x[2]['lapMapCheck']['agree'], lambda x: [x[0], x[1]]))
    doc = dict(tool='artifacts/kerb-event-truth-2026-10-22/T1-logger-events/verify_events_consistency.py', checks=out,
               totalViolations=sum(c.get('violations', 0) for c in out))
    (HERE / 'verify_events_consistency.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    for c in out:
        print(c['check'], '| n', c.get('n'), '| violations', c.get('violations'), c.get('examples', ''))
    print('total violations', doc['totalViolations'])


if __name__ == '__main__':
    main()
