"""Source-derived CG study. Only licensed native orthophoto, never AI tiles.

Cross-sections use editor edges as search priors, not fixed curb placement.
Outputs retain source provenance and distinguish inferred/interpolated samples.
"""
import json, math, hashlib, os
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import map_coordinates, median_filter, gaussian_filter1d, label
from scipy.signal import savgol_filter
from cg_geometry_checks import repair_folded_samples
from refine_fuji_curbs import refine_source_curbs, connect_road_to_curbs
from road_edge_candidates import select_white_road_run

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'public/data/tracks/fuji/cg_study'
AUDIT = ROOT / os.environ.get('FUJI_CG_AUDIT', 'artifacts/fuji-cg-2026-09-08')
OUT.mkdir(parents=True, exist_ok=True)
AUDIT.mkdir(parents=True, exist_ok=True)
source = ROOT / 'pipeline/cache/shizuoka_ortho/native_mosaic.png'
meta = json.loads(source.with_name('native_mosaic_meta.json').read_text())
assert 'CC BY' in meta['license'] and meta['sourceUrl']
im = Image.open(source).convert('RGB')
rgb = np.array(im)
W, H = im.size
track = json.loads((ROOT / 'public/data/tracks/fuji/track.json').read_text())
edges = json.loads((ROOT / 'public/data/tracks/fuji/road_edges.json').read_text())
pts = track['centerline']
dist = np.array([p['dist'] for p in pts])
xs = np.array([p['x'] for p in pts]); zs = np.array([p['z'] for p in pts])
b = meta['bbox']; o = track['origin']
north = np.arcsinh(np.tan(np.radians(b['maxLat'])))
south = np.arcsinh(np.tan(np.radians(b['minLat'])))

def pixel(x, z):
    lat = o['lat'] - z / 111320
    lng = o['lng'] + x / (111320 * math.cos(math.radians(o['lat'])))
    return ((lng-b['minLng'])/(b['maxLng']-b['minLng'])*W,
            (north-np.arcsinh(np.tan(np.radians(lat))))/(north-south)*H)

def center(d):
    x=np.interp(d,dist,xs); z=np.interp(d,dist,zs)
    # Smooth central tangent, shared by the runtime via explicit exported XZ.
    dx=np.interp(np.minimum(d+1,dist[-1]),dist,xs)-np.interp(np.maximum(d-1,0),dist,xs)
    dz=np.interp(np.minimum(d+1,dist[-1]),dist,zs)-np.interp(np.maximum(d-1,0),dist,zs)
    length=np.maximum(.001,np.hypot(dx,dz))
    return x,z,-dz/length,dx/length

def sample(x,z):
    # pixel() returns raster-edge coordinates; array index 0 is its centre.
    px,py=pixel(x,z)
    px,py=px-.5,py-.5
    return np.stack([map_coordinates(rgb[:,:,c], [py,px], order=1, mode='nearest') for c in range(3)],axis=-1)

