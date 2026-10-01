import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {visualPositionOffset, registeredVehicleWidth, type LocalReplayAlignment} from './visualAlignment';
import {sampleReplay,getProjectedLapArcLengths} from './interpolation';
import type {TrackData,LapData} from './types';
const raw=JSON.parse(readFileSync('public/data/tracks/fuji/track.json','utf8')) as TrackData;
const lap=JSON.parse(readFileSync('public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_003.json','utf8')) as LapData;
const track={...raw,visualProfile:'cg' as const,replayAlignment:JSON.parse(readFileSync('public/data/tracks/fuji/cg_study/replay-alignment.json','utf8'))};
describe('display-only onboard registration',()=>{
 it('preserves raw arrays and telemetry, applies a rigid translation throughout the reviewed interval',()=>{
  const before=JSON.stringify(lap);
  for(const t of [47,52.387,55,60,64.367,68,72]){const a=sampleReplay(lap,raw,t),b=sampleReplay(lap,track,t);expect(b.x-a.x).toBeCloseTo(-3.309,6);expect(b.z-a.z).toBeCloseTo(1.531,6);expect(b.heading).toBeCloseTo(a.heading,5);expect(b.telemetry).toEqual(a.telemetry);}
  expect(JSON.stringify(lap)).toBe(before);
 });
 it('does not leak cached projected distances between raw and registered tracks',()=>{
  const a=getProjectedLapArcLengths(lap,raw),b=getProjectedLapArcLengths(lap,track);expect(a).not.toBe(b);expect(Math.abs(a[550]-b[550])).toBeGreaterThan(.1);expect(getProjectedLapArcLengths(lap,raw)).toBe(a);
 });
 it('is identity outside the interval and for other laps/profiles, with smooth fades',()=>{
  for(const t of [0,40,79,100])expect(visualPositionOffset(lap,track,t)).toEqual({x:0,z:0});
  expect(visualPositionOffset({...lap,meta:{...lap.meta,lap:2}},track,55)).toEqual({x:0,z:0});
  expect(visualPositionOffset(lap,{...track,visualProfile:'reference'},55)).toEqual({x:0,z:0});
  expect(Math.abs(visualPositionOffset(lap,track,40.001).x)).toBeLessThan(1e-5);
 });
});

const localProfile = JSON.parse(readFileSync('public/data/tracks/fuji/cg_study/local-replay-alignment.json','utf8')) as LocalReplayAlignment;
const localTrack: TrackData = {...raw, visualProfile: 'cg', replayAlignment: localProfile};
const sameWidthRaw: TrackData = {...localTrack, replayAlignment: {...localProfile, enabled: false}};

describe('local visual registration', () => {
 it('keeps agreed raw locations and telemetry while correcting both reviewed cores', () => {
  const before = JSON.stringify(lap);
  for (const t of [0,18,21,22,23,75,76,77,96,97,98,121,122,123,141]) {
   expect(sampleReplay(lap,localTrack,t)).toEqual(sampleReplay(lap,sameWidthRaw,t));
  }
  for (const t of [53,54,55,56,57,83,84,85,86]) {
   const a = sampleReplay(lap,sameWidthRaw,t), b = sampleReplay(lap,localTrack,t);
   expect(b.x-a.x).toBeCloseTo(localProfile.segments[0].offsetMeters[0],8);
   expect(b.z-a.z).toBeCloseTo(localProfile.segments[0].offsetMeters[1],8);
   expect(b.heading).toBeCloseTo(a.heading,8);
   expect(b.telemetry).toEqual(a.telemetry);
  }
  expect(JSON.stringify(lap)).toBe(before);
 });
 it('has smooth bounded transitions and no accumulating offset after seeking', () => {
  for (const seg of localProfile.segments) for (const t of seg.windowSeconds) {
   const h=.001, a=visualPositionOffset(lap,localTrack,t-h),b=visualPositionOffset(lap,localTrack,t),c=visualPositionOffset(lap,localTrack,t+h);
   expect(Math.hypot(c.x-a.x,c.z-a.z)/(2*h)).toBeLessThan(.00001);
   expect(Math.hypot(c.x-2*b.x+a.x,c.z-2*b.z+a.z)/(h*h)).toBeLessThan(.003);
  }
  for (let t=0;t<141.3;t+=.1) {
   const offset=visualPositionOffset(lap,localTrack,t);
   expect(Math.hypot(offset.x,offset.z)).toBeLessThanOrEqual(2.117);
   const a=sampleReplay(lap,sameWidthRaw,t), b=sampleReplay(lap,localTrack,t);
   expect(b.telemetry).toEqual(a.telemetry);
   expect([b.x,b.y,b.z,b.heading,b.pitch,b.roll].every(Number.isFinite)).toBe(true);
  }
  const first=sampleReplay(lap,localTrack,55);
  sampleReplay(lap,localTrack,120); sampleReplay(lap,sameWidthRaw,3);
  expect(sampleReplay(lap,localTrack,55)).toEqual(first);
 });
 it('isolates width and corrections to the exact CG lap; raw mode has the same width', () => {
  expect(registeredVehicleWidth(lap,localTrack)).toBe(1.7);
  expect(registeredVehicleWidth(lap,sameWidthRaw)).toBe(1.7);
  expect(visualPositionOffset(lap,sameWidthRaw,55)).toEqual({x:0,z:0});
  for (const changed of [{lap:2},{race_id:'fuji_aim_2020_07_30'},{vehicle_id:'other'}]) {
   const other={...lap,meta:{...lap.meta,...changed}};
   expect(visualPositionOffset(other,localTrack,55)).toEqual({x:0,z:0});
   expect(registeredVehicleWidth(other,localTrack)).toBeNull();
  }
  for (const changed of [{trackId:'suzuka'},{visualProfile:'reference' as const}]) {
   expect(visualPositionOffset(lap,{...localTrack,...changed},55)).toEqual({x:0,z:0});
   expect(registeredVehicleWidth(lap,{...localTrack,...changed})).toBeNull();
  }
 });
 it('does not add overlapping windows or propagate invalid parameters', () => {
  for (const segments of [
   [localProfile.segments[0], localProfile.segments[0]],
   [{...localProfile.segments[0],offsetMeters:[NaN,0] as [number,number]}],
   [{...localProfile.segments[0],windowSeconds:[40,59,51,70] as [number,number,number,number]}],
  ]) expect(visualPositionOffset(lap,{...localTrack,replayAlignment:{...localProfile,segments}},55)).toEqual({x:0,z:0});
 });
});
