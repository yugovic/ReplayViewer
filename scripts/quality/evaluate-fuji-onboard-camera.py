from pathlib import Path
import importlib.util,json,hashlib,math
import numpy as np
path=Path('scripts/quality/calibrate-fuji-onboard-camera.py'); spec=importlib.util.spec_from_file_location('camera_fit',path);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
f=json.loads((c.OUT/'camera-fit.json').read_text(encoding='utf8'));p=f['profile'];theta=[p['f'],p['pitchDeg'],p['yawDeg'],p['rollDeg']]
epath=c.OUT/'evidence/heldout-boundary-picks.json';src=json.loads(epath.read_text(encoding='utf8'))
rows=[dict(id=s['id'],lines={'paintL' if a['side']=='left' else 'paintR':a['points'] for a in s['annotations']}) for s in src['shots']]
assert set(r['id'] for r in rows)==set(f['heldoutSceneIds'])
result=c.metrics(rows,theta)
for r in rows:c.draw(r,theta)
before=[r['before']['medianPx'] for r in result];after=[r['after']['medianPx'] for r in result]
agg={'scenes':len(rows),'points':sum(r['before']['points'] for r in result),'beforeSceneMedianPx':float(np.median(before)),'afterSceneMedianPx':float(np.median(after)),'beforeSceneP95Px':float(np.percentile(before,95)),'afterSceneP95Px':float(np.percentile(after,95)),'improvedScenes':sum(b>a for b,a in zip(before,after)),'improvementPercent':float((1-np.median(after)/np.median(before))*100),'regressionsOver5Px':[r['id'] for r in result if r['improvementPx']< -5]}
for s in f['sensitivity']:s['heldout']=c.metrics(rows,s['parameters'],s['heightMeters'])
report={'aggregate':agg,'scenes':result,'parametersFrozenBeforeHoldout':True,'annotationsSha256':hashlib.sha256(epath.read_bytes()).hexdigest(),'profileSha256':hashlib.sha256((c.OUT/'camera-profile.json').read_bytes()).hexdigest(),'passCameraFramingGate':agg['improvedScenes']>=8 and agg['improvementPercent']>=30 and not agg['regressionsOver5Px'] and not any(f['activeBounds']),'limitations':['Conditional image alignment against current course, not physical camera/GPS calibration.','Manual boundary uncertainty around4px is sensitivity not confidence interval.','10 scenes,98correlated pixel points; scenes weighted equally.','Pinhole approximation omits lens distortion; camera height1.25m remains assumed.','No GPS/time/course/per-frame fitting.'],'heightSensitivity':f['sensitivity']}
(c.OUT/'heldout-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'aggregate':agg,'scenes':result,'passCameraFramingGate':report['passCameraFramingGate']},indent=2))