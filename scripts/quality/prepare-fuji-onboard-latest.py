from pathlib import Path
import json, hashlib, subprocess, datetime, re
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/onboard-comparison-2026-10-03'
OUT.mkdir(parents=True,exist_ok=True)
BASE=ROOT/'artifacts/improvement-eval-2026-10-02/baseline'
AUDIT=ROOT/'artifacts/video-position-gap-2026-10-02/video-timer-reads.json'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
 return h.hexdigest()
def tree_digest(root):
 rows=[(str(p.relative_to(root)).replace('\\','/'),sha(p)) for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in ('.ts','.tsx','.css')]
 return hashlib.sha256(json.dumps(rows).encode()).hexdigest(),len(rows)
wetMeta=json.loads((ROOT/'artifacts/fuji-onboard-2026-09-16/source.json').read_text('utf-8'))
wet=Path(wetMeta['source']); dry=wet.parent.parent/'20200730/SCHD0624.MOV'
cfg=json.loads(AUDIT.read_text('utf-8'))['sessions']
sets=[('fuji_aim_01','2020-07-29',3,wet,[47,52.387,55,60,64.367,68],.375),('fuji_aim_2020_07_30','2020-07-30',2,dry,[18,45,47.522,50,84,94,112],.270)]
media=[]
for race,date,lap,path,times,yawLag in sets:
 if not path.is_file():raise FileNotFoundError(path.name)
 digest=sha(path)
 if race=='fuji_aim_01' and digest!=wetMeta['sha256']:raise ValueError('wet video SHA mismatch')
 media.append({'raceId':race,'name':path.name,'sha256':digest,'size':path.stat().st_size,'permission':'User supplied; local frame extraction and comparison only. No public redistribution licence.','permissionEvidence':['artifacts/fuji-onboard-2026-09-16/source.json' if race=='fuji_aim_01' else str(AUDIT.relative_to(ROOT)).replace('\\','/'),'docs/fuji-video-position-gap-2026-10-02.md'],'processing':'ffmpeg accurate PTS frame extraction, scale/JPEG only; no AI enhancement.'})
(OUT/'source-metadata.json').write_text(json.dumps(media,ensure_ascii=False,indent=2),'utf-8')
sourceMap={r:p for r,d,l,p,t,y in sets}
shots=[]
for race,date,lap,path,times,yawLag in sets:
 dataPath=ROOT/f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json'
 data=json.loads(dataPath.read_text('utf-8'));meta=data['meta'];delta=meta['first_sample_after_lap_start_seconds'];c=cfg[race]
 wall=lambda s:datetime.datetime.fromisoformat(s.replace('Z',''))
 start=(wall(meta['lap_start_time'])-wall(c['referenceLapStart'])).total_seconds()+c['referencePtsSeconds']-c['perLapCorrectionSeconds'][str(lap)]
 for time in times:
  ident=f'{date}-L{lap}-{time:.3f}';pts=round(start+time,3);video=f'video/{ident}.jpg'
  shot={'id':ident,'raceId':race,'date':date,'lap':lap,'overlayLap':lap+1,'label':'ブレーキ時刻の比較' if time==47.522 else f'ラップ {time:.3f} 秒','time':time,'delta':delta,'videoPtsSeconds':pts,'beforePlaybackSeconds':time,'afterPlaybackSeconds':round(time-delta,6),'video':video,'cameras':{cam:{version:f'{version}/{ident}-{cam}.png' for version in ('before','after')} for cam in ('cockpit','chase')},'telemetry':{},'alignment':{'imageYawLagSeconds':yawLag,'uncertaintySeconds':.05},'latestLapSha256':sha(dataPath),'baselineLapSha256':sha(BASE/f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json') if (BASE/f'public/data/races/{race}/osaki_hmr_demio_101_lap_{lap:03d}.json').exists() else sha(dataPath)}
  shots.append(shot)
manifest={'version':1,'createdAt':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds'),'baselineLabel':'2026-10-02 改善前の保存スナップショット','latestLabel':'現在の作業ツリー（2026-10-03撮影）','comparisonDefinition':'同じ表示ラップ時刻Tで比較。実車載PTS=ラップ開始PTS+T。改善前の内部t=T、改善後の内部t=T−δ。補正候補gps=kerbは使わず、両版で既定GPS登録を有効。','provenance':media,'shots':shots,'qa':{'captureFps':60,'prerollSeconds':2,'captureMethod':'各対象時刻まで2秒を60fpsで決定的に進めた直後の描画を保存。再生時の姿勢追従を保持。','notes':['7/29の配信ラップはbaselineにないため、改善評価で不変が確認された現在のラップを共用。道路/車両/柵の共通資産も現在のpublicから読み、旧srcと旧7/30ラップだけを隔離。','PTSはffmpegの正確な入力シーク。旧OpenCV秒は使わない。GPS/映像ヨー差は比較に加算していない。']},'sourceAuditSha256':sha(AUDIT)}
for key,tree in [('beforeSource',BASE/'src'),('afterSource',ROOT/'src')]:
 digest,count=tree_digest(tree);manifest['qa'][key+'Sha256']=digest;manifest['qa'][key+'Files']=count
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
for shot in shots:
 target=OUT/shot['video'];target.parent.mkdir(parents=True,exist_ok=True)
 cmd=['ffmpeg','-hide_banner','-loglevel','info','-copyts','-ss',str(shot['videoPtsSeconds']),'-i',str(sourceMap[shot['raceId']]),'-vf','scale=1280:720,showinfo','-frames:v','1','-q:v','2','-update','1','-y',str(target)]
 proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace')
 if proc.returncode:raise RuntimeError(proc.stderr[-2000:])
 matched=re.search(r'\bn:\s*0\s+pts:.*?pts_time:([\d.]+)',proc.stderr)
 if matched:shot['decodedFramePtsSeconds']=float(matched.group(1))
 print(f"extracted {shot['id']} requestedPTS={shot['videoPtsSeconds']} decodedPTS={shot.get('decodedFramePtsSeconds')}",flush=True)
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
print('Prepared',len(shots),'scenes; source metadata written before extraction',flush=True)