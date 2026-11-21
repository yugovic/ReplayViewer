"""T3 step 1: replay (3DCG) timing of every inside-kerb pass and its video PTS window.

For each lap and inside kerb (build_apex_kpi INSIDE list) computes, from the shipped registered GPS path:
  - lap t / lap time T when the car reference point (GPS antenna, assumed vehicle centre) passes the kerb's
    first and last station (kerb_zones.json), and the replay apex (apex_kpi.json apexT / apexStation)
  - the video PTS window [s0 - 70 m, s1 + 15 m] to decode
Output: passes_windows.json next to this file (numbers only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main():
    course = C.Course()
    zones = C.load_zones()
    reads = C.timer_reads()
    rows = []
    for race in C.RACES:
        idx = json.loads((C.ROOT / f'public/data/races/{race}/laps.json').read_text(encoding='utf-8'))
        apex = json.loads((C.ROOT / f'public/data/races/{race}/apex_kpi.json').read_text(encoding='utf-8'))
        for sel in idx['selected']:
            lap = int(sel['lap'])
            L = C.Lap(race, lap, course)
            p0 = C.lap_start_pts(race, lap, L.meta, reads)
            ak = {r['kerb']: r for r in apex['laps'][str(lap)]}
            for kid in C.INSIDE:
                z = zones[kid]
                s0, s1 = float(z['station'][0]), float(z['station'][-1])
                ta, tb = L.t_at_station(s0 - 70.0), L.t_at_station(s1 + 15.0)
                t0, t1 = L.t_at_station(s0), L.t_at_station(s1)
                if None in (ta, tb, t0, t1):
                    rows.append(dict(race=race, lap=lap, kerb=kid, status='outside-lap'))
                    continue
                a = ak.get(kid) or {}
                st0, st1 = L.at(t0), L.at(t1)
                rows.append(dict(
                    race=race, date=C.DATE[race], lap=lap, overlayLap=lap + 1, kerb=kid, name=C.NAMES[kid], side=z['side'],
                    kerbStationStart=s0, kerbStationEnd=s1, lapStartPts=round(p0, 4), delta=L.delta,
                    replay=dict(
                        tKerbStart=round(t0, 3), tKerbEnd=round(t1, 3),
                        lapTimeKerbStart=round(t0 + L.delta, 3), lapTimeKerbEnd=round(t1 + L.delta, 3),
                        speedKerbStartMps=round(st0['v'], 2), speedKerbEndMps=round(st1['v'], 2),
                        apexT=a.get('apexT'), apexLapTime=a.get('apexLapTime'), apexStation=a.get('apexStation'),
                        gBlockFront=a.get('gBlockFront'), gPaintFront=a.get('gPaintFront'),
                        contact=(a.get('contact') or {}).get('class')),
                    window=dict(t=[round(ta, 3), round(tb, 3)],
                                pts=[round(p0 + ta + L.delta, 3), round(p0 + tb + L.delta, 3)])))
    out = dict(
        kind='t3-pass-windows',
        definition='Replay timing of each inside-kerb pass from the shipped registered GPS path (lap t base = lap JSON t; '
                   'lap time T = t + meta.first_sample_after_lap_start_seconds). video PTS = lapStartPts + T, lapStartPts from '
                   'artifacts/video-position-gap-2026-10-02/video-timer-reads.json (ffmpeg PTS base, per-lap corrections).',
        stationSource='car reference point (registered GPS antenna, assumed vehicle centre) projected on cg_study/geometry.json road centre',
        rows=rows)
    (Path(__file__).resolve().parent / 'passes_windows.json').write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    for r in rows:
        if 'window' in r:
            print(r['race'][-5:], r['lap'], r['kerb'], r['side'], r['kerbStationStart'], r['kerbStationEnd'],
                  r['replay']['lapTimeKerbStart'], r['replay']['lapTimeKerbEnd'], r['window']['pts'], r['replay']['gBlockFront'], r['replay']['contact'])


if __name__ == '__main__':
    main()
