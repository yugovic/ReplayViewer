"""Tests for the opt-in kerb-contact registration candidate (BACKLOG GPS-REG-15).

Unit tests of pipeline/register_gps_kerb_contact.py (robust loss, robust edge sign, label list,
refusal rule) and the data contract of public/data/races/fuji_aim_2020_07_30/gps_registration_kerb.json
(the viewer loads it only with ?gps=kerb).

Run: pipeline/.venv-sr/Scripts/python.exe -m pytest scripts/quality/apex_kpi/test_kerb_registration.py -q
"""
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'pipeline'))
import register_gps_kerb_contact as R  # noqa: E402
import register_gps_to_track as base  # noqa: E402

RACE = 'fuji_aim_2020_07_30'
RACE_DIR = ROOT / 'public/data/races' / RACE
CANDIDATE = RACE_DIR / 'gps_registration_kerb.json'
WET_CANDIDATE = ROOT / 'public/data/races/fuji_aim_01/gps_registration_kerb.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RobustLoss(unittest.TestCase):
    def test_losses_agree_for_small_violations(self):
        for loss in ('squared', 'huber', 'soft_l1'):
            self.assertEqual(R.rho(0.0, loss, 0.15), 0.0)
            self.assertAlmostEqual(R.rho(0.01, loss, 0.15), 1e-4, delta=2e-6)

    def test_huber_and_soft_l1_grow_linearly(self):
        # beyond delta the slope is bounded by 2*delta (one contradicting label cannot dominate)
        for loss in ('huber', 'soft_l1'):
            slope = R.rho(3.0, loss, 0.15) - R.rho(2.0, loss, 0.15)
            self.assertLessEqual(slope, 2 * 0.15 + 1e-9)
        self.assertAlmostEqual(R.rho(1.0, 'huber', 0.15), 2 * 0.15 * 1.0 - 0.15 ** 2)
        self.assertGreater(R.rho(3.0, 'squared', 0.15) - R.rho(2.0, 'squared', 0.15), 4.9)

    def test_violation_hinges(self):
        p = R.Params()
        self.assertEqual(R.violation('strong', -0.3, p), 0.0)
        self.assertAlmostEqual(R.violation('strong', 0.2, p), 0.2)
        self.assertEqual(R.violation('silent', -0.1, p), 0.0)
        self.assertAlmostEqual(R.violation('silent', -0.6, p), 0.2)
        self.assertEqual(R.violation('silent', None, p), 0.0)


class RobustEdgeSign(unittest.TestCase):
    """Right road edge, driving north (-z; driver's right = +x). A kerb outer edge (x = 6.5) ends at
    z = -10 and the edge returns to the road edge (x = 5, tilting slightly east afterwards): a transverse
    step like the Dunlop-R kerb end in the zoned limits. For a point inside the road just past the step
    both the step segment and the next edge segment have their foot on the shared vertex (a tie);
    Boundary.signed_gap may then take the step segment's direction and report the point as beyond the
    limit, the robust sign (inward road normal) cannot."""

    def setUp(self):
        kerb = [[6.5, -z] for z in np.arange(0.0, 10.01, 0.5)]
        road = [[5.0 + 0.08 * k, -10.0 - 0.5 * k] for k in range(0, 21)]
        self.v = np.array(kerb + road)
        self.inward = np.tile([-1.0, 0.0], (len(self.v), 1))     # -normal for a right edge
        self.line = base.Boundary.from_vertices(self.v)

    def test_signs_around_the_step(self):
        cases = {(1.5, -10.05): +1, (6.0, -5.0): +1, (7.0, -5.0): -1, (5.6, -12.0): -1, (2.0, -15.0): +1}
        for (x, z), sign in cases.items():
            g = R.robust_signed_gap(self.line, self.inward, np.array([[x, z]]))[0]
            self.assertEqual(np.sign(g), sign, (x, z, g))

    def test_distance_matches_base(self):
        pts = np.array([[1.5, -10.05], [6.0, -5.0], [7.0, -5.0], [5.6, -12.0], [2.0, -15.0]])
        np.testing.assert_allclose(np.abs(R.robust_signed_gap(self.line, self.inward, pts)),
                                   np.abs(self.line.signed_gap(pts, -1)), atol=1e-9)

    def test_agrees_with_base_away_from_the_step(self):
        pts = np.array([[6.0, -5.0], [7.0, -5.0], [5.6, -14.0], [2.0, -16.0], [5.0, -2.0]])
        np.testing.assert_array_equal(np.sign(R.robust_signed_gap(self.line, self.inward, pts)),
                                      np.sign(self.line.signed_gap(pts, -1)))


