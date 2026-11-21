from pathlib import Path
import json, math, hashlib
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'artifacts/video-cg-alignment-2026-10-03'; SRC=ROOT/'artifacts/onboard-comparison-2026-10-03'
G=json.loads((OUT/'runtime-geometry.json').read_text(encoding='utf8')); M=json.loads((SRC/'manifest.json').read_text(encoding='utf8')); SHOTS={s['id']:s for s in G['shots']}
# Road-facing edge of white marking/kerb paint, picked from video alone before viewing CG overlay.
TRAIN=[
 {'id':'2020-07-29-L3-47.000','lines':{'paintL':[[244,290],[294,274],[350,259],[414,243],[474,230]],'paintR':[[992,254],[948,243],[900,232],[863,221],[849,214],[851,208]]}},
 {'id':'2020-07-29-L3-55.000','lines':{'paintR':[[704,286],[688,275],[673,264],[654,251],[636,239],[620,231]]}},
 {'id':'2020-07-29-L3-68.000','lines':{'paintL':[[244,277],[301,266],[357,254],[415,243],[472,233],[527,226],[578,220]],'paintR':[[995,266],[959,255],[918,244],[877,234],[837,225],[809,220]]}}
]
for row in TRAIN:
 p=SRC/'video'/f"{row['id']}.jpg"; row.update(role='train',frameSha256=hashlib.sha256(p.read_bytes()).hexdigest(),uncertaintyPixels=3,definition='Road-facing white paint boundary; manual video-only picks, no CG overlay.')
(OUT/'train-annotations.json').write_text(json.dumps({'frames':TRAIN,'heightMetersFixed':1.25,'cameraLateralFixed':0,'cameraForwardFixed':-.55,'fittedParameters':['f','pitchDeg','yawDeg','rollDeg'],'poseAndTime':'frozen from current runtime capture; no per-frame parameters'},indent=2))
def rot(yaw,pitch,roll):
 y,p,r=np.radians([yaw,pitch,roll]);cy,sy=np.cos(y),np.sin(y);cp,sp=np.cos(p),np.sin(p);cr,sr=np.cos(r),np.sin(r)
 return np.array([[cr,-sr,0],[sr,cr,0],[0,0,1]])@np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])@np.array([[cy,0,-sy],[0,1,0],[sy,0,cy]])
def project(points,shot,params,height=1.25,baseline=False):
 q=Rotation.from_quat(shot['quaternion']); fw=q.apply([0,0,1]);up=q.apply([0,1,0]);right=q.apply([-1,0,0])
 if baseline:
  pos=np.array(shot['beforeCamera']['position']);right=np.cross(fw,[0,1,0]);right/=np.linalg.norm(right);up=np.cross(right,fw);f=360/math.tan(math.radians(shot['beforeCamera']['fov']/2));R=np.stack([right,-up,fw])
 else:
  f,pitch,yaw,roll=params;pos=np.array(shot['position'])+up*height+fw*(-.55);R=rot(yaw,pitch,roll)@np.stack([right,-up,fw])
 cam=(points-pos)@R.T;z=cam[:,2];xy=cam[:,:2]/np.maximum(z[:,None],1e-5)*f+[640,360]
 return xy,z
LOCAL={}
for ident,shot in SHOTS.items():
 LOCAL[ident]={}
 for name,line in G['lines'].items():
  rows=[r for r in line if -5<((r['s']-shot['station']+G['trackLength']/2)%G['trackLength']-G['trackLength']/2)<180]
  LOCAL[ident][name]=np.array([r['point'] for r in rows])
def projected_segments(ident,name,p,height=1.25,baseline=False):
 xy,z=project(LOCAL[ident][name],SHOTS[ident],p,height,baseline);ok=(z[:-1]>2)&(z[1:]>2)&(z[:-1]<180)&(z[1:]<180)
 return xy[:-1][ok],xy[1:][ok]
