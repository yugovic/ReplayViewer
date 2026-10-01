"""Ablation and nuisance-parameter audit. Every alternative is re-fitted.

This measures track-limit objective sensitivity, never surveyed position error.
"""
from pathlib import Path
import importlib.util, json, sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/gps-accuracy-2026-09-22/registration'
BASE=ROOT/'artifacts/gps-accuracy-2026-09-22/baseline'
sys.path.insert(0,str(ROOT/'pipeline'))
import register_gps_to_track as reg
spec=importlib.util.spec_from_file_location('baseline_reg',BASE/'pipeline/register_gps_to_track.py')
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
track=json.loads((ROOT/'public/data/tracks/fuji/track.json').read_text())
geometry=reg.load_vehicle_geometry(ROOT/'public/data/vehicles/mazda2-dj.json')
limits=reg.load_limits(ROOT/'public/data/tracks/fuji')
old_limits=old.load_limits(BASE/'public/data/tracks/fuji')
OUT.mkdir(parents=True,exist_ok=True)

def load(race,lim,geom=None,antenna=(0,0)):
 path=ROOT/'public/data/races'/race
 idx=json.loads((path/'laps.json').read_text())
 laps=[]
 for r in idx['selected']:
  p=path/r['data_file'];d=json.loads(p.read_text())
  t=np.array(d['t']);xy=reg.to_local(d['lat'],d['lng'],track['origin'])
  station=lim.dist[lim.lateral(xy)[0]]
  laps.append(reg.LapInput(r,t,xy,reg.wheel_points(xy,t,antenna,geom),reg.racing_mask(lim,xy,t),station,reg.sha256(p)))
 return laps

def fit(laps,lim,curb):
 d,_=reg.solve(lambda q:sum(reg.lap_cost(q,l,lim,curb,0) for l in laps)+reg.SESSION_EPS*np.dot(q,q))
 return d

report={'definition':'Re-fitted session translation sensitivity; no absolute error estimate.',
 'inputs':{'limitsSha256':limits.source_sha256,'trackSha256':reg.sha256(ROOT/'public/data/tracks/fuji/track.json'),'vehicleGeometry':geometry},'races':{}}
for race,curb in [('fuji_aim_01',0),('fuji_aim_2020_07_30',1)]:
 shipped=json.loads((ROOT/'public/data/races'/race/'gps_registration.json').read_text())
 previous=json.loads((BASE/'public/data/races'/race/'gps_registration.json').read_text())
 row={'ablation':{'baseline':previous['sessionOffsetMeters'],'final':shipped['sessionOffsetMeters']},'antennaSensitivity':{},'surfaceSensitivity':{},'commonMetricLaps':[]}
 for label,lim,geom in [('continuousPreprocessingOnly',old_limits,None),('plusMazda2Geometry',old_limits,geometry)]:
  row['ablation'][label]=fit(load(race,lim,geom),lim,curb).round(4).tolist()
  print(race,label,row['ablation'][label],flush=True)
 for ar,af in [(0,0),(.5,0),(-.5,0),(0,1),(0,-1)]:
  key=f'right={ar},forward={af}'
  row['antennaSensitivity'][key]=fit(load(race,limits,geometry,(ar,af)),limits,curb).round(4).tolist()
  print(race,key,row['antennaSensitivity'][key],flush=True)
 for use in [0,1]:
  row['surfaceSensitivity'][str(use)]=fit(load(race,limits,geometry),limits,use).round(4).tolist()
 for lap,before,after in zip(load(race,limits,geometry),previous['laps'],shipped['laps']):
  row['commonMetricLaps'].append({'lap':lap.record['lap'],'oldOffsetOnFinalInputs':reg.violation_summary(lap,limits,before['offsetMeters'],curb),'newOffsetOnFinalInputs':reg.violation_summary(lap,limits,after['offsetMeters'],curb)})
 report['races'][race]=row
 (OUT/'sensitivity-audit.json').write_text(json.dumps(report,indent=2)+'\n')
a,b=[report['races'][r] for r in ['fuji_aim_01','fuji_aim_2020_07_30']]
report['dayDifferenceMeters']={key:float(np.linalg.norm(np.array(a['ablation'][key])-b['ablation'][key])) for key in a['ablation']}
report['dayDifferenceSameSurfaceMeters']={key:float(np.linalg.norm(np.array(a['surfaceSensitivity'][key])-b['surfaceSensitivity'][key])) for key in ['0','1']}
report['dayDifferenceCommonAntennaMeters']={key:float(np.linalg.norm(np.array(a['antennaSensitivity'][key])-b['antennaSensitivity'][key])) for key in a['antennaSensitivity']}
report['limitations']=['Antenna offsets are sensitivity scenarios, not measurements and not applied to shipped data.','Same-surface test separates changed kerb-use assumptions from day-dependent fitted translation.','Ablation is order-dependent; estimated translations absorb common map/antenna bias.']
(OUT/'sensitivity-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k.startswith('dayDifference')},indent=2))