class LabelList(unittest.TestCase):
    def setUp(self):
        self.zones = {'k21': dict(s0=3253.5, s1=3348.0), 'k13': dict(s0=875.5, s1=932.0)}
        self.laps = {2: SimpleNamespace(passes={'k21': {}, 'k13': {}}), 3: SimpleNamespace(passes={'k13': {}})}
        self.contacts = {'laps': {'2': [{'kerb': 'k21', 'class': 'strong'}, {'kerb': 'k13', 'class': 'strong'},
                                        {'kerb': 'k13', 'class': 'mild'}],
                                  '3': [{'kerb': 'k13', 'class': 'silent'}, {'kerb': 'k99', 'class': 'silent'},
                                        {'kerb': 'k21', 'class': 'silent'}]}}
        self.anom = R.KNOWN_GPS_ANOMALIES[RACE]

    def test_exclude_downweight_include(self):
        ex = R.build_labels(self.contacts, self.zones, self.laps, self.anom, 'exclude')
        by = {(l['lap'], l['kerb']): l for l in ex}
        self.assertFalse(by[(2, 'k21')]['used'])
        self.assertTrue(by[(2, 'k21')]['anomaly'])
        self.assertIn('excluded', by[(2, 'k21')]['reason'])
        self.assertTrue(by[(2, 'k13')]['used'])
        self.assertNotIn((2, 'k13', 'mild'), [(l['lap'], l['kerb'], l['class']) for l in ex])
        self.assertFalse(by[(3, 'k99')]['used'])        # unknown kerb id
        self.assertFalse(by[(3, 'k21')]['used'])        # no pass samples
        dw = {(l['lap'], l['kerb']): l for l in R.build_labels(self.contacts, self.zones, self.laps, self.anom, 'downweight', 0.25)}
        self.assertTrue(dw[(2, 'k21')]['used'])
        self.assertEqual(dw[(2, 'k21')]['weight'], 0.25)
        inc = {(l['lap'], l['kerb']): l for l in R.build_labels(self.contacts, self.zones, self.laps, self.anom, 'include')}
        self.assertEqual(inc[(2, 'k21')]['weight'], 1.0)
        self.assertTrue(inc[(2, 'k21')]['anomaly'])

    def test_wet_session_is_refused(self):
        """7/29 has a single strong contact: below the evidence floor, no candidate."""
        contacts = json.loads((ROOT / 'public/data/races/fuji_aim_01/kerb_contacts.json').read_text(encoding='utf-8'))
        strong = [r for rows in contacts['laps'].values() for r in rows if r['class'] == 'strong']
        self.assertLess(len(strong), R.MIN_STRONG_LABELS)
        self.assertFalse(WET_CANDIDATE.exists())