def distances(points,a,b):
 v=b-a;den=np.sum(v*v,axis=1);q=np.asarray(points)[:,None,:]-a[None,:,:];t=np.clip(np.einsum('ijk,jk->ij',q,v)/np.maximum(den,1e-9),0,1)
 nearest=a[None,:,:]+t[:,:,None]*v[None,:,:];ds=np.linalg.norm(np.asarray(points)[:,None,:]-nearest,axis=2);idx=np.argmin(ds,axis=1)
 return ds[np.arange(len(points)),idx]
def residuals(p,rows,height=1.25,baseline=False):
 ans=[]
 for row in rows:
  vals=[]
  for name,points in row['lines'].items():
   a,b=projected_segments(row['id'],name,p,height,baseline);vals.extend(distances(points,a,b))
  ans.extend(np.array(vals)/math.sqrt(len(vals)))
 return np.array(ans)
def fit(height):
 return least_squares(residuals,[600,14,0,0],bounds=([430,5,-8,-6],[850,25,8,6]),args=(TRAIN,height),loss='soft_l1',f_scale=2,xtol=1e-10,ftol=1e-10,gtol=1e-10,max_nfev=700)
def metrics(rows,p,height=1.25):
 result=[]
 for row in rows:
  vals={}
  for mode in ['before','after']:
   ds=[]
   for name,points in row['lines'].items():
    a,b=projected_segments(row['id'],name,p,height,mode=='before');ds.extend(distances(points,a,b))
   vals[mode]={'medianPx':float(np.median(ds)),'p95Px':float(np.percentile(ds,95)),'points':len(ds)}
  result.append({'id':row['id'],**vals,'improvementPx':vals['before']['medianPx']-vals['after']['medianPx']})
 return result
def draw(row,p,height=1.25):
 im=Image.open(SRC/'video'/f"{row['id']}.jpg").convert('RGB');d=ImageDraw.Draw(im)
 for mode,col in [('before','#ff6b75'),('after','#00f1c8')]:
  for name in row['lines']:
   a,b=projected_segments(row['id'],name,p,height,mode=='before')
   for u,v in zip(a,b):
    if all(np.isfinite([*u,*v])) and np.max(np.abs([*u,*v]))<10000:d.line([tuple(u),tuple(v)],fill=col,width=2)
 for pts in row['lines'].values():
  for x,y in pts:d.ellipse((x-3,y-3,x+3,y+3),outline='#fff200',width=2)
 d.text((25,315),'PINK current cockpit / CYAN camera fit / YELLOW video-only observations',fill='white')
 im.save(OUT/f"overlay-{row['id']}.jpg",quality=94)
def main():
 r=fit(1.25);p=r.x;results=metrics(TRAIN,p)
 profile={'id':'fuji-smartycam-camera-fit-2026-10-03','label':'動画比較用・推定カメラ','calibrationStatus':'estimated','referenceWidth':1280,'referenceHeight':720,'f':float(p[0]),'cx':640,'cy':360,'rightMeters':0,'upMeters':1.25,'forwardMeters':-.55,'pitchDeg':float(p[1]),'yawDeg':float(p[2]),'rollDeg':float(p[3])}
 sensitivity=[]
 for height in [1.,1.25,1.5]:
  rr=r if height==1.25 else fit(height);sensitivity.append({'heightMeters':height,'parameters':rr.x.tolist(),'training':metrics(TRAIN,rr.x,height),'chosenByHoldout':False})
 data={'profile':profile,'training':results,'sensitivity':sensitivity,'activeBounds':r.active_mask.tolist(),'optimizerSuccess':bool(r.success),'jacobianSingularValues':np.linalg.svd(r.jac,compute_uv=False).tolist(),'method':'Four global pinhole camera parameters; fixed mounting height/right/forward; fixed production pose, GPS, course and lap time. Video points to projected labeled polylines. Scene weighting 1/sqrt(n). No per-frame changes.','trainSceneIds':[s['id'] for s in TRAIN],'heldoutSceneIds':[s for s in SHOTS if s not in {r['id'] for r in TRAIN}]}
 (OUT/'camera-fit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');(OUT/'camera-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf8')
 for row in TRAIN:draw(row,p)
 print(json.dumps({'profile':profile,'training':results,'activeBounds':r.active_mask.tolist()},ensure_ascii=False,indent=2))
if __name__=='__main__':main()