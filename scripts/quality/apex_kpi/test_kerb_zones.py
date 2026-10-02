"""Data-contract tests for public/data/tracks/fuji/cg_study/kerb_zones.json (no ortho needed).

Run: pipeline/.venv-sr/Scripts/python.exe -m pytest scripts/quality/apex_kpi/test_kerb_zones.py -q
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'pipeline'))

ZONES = ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json'
GEOMETRY = ROOT / 'public/data/tracks/fuji/cg_study/geometry.json'
INSIDE = ['k1', 'k3', 'k7', 'k9', 'k10', 'k13', 'k15', 'k18', 'k19', 'k20', 'k21', 'k23', 'k24', 'kDunlopR']


def inward(xy, side, stations, road):
    s = np.asarray(xy, float)
    t = np.gradient(s, axis=0); t /= np.linalg.norm(t, axis=1)[:, None]
    n = np.column_stack([-t[:, 1], t[:, 0]])
    ref = road[np.round(np.asarray(stations) / 0.5).astype(int), 3:5]
    ref = ref if side == 'left' else -ref
    return n * np.sign(np.sum(n * ref, axis=1))[:, None]


class KerbZonesContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.z = json.loads(ZONES.read_text(encoding='utf-8'))
        cls.g = json.loads(GEOMETRY.read_text(encoding='utf-8'))
        cls.road = np.array(cls.g['road'], float)
        cls.by = {k['id']: k for k in cls.z['kerbs']}

    def test_header_and_provenance(self):
        z = self.z
        self.assertEqual(z['trackId'], 'fuji')
        self.assertIs(z['surveyTruth'], False)
        self.assertIn('CC BY 4.0', z['source'])
        self.assertIn('2019', z['source'])
        self.assertIn('not verified', z['source'])
        for key in ('version', 'definition', 'kerbs', 'limitations'):
            self.assertIn(key, z)

    def test_geometry_sha_is_current(self):
        self.assertEqual(self.z['geometrySha256'], hashlib.sha256(GEOMETRY.read_bytes()).hexdigest(),
                         'geometry.json changed: rebuild kerb_zones.json (kerb_zones_v3.py + kerb_zones_validate.py)')

    def test_ids_sides_and_station_grid(self):
        ids = [k['id'] for k in self.z['kerbs']]
        self.assertEqual(ids, [f'k{i}' for i in range(25)] + ['kDunlopR'])
        for i, c in enumerate(self.g['curbs']):
            kb = self.by[f'k{i}']
            self.assertEqual(kb['side'], c['side'])
            self.assertFalse(kb['addedOutsideCg'])
            self.assertEqual(kb['station'], [r[0] for r in c['rows']])
        d = self.by['kDunlopR']
        self.assertTrue(d['addedOutsideCg']); self.assertEqual(d['side'], 'right')
        self.assertTrue(2955 <= d['station'][0] < 2970 and 2990 < d['station'][-1] <= 3010)
        for kb in self.z['kerbs']:
            st = np.array(kb['station'])
            np.testing.assert_allclose(np.diff(st), 0.5)
            np.testing.assert_allclose(st * 2, np.round(st * 2))
            for key in ('paint', 'block', 'outer'):
                a = np.array(kb[key], float)
                self.assertEqual(a.shape, (len(st), 2))
                self.assertTrue(np.isfinite(a).all())
                np.testing.assert_allclose(a, np.round(a, 3), atol=1e-9)

    def test_line_order_paint_block_outer(self):
        for kb in self.z['kerbs']:
            st = np.array(kb['station']); p = np.array(kb['paint']); b = np.array(kb['block']); o = np.array(kb['outer'])
            n = inward(p, kb['side'], st, self.road)
            strip = np.sum((p - b) * n, axis=1)          # block lies outward of paint by the white strip
            band = np.sum((b - o) * n, axis=1)
            ok = np.ones(len(st), bool)
            for g0, g1 in kb['observed']['blockGaps']:
                ok &= ~((st >= g0) & (st <= g1))
            self.assertTrue(np.all((strip[ok] > 0.15) & (strip[ok] < 0.75)), (kb['id'], strip[ok].min(), strip[ok].max()))
            self.assertTrue(np.all((band[ok] > 0.3) & (band[ok] < 2.3)), (kb['id'], band[ok].min(), band[ok].max()))

    def test_double_row_kerbs_are_smooth(self):
        for kid in ('k15', 'k23'):
            kb = self.by[kid]
            p = np.array(kb['paint']); b = np.array(kb['block'])
            rows = np.array(self.g['curbs'][int(kid[1:])]['rows'], float)
            n = inward(p, kb['side'], kb['station'], self.road)
            strip = np.sum((p - b) * n, axis=1)
            cg = np.sum((p - rows[:, 1:3]) * n, axis=1)  # CG inner line zig-zags between the two rows
            self.assertLess(np.std(strip), 0.06, kid)
            self.assertGreater(np.std(cg), 0.2, kid)

    def test_embedded_validation_meets_acceptance(self):
        v = self.z.get('validation')
        self.assertIsNotNone(v, 'run kerb_zones_validate.py --write-back')
        self.assertEqual(v['insideKerbs'], INSIDE)
        self.assertEqual(v['insidePass'], f'{len(INSIDE)}/{len(INSIDE)}')
        for kid in INSIDE:
            chk = self.by[kid]['check']
            self.assertTrue(chk['accepted'], kid)
            for key in ('paint', 'block'):
                med, q1, q3, n = chk[key]
                self.assertGreaterEqual(n, 5)
                self.assertLessEqual(abs(med), 0.15); self.assertGreaterEqual(q1, -0.15); self.assertLessEqual(q3, 0.15)

    def test_consumer_zoned_limits(self):
        import register_gps_to_track as base
        import register_gps_kerb_contact as reg
        limits = base.load_limits(ROOT / 'public/data/tracks/fuji')
        zones = reg.load_zones(ROOT / 'public/data/tracks/fuji/cg_study/kerb_zones.json')
        out = reg.zoned_limits(limits, zones)
        self.assertEqual(len(zones), 26)
        d = self.by['kDunlopR']
        i = int(round(d['station'][10] / 0.5))
        np.testing.assert_allclose(out.road_right.vertices[i], d['paint'][10])
        np.testing.assert_allclose(out.dry_right.vertices[i], d['outer'][10])


if __name__ == '__main__':
    unittest.main()