@unittest.skipUnless(CANDIDATE.exists(), 'candidate not generated')
class CandidateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CANDIDATE.read_text(encoding='utf-8')
        cls.doc = json.loads(cls.text)
        cls.index = json.loads((RACE_DIR / 'laps.json').read_text(encoding='utf-8'))
        cls.contacts = json.loads((RACE_DIR / 'kerb_contacts.json').read_text(encoding='utf-8'))

    def test_viewer_contract(self):
        d = self.doc
        self.assertEqual(d['kind'], 'lap-translation')
        self.assertEqual(d['raceId'], RACE)
        self.assertEqual(d['trackId'], 'fuji')
        self.assertEqual(d['limits']['source'], 'public/data/tracks/fuji/cg_study/geometry.json')
        self.assertEqual(d['limits']['sha256'], sha(ROOT / d['limits']['source']))
        self.assertEqual(d['referenceTrack']['source'], 'public/data/tracks/fuji/track.json')
        self.assertEqual(d['referenceTrack']['sha256'], sha(ROOT / d['referenceTrack']['source']))
        ant = d['method']['antennaOffsetMeters']
        self.assertEqual((ant['right'], ant['forward']), (0.0, 0.0))
        selected = {(s['vehicle_id'], int(s['lap'])): s['data_file'] for s in self.index['selected']}
        self.assertEqual({(l['vehicleId'], l['lap']) for l in d['laps']}, set(selected))
        for lap in d['laps']:
            self.assertEqual(lap['dataFile'], selected[(lap['vehicleId'], lap['lap'])])
            self.assertEqual(lap['dataSha256'], sha(RACE_DIR / lap['dataFile']), lap['lap'])
            self.assertEqual(len(lap['offsetMeters']), 2)
            self.assertTrue(all(np.isfinite(lap['offsetMeters'])))
            self.assertLessEqual(float(np.hypot(*lap['offsetMeters'])), 8.0)

    def test_opt_in_and_uncertainty(self):
        d = self.doc
        self.assertEqual(d['status'], 'candidate-kerb-contact-fit')
        self.assertFalse(d['optIn']['replacesShipped'])
        self.assertGreaterEqual(d['uncertainty']['absoluteGapIntervalMeters'], 0.7)
        self.assertFalse(d['uncertainty']['absolutePositionAccuracyVerified'])
        self.assertIn(d['method']['labelTerm']['loss'], ('huber', 'soft_l1'))

    def test_inputs_are_current(self):
        for key in ('kerbZones', 'kerbContacts', 'vehicleProfile'):
            item = self.doc['method']['inputs'][key]
            self.assertEqual(item['sha256'], sha(ROOT / item['source']), key)

    def test_explicit_label_list(self):
        listed = {(l['lap'], l['kerb'], l['class']) for l in self.doc['labelsUsed']}
        expected = {(int(lap), r['kerb'], r['class']) for lap, rows in self.contacts['laps'].items() for r in rows
                    if r['class'] in ('strong', 'silent')}
        self.assertEqual(listed, expected)
        for l in self.doc['labelsUsed']:
            if not l['used']:
                self.assertTrue(l['reason'])
        s = self.doc['labelSummary']
        self.assertEqual(s['strongUsed'], sum(l['used'] and l['class'] == 'strong' for l in self.doc['labelsUsed']))

    def test_anomaly_flagged_not_absorbed(self):
        anomalies = self.doc['gpsAnomalies']
        self.assertTrue(any(a['lap'] == 2 and a['stationRange'] == [3250.0, 3900.0] for a in anomalies))
        if self.doc['method']['anomalyTreatment'] == 'exclude':
            for l in self.doc['labelsUsed']:
                if l['anomaly']:
                    self.assertFalse(l['used'])
        lap2 = next(l for l in self.doc['laps'] if l['lap'] == 2)
        self.assertTrue(any('gps-anomaly' in f for f in lap2['flags']))

    def test_feasible_region_published(self):
        fr = self.doc['feasibleRegion']
        self.assertIn('session', fr)
        self.assertEqual(set(fr['laps']), {str(l['lap']) for l in self.doc['laps']})
        for e in [fr['session'], *fr['laps'].values()]:
            self.assertIn('trackNearOptimal', e)
            self.assertIn('labelFeasible_tol0.15', e)

    def test_no_local_paths(self):
        # drive letters (not URL schemes such as https://), user folders, temp dirs
        self.assertIsNone(re.search(r'(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\Users|/Users/|AppData', self.text))


class PrunedCostIsExact(unittest.TestCase):
    """Pruned track cost == full robust track cost; pruned kerb gap == full gap (real 7/30 data)."""

    def test_random_offsets(self):
        S = R.setup('fuji', RACE, 'dry', ROOT / 'public/data/vehicles/mazda2-dj.json')
        P, L = S['problem'], S['limits']
        rng = np.random.default_rng(1)
        lap = P.laps[4]
        W = lap.w10[lap.keep].reshape(-1, 2)
        for d in [S['ref_session'] + rng.normal(0, 1.5, 2) for _ in range(5)] + [S['ref_session'] + np.array([4.5, 0.0])]:
            gl = R.robust_signed_gap(L.dry_left, L.normal, W + d)
            gr = R.robust_signed_gap(L.dry_right, -L.normal, W + d)
            full = float(base.huber(np.maximum(0, -np.minimum(gl, gr))).sum())
            self.assertAlmostEqual(lap.track_cost(d), full, delta=1e-6 * (1 + full))
        lab = next(l for l in P.labels if l['used'] and l['lap'] == 4)
        ps = lap.passes[lab['kerb']]
        for d in [S['ref_session'] + rng.normal(0, 1.5, 2) for _ in range(5)]:
            g = R.line_gap(ps['zone']['block'], ps['all'] + d, ps['zone']['sign'])
            self.assertAlmostEqual(lap.gap(lab['kerb'], d), float(np.nanmin(g)), places=9)


if __name__ == '__main__':
    unittest.main()
