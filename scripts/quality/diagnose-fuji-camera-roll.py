from pathlib import Path
import json,importlib.util,numpy as np
from scipy.spatial.transform import Rotation
sp=importlib.util.spec_from_file_location('c','scripts/quality/calibrate-fuji-onboard-camera.py');c=importlib.util.module_from_spec(sp);sp.loader.exec_module(c)
f=json.loads((c.OUT/'camera-fit.json').read_text(encoding='utf8'));held=json.loads((c.OUT/'evidence/heldout-boundary-picks.json').read_text(encoding='utf8'));rows=[{'id':s['id'],'lines':{'paintL' if a['side']=='left' else 'paintR':a['points'] for a in s['annotations']}} for s in held['shots']]
original=c.project;rolls=[]
for s in c.SHOTS.values():
 q=Rotation.from_quat(s['quaternion']);fw=q.apply([0,0,1]);r=q.apply([-1,0,0]);rr=np.cross(fw,[0,1,0]);rr/=np.linalg.norm(rr);uu=np.cross(rr,fw);rolls.append({'id':s['id'],'rollDeg':float(np.degrees(np.arctan2(np.dot(r,uu),np.dot(r,rr))))})
def neutral(points,shot,params,height=1.25,baseline=False):
 if baseline:return original(points,shot,params,height,baseline)
 q=Rotation.from_quat(shot['quaternion']);fw=q.apply([0,0,1]);rr=np.cross(fw,[0,1,0]);rr/=np.linalg.norm(rr);uu=np.cross(rr,fw);s=dict(shot,quaternion=Rotation.from_matrix(np.column_stack([-rr,uu,fw])).as_quat())
 return original(points,s,params,height,False)
c.project=neutral;r=c.fit(1.25);primary=c.metrics(rows,r.x);train=c.metrics(c.TRAIN,r.x)
seqposes=json.loads((c.OUT/'evidence/sequence/runtime-poses.json').read_text(encoding='utf8'));seqanns=json.loads((c.OUT/'evidence/sequence/sequence-paint-lines.json').read_text(encoding='utf8'))
seqrows=seqanns if isinstance(seqanns,list) else seqanns.get('shots',seqanns.get('frames'))
for row in seqposes['shots']:
 s=dict(row['poses'][0],id=row['id']);c.SHOTS[row['id']]=s;c.LOCAL[row['id']]={}
 for name,line in c.G['lines'].items():c.LOCAL[row['id']][name]=np.array([x['point'] for x in line if -5<((x['s']-s['station']+c.G['trackLength']/2)%c.G['trackLength']-c.G['trackLength']/2)<180])
seq=c.metrics(seqrows,r.x)
res={'scope':'Exploratory camera roll ablation after original holdouts were seen. NOT adopted, no new acceptance claim. Same3trainingframes fit globals again; geometry/GPS/time frozen.','parameters':r.x.tolist(),'bounds':r.active_mask.tolist(),'training':train,'originalHeldout':primary,'diagnosticSequence':seq,'productionRoll':rolls}
(c.OUT/'roll-ablation.json').write_text(json.dumps(res,indent=2),encoding='utf8');print(json.dumps({'parameters':r.x.tolist(),'bounds':r.active_mask.tolist(),'heldout':[(s['id'],round(s['after']['medianPx'],2)) for s in primary],'sequence':[(s['id'],round(s['after']['medianPx'],2)) for s in seq],'productionRoll':rolls},indent=2))