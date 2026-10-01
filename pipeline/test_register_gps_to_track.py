"""Regression tests for vehicle reference frames and rendered-boundary inputs."""
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import register_gps_to_track as reg

class RegistrationGeometryTests(unittest.TestCase):
    def setUp(self):
        self.geometry=reg.load_vehicle_geometry(reg.ROOT/'public/data/vehicles/mazda2-dj.json')

    def test_axles_have_distinct_outer_spans(self):
        xy=np.column_stack([np.arange(15,dtype=float),np.zeros(15)])
        pts=reg.wheel_points(xy,np.arange(15)*.1,geometry=self.geometry)[7]
        np.testing.assert_allclose(pts[:,0],[8.285,8.285,5.715,5.715])
        np.testing.assert_allclose(pts[:,1],[-.840,.840,-.8325,.8325])

    def test_antenna_is_body_fixed_and_rotates_with_heading(self):
        xy=np.column_stack([np.zeros(15),np.arange(15,dtype=float)])
        t=np.arange(15)*.1
        a=reg.wheel_points(xy,t,geometry=self.geometry)
        b=reg.wheel_points(xy,t,antenna=(.4,1.2),geometry=self.geometry)
        np.testing.assert_allclose(b-a,np.broadcast_to([.4,-1.2],a.shape),atol=1e-12)

    def test_legacy_dimensions_remain_available_without_profile(self):
        xy=np.column_stack([np.arange(15,dtype=float),np.zeros(15)])
        p=reg.wheel_points(xy,np.arange(15)*.1)[7]
        np.testing.assert_allclose(p[:,1],[-.85,.85,-.85,.85])
        self.assertAlmostEqual(p[0,0]-p[2,0],2.6)

    def test_rendered_world_edges_replace_candidate_offsets(self):
        with tempfile.TemporaryDirectory(dir=reg.ROOT/'artifacts') as d:
            path=Path(d); (path/'cg_study').mkdir()
            rows=[[i,float(i),0,0,1,-100,100,float(i),-4 if i==5 else -5,float(i),5] for i in range(12)]
            curb={'side':'left','rows':[[5,5,-4,5,-6,-99,99,1,1]]}
            (path/'cg_study/geometry.json').write_text(json.dumps({'road':rows,'stationStep':1,'curbs':[curb]}))
            lim=reg.load_limits(path)
            self.assertEqual(lim.left[5],-4) # no hidden median erasing the mesh
            self.assertEqual(lim.right[5],5)
            self.assertEqual(lim.curb_left[5],2)
            self.assertAlmostEqual(lim.margins(np.array([[5.,-5.]]),0)[0],-np.sqrt(.5))
            self.assertAlmostEqual(lim.margins(np.array([[5.,-5.]]),1)[0],np.sqrt(.5))

    def test_real_dry_lap_nearest_station_tie_is_continuous(self):
        track_dir=reg.ROOT/'public/data/tracks/fuji'
        race_dir=reg.ROOT/'public/data/races/fuji_aim_2020_07_30'
        track=json.loads((track_dir/'track.json').read_text(encoding='utf-8'))
        data=json.loads((race_dir/'osaki_hmr_demio_101_lap_001.json').read_text(encoding='utf-8'))
        fit=json.loads((race_dir/'gps_registration.json').read_text(encoding='utf-8'))
        xy=reg.to_local(data['lat'],data['lng'],track['origin'])
        wheels=reg.wheel_points(xy,np.asarray(data['t']),geometry=self.geometry)
        sample=wheels[330:331,0,:]+np.asarray(fit['laps'][0]['offsetMeters'])
        lim=reg.load_limits(track_dir)
        offsets=np.linspace(-.005,.005,41)
        gaps=np.array([lim.margins(sample+np.array([0,d]),1)[0] for d in offsets])
        self.assertLess(np.max(np.abs(np.diff(gaps))),.005)

    def test_invalid_profile_rejected(self):
        with tempfile.TemporaryDirectory(dir=reg.ROOT/'artifacts') as d:
            p=Path(d)/'bad.json'
            p.write_text(json.dumps({**self.geometry,'frontTyreWidthMeters':float('nan')}))
            with self.assertRaises(ValueError): reg.load_vehicle_geometry(p)

if __name__=='__main__': unittest.main()