if __name__ == '__main__':
    ds=np.arange(0,track['totalLength'],.5)
    x,z,nx,nz=center(ds)
    offset=np.arange(-2,5.01,.1)
    result = dict(version=1, stationStep=.5, road=[], curbs=[], green=[], source=meta['source'],
        sourceUrl=meta['sourceUrl'], license=meta['license'],
        sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        sourceImageSize=[W,H], nominalSourceResolutionMeters=.2,
        rasterSampling='BBox describes outer pixel edges; bilinear array indices subtract 0.5 on both axes.',
        surveyTruth=False, curbHeightMeters=.055,
        limitations=['Plan position is traced from 2019 orthophoto, not a current survey.',
        'Curb height/cross-section are illustrative, not measured.',
        'White/shadow gaps interpolate between observed red paint; inspect audit overlays.',
        'Road edges outside the hairpin are automatic candidates, not individually reviewed.'])
    # Reviewed on the licensed source, without reference to any GPS trajectory.
    # Other white marks exist closer to the editor prior than the road edge.
    # These short windows constrain candidate identity, not the final position.
    reviewed_windows = [
        dict(side='left', start=82., end=88., innerOffsetRange=[.6, 1.6],
             reason='Starting-grid white box competes with a continuously visible road-edge line.',
             evidence='review-left-85.png'),
        dict(side='right', start=1288., end=1296., innerOffsetRange=[.8, 1.8],
             reason='Isolated road-side white candidate before the curb competes with the continuous edge. Broad white curb onset can leave an unobserved section.',
             evidence='review-right-1291.png'),
    ]
    result['reviewedRoadCandidateWindows'] = reviewed_windows
    result['reviewedRoadCandidatePolicy'] = 'Select source-observed runs inside reviewed bands; missing paint interpolates between retained observations. No GPS input; not survey control.'
    all_edges={}; curb_stats=[]
    def smooth_plan(values):
        # Fit in WORLD coordinates, not lateral offsets along a noisy tangent.
        # 5.5m quadratic window retains corner curvature. Limit correction to
        # 0.25m; the source's nominal 20cm pixels cannot justify finer truth.
        smooth=savgol_filter(values,11,2,axis=0,mode='interp')
        delta=smooth-values; length=np.linalg.norm(delta,axis=1)
        delta*=np.minimum(1,.25/np.maximum(length,1e-9))[:,None]
        return values+delta
    editor=json.loads((ROOT/'track-creator/tracks/fuji/track.json').read_text())
    for side,sign in [('left',-1),('right',1)]:
        e=np.interp(ds,np.arange(len(edges[side]))*edges['stepMeters'],edges[side])
        lateral=sign*(e[:,None]+offset[None,:])
        strip=sample(x[:,None]+nx[:,None]*lateral,z[:,None]+nz[:,None]*lateral)
        np.savez(AUDIT / ('fuji-cg-'+side+'.npz'),ds=ds,offset=offset,strip=strip,edge=e)
        f=strip.astype(float); r,g,bl=f[:,:,0],f[:,:,1],f[:,:,2]
        red=(r-g>22)&(r-bl>18)&(r>120)&(g<175)
        white=(np.min(f,axis=2)>155)&(np.ptp(f,axis=2)<38)
        # A measured narrow white marking, or the innermost white curb paint.
        candidate=np.full(len(ds),np.nan)
        occluded_white=[]
        for i in range(len(ds)):
            # Gantries/transverse markings are bright across several metres.
            # They occlude the road edge; they are not a new road boundary.
            if np.count_nonzero(white[i])>20:
                occluded_white.append(float(ds[i]));continue
            labs,n=label(white[i] & (offset<3))
            runs=[np.flatnonzero(labs==j) for j in range(1,n+1)]
            # A run cut off by the search window has no observed inner edge.
            runs=[q for q in runs if 2<=len(q)<=11 and q[0]>0 and offset[q[-1]]<2.9]
            reviewed = next((review for review in reviewed_windows
                             if review['side'] == side and review['start'] <= ds[i] <= review['end']), None)
            q = select_white_road_run(runs, offset, reviewed['innerOffsetRange'] if reviewed else None)
            if q is not None:
                candidate[i]=offset[q[0]]+.08
        good=np.isfinite(candidate)
        for review in reviewed_windows:
            if review['side'] == side:
                review['unobservedStations'] = ds[(ds >= review['start']) & (ds <= review['end']) & ~good].tolist()
        correction=np.interp(ds,ds[good],candidate[good])
        correction=gaussian_filter1d(median_filter(correction,size=5),1)
        measured_edge=e+correction
        result.setdefault('roadEdgeOcclusions',{})[side]=occluded_white
        # Editor spans are ONLY search windows. Actual inner/outer offsets and
        # color transitions come from source pixels; overlaps are unioned.
        eligible=np.zeros(len(ds),bool)
        for span in editor['curbs']:
            if span['side']==side:
                eligible|=(ds>=span['startDist']-8)&(ds<=span['endDist']+8)
        inner=np.full(len(ds),np.nan); outer=inner.copy()
        for i in np.flatnonzero(eligible):
            labs,n=label(red[i]); runs=[np.flatnonzero(labs==j) for j in range(1,n+1)]
            runs=[q for q in runs if 4<=len(q)<=32 and -.9<offset[q[0]]<3.5]
            if runs:
                q=min(runs,key=lambda q:abs(offset[q[0]]-correction[i]))
                inner[i]=e[i]+offset[q[0]]-.05
                outer[i]=e[i]+offset[q[-1]]+.05
        observed=np.flatnonzero(np.isfinite(inner))
        # A narrow red rim surrounding a WHITE block is not the full curb
        # width. Reject those partial cross-sections before fitting geometry.
        widths=outer[observed]-inner[observed]
        typical=median_filter(widths,size=21)
        partial=observed[widths<typical*.7]
        inner[partial]=np.nan;outer[partial]=np.nan
        observed=np.flatnonzero(np.isfinite(inner))
        groups=np.split(observed,np.flatnonzero(np.diff(observed)>18)+1)
        for ids in groups:
            if len(ids)<8 or ds[ids[-1]]-ds[ids[0]]<8: continue
            a,c=ids[0],ids[-1]
            # Follow actual broad white paint at each endpoint (a narrow road
            # line alone cannot satisfy the majority-of-curb-width test).
            for direction,anchor in [(-1,a),(1,c)]:
                for j in range(1,11):
                    i=anchor+direction*j
                    if not 0<=i<len(ds):break
                    q=(e[i]+offset>=inner[anchor]+.1)&(e[i]+offset<=outer[anchor]-.1)
                    if q.sum()<3 or np.mean(white[i,q])<.55:break
                    if direction<0:a=i
                    else:c=i
            ii=np.arange(a,c+1)
            iv=np.interp(ii,ids,inner[ids]); ov=np.interp(ii,ids,outer[ids])
            iv=gaussian_filter1d(iv,1); ov=gaussian_filter1d(ov,1)
            # White line's road-facing edge sits immediately inside the curb.
            measured_edge[ii]=iv-.08
            rows=[]
            for k,i in enumerate(ii):
                q=(offset+e[i]>=iv[k]+.2)&(offset+e[i]<=ov[k]-.15)
                is_red=bool(np.mean(red[i,q])>.3) if q.any() else False
                rows.append([round(float(ds[i]),2),round(float(x[i]+nx[i]*sign*iv[k]),3),round(float(z[i]+nz[i]*sign*iv[k]),3),
                    round(float(x[i]+nx[i]*sign*ov[k]),3),round(float(z[i]+nz[i]*sign*ov[k]),3),
                    round(float(sign*iv[k]),3),round(float(sign*ov[k]),3),int(is_red),int(np.isfinite(inner[i]))])
            # Interpolate gaps in XZ, NOT in lateral offset coordinates: an
            # editor tangent changing at a white block otherwise makes teeth.
            def world_fit(values):
                observed_xy=np.column_stack([x[ids]+nx[ids]*sign*values[ids],z[ids]+nz[ids]*sign*values[ids]])
                xy=np.column_stack([np.interp(ii,ids,observed_xy[:,j]) for j in range(2)])
                for end_index,neighbor in [(0,1),(-1,-2)]:
                    anchor=ids[end_index];other=ids[neighbor]
                    mask=ii<anchor if end_index==0 else ii>anchor
                    fit_ids=ids[:8] if end_index==0 else ids[-8:]
                    fit_xy=observed_xy[:8] if end_index==0 else observed_xy[-8:]
                    slope=np.array([np.polyfit(fit_ids,fit_xy[:,axis],1)[0] for axis in range(2)])
                    xy[mask]=observed_xy[end_index]+(ii[mask]-anchor)[:,None]*slope
                # Partial paint detections can jump onto a transverse marker.
                # Reject isolated geometric outliers before the bounded fit;
                # they are interpolated candidates, never survey observations.
                for _ in range(2):
                    window=min(21,len(xy) if len(xy)%2 else len(xy)-1)
                    fit=savgol_filter(xy,window,2,axis=0,mode='interp')
                    residual=np.linalg.norm(fit-xy,axis=1)
                    valid=residual<.4
                    valid[:2]=True;valid[-2:]=True
                    for k in np.flatnonzero(~valid):rows[k][8]=0
                    for axis in range(2):xy[:,axis]=np.interp(ii,ii[valid],xy[valid,axis])
                return smooth_plan(xy)
            # Fit the centre and positive measured width together. Fitting
            # two borders independently can collapse/invert a narrow curb
            # when each border rejects different partially occluded pixels.
            midpoint=world_fit((inner+outer)/2)
            # Differentiate a local curve fit, not adjacent noisy 0.5m points.
            # An unstable normal can fold the inner border back on itself.
            tangent=savgol_filter(midpoint,15,2,deriv=1,delta=.5,axis=0,mode='interp')
            normals=np.column_stack([-tangent[:,1],tangent[:,0]])*sign
            normals/=np.maximum(np.linalg.norm(normals,axis=1),.001)[:,None]
            width=gaussian_filter1d(np.interp(ii,ids,outer[ids]-inner[ids]),1)
            positions=midpoint-normals*width[:,None]/2
            outer_positions=midpoint+normals*width[:,None]/2
            for k,row in enumerate(rows):
                row[1:5]=[round(float(v),3) for v in [*positions[k],*outer_positions[k]]]
            result['curbs'].append(dict(side=side,rows=rows))
            curb_stats.append(dict(side=side,start=float(ds[a]),end=float(ds[c]),redObservedStations=len(ids),
                stations=len(ii),medianWidth=float(np.median(ov-iv)),minWidth=float(np.min(ov-iv)),maxWidth=float(np.max(ov-iv))))
        # Outside runoff paint (green) is independent of curb width.
        green=(g-r>4)&(g-bl>8)&(g>85)
        for i in range(0,len(ds)-1):
            if not (1800<=ds[i]<=2600): continue
            q=np.flatnonzero(green[i]&(offset+e[i]>measured_edge[i]+.3))
            if len(q)<4: continue
            lo=e[i]+offset[q[0]]; hi=e[i]+offset[q[-1]]
            result['green'].append([float(ds[i]),float(sign*lo),float(sign*hi)])
        all_edges[side]=measured_edge
        for start,end in [(1800,2150),(2150,2500)]:
            a=strip[(ds>=start)&(ds<end)].transpose(1,0,2)
            img=Image.fromarray(a).resize((1400,284))
            draw=ImageDraw.Draw(img)
            draw.line((0,80,1400,80),fill='cyan')
            for d in range(start,end,25): draw.text(((d-start)*4,3),str(d),fill='yellow')
            img.save(AUDIT/f'strip-{side}-{start}.png')
    for i,d in enumerate(ds):
        l=-all_edges['left'][i]; r=all_edges['right'][i]
        result['road'].append([round(float(v),3) for v in [d,x[i],z[i],nx[i],nz[i],l,r]])
    for side,col in [('left',5),('right',6)]:
        rows=np.array(result['road']); lateral=rows[:,col]
        xy=smooth_plan(np.column_stack([x+nx*lateral,z+nz*lateral]))
        for i,row in enumerate(result['road']): row.extend([round(float(v),3) for v in xy[i]])
    # The source centerline closes; an explicit last cross-section closes the
    # mesh without modulo interpolation shortening the final station.
    result['road'].append([track['totalLength'],*result['road'][0][1:]])
    refine_source_curbs(result, sample)
    connect_road_to_curbs(result)
    repair_folded_samples(result)
    for stats, curb in zip(curb_stats, result['curbs']):
        widths = [math.hypot(r[3]-r[1], r[4]-r[2]) for r in curb['rows']]
        stats.update(medianWidth=float(np.median(widths)), minWidth=min(widths), maxWidth=max(widths))
    result['statistics']=curb_stats
    assert result['road'][0][1:]==result['road'][-1][1:], 'Road does not close'
    assert all(np.isfinite(row).all() and row[6]>row[5] for row in result['road'])
    assert all(.25<math.hypot(row[3]-row[1],row[4]-row[2])<4
               for curb in result['curbs'] for row in curb['rows']), 'Collapsed or invalid curb width'
    # Coarse land-cover material atlas: no photo shadows, cars or painted
    # curbs are carried into the CG ground. This is a visual classification,
    # not a survey boundary layer. Road/curb geometry is separate.
    small=np.array(im.resize((1536,1536),Image.Resampling.BOX)).astype(float)
    r,g,bl=small[:,:,0],small[:,:,1],small[:,:,2]
    lum=small.mean(axis=2); chroma=np.ptp(small,axis=2)
    cover=np.zeros(lum.shape,dtype=np.uint8)
    cover[((r-bl)<10)&((g-bl)<9)]=1  # neutral paved/roof, including shadow
    cover[(chroma>24)&(lum>200)]=2 # very light dry soil / gravel
    cover[(lum<78)&(cover!=1)]=3  # canopy uncertainty, coarse only
    cover[(g-r>5)&(g-bl>8)]=4     # painted green runoff
    cover=median_filter(cover,size=3)
    palette=np.array([[142,150,94],[100,104,104],[174,161,125],[79,96,68],[66,132,103]],dtype=np.uint8)
    Image.fromarray(palette[cover]).save(OUT/'landcover.png')
    # Remove terrain fragments UNDER the road, avoiding z-fighting on hills.
    # Half-metre inset prevents raster quantisation cutting holes beside it.
    mask=Image.new('L',(4096,4096),255); md=ImageDraw.Draw(mask)
    def maskxy(row,s):
        t=(s-row[5])/(row[6]-row[5]); px,py=pixel(row[7]+(row[9]-row[7])*t,row[8]+(row[10]-row[8])*t)
        return float(px/W*4096),float(py/H*4096)
    for a,c in zip(result['road'][:-1],result['road'][1:]):
        md.polygon([maskxy(a,a[5]+.5),maskxy(a,a[6]-.5),maskxy(c,c[6]-.5),maskxy(c,c[5]+.5)],fill=0)
    mask.save(OUT/'road-cutout.png')
    (OUT/'landcover-meta.json').write_text(json.dumps(dict(bbox=b,mercator=True,
        source=meta['source'],sourceUrl=meta['sourceUrl'],license=meta['license'],
        processing='1536px box resize, five-class visual color classification, 3px median. Not measured land cover.'))+'\n')
    (OUT/'geometry.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    # Independent presentation: raw source versus traced inner/outer polylines.
    for name,box in [('hairpin',(150,105,355,340)),('entry',(280,175,530,345)),('exit',(145,285,215,440))]:
        x0,z0,x1,z1=box; px0,py0=pixel(x0,z0); px1,py1=pixel(x1,z1)
        crop=im.crop((int(px0),int(py0),int(px1),int(py1)))
        draw=ImageDraw.Draw(crop)
        def xy(x,z):
            px,py=pixel(x,z); return float(px-int(px0)),float(py-int(py0))
        for curb in result['curbs']:
            rows=curb['rows']
            for cols,color in [((1,2),'cyan'),((3,4),'magenta')]:
                draw.line([xy(row[cols[0]],row[cols[1]]) for row in rows],fill=color,width=2)
        crop.save(AUDIT/f'curb-audit-{name}.jpg',quality=94)
    (AUDIT/'extraction-report.json').write_text(json.dumps(dict(source=result['source'],statistics=curb_stats,limitations=result['limitations']),indent=2)+'\n')
    print(json.dumps(curb_stats,indent=2))
