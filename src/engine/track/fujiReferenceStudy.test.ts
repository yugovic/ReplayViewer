import { describe, expect, it } from "vitest";
import { buildFujiReferenceRoad, excludedFujiTreeZone, pointInPolygon } from "./FujiReferenceStudy";
import { readFileSync } from "node:fs";
import type { Mesh } from "three";
import type { TrackData } from "../../replay/types";
import { localToLatLng } from "../../replay/projection";
import { satelliteUvToLatLng } from "./TrackBuilder";

describe("evidence-reviewed tree exclusions",()=>{
  it("removes an equipment/island point while retaining a point in the wooded slope",()=>{
    const t={height:10,crownRadius:3};
    expect(excludedFujiTreeZone({...t,x:210,z:200})).toBe("hairpin-island-and-marshal-equipment");
    expect(excludedFujiTreeZone({...t,x:375,z:160})).toBeNull();
    expect(excludedFujiTreeZone({...t,x:-500,z:700})).toBeNull();
  });
  it("handles a concave exclusion polygon without removing its outside notch",()=>{
    const p=[[0,0],[4,0],[4,1],[1,1],[1,4],[0,4]] as const;
    expect(pointInPolygon(.5,3,p)).toBe(true);expect(pointInPolygon(3,3,p)).toBe(false);
  });
});

it("keeps reference road faces upward and photo UVs aligned to geographic vertices",()=>{
  const track=JSON.parse(readFileSync("public/data/tracks/fuji/track.json","utf8")) as TrackData;
  const edges=JSON.parse(readFileSync("public/data/tracks/fuji/road_edges.json","utf8"));
  const meta=JSON.parse(readFileSync("public/data/tracks/fuji/satellite_meta.json","utf8"));
  const group=buildFujiReferenceRoad(track,edges);
  const road=group.children[0] as Mesh;
  const p=road.geometry.attributes.position,uv=road.geometry.attributes.uv,n=road.geometry.attributes.normal;
  for(let i=0;i<p.count;i+=37){
    expect(n.getY(i)).toBeGreaterThan(.5);
    const expected=localToLatLng(p.getX(i),p.getZ(i),track.origin);
    const actual=satelliteUvToLatLng(uv.getX(i),uv.getY(i),meta.bbox,true);
    expect(actual.lat).toBeCloseTo(expected.lat,7);
    expect(actual.lng).toBeCloseTo(expected.lng,7);
  }
  group.userData.dispose();
});
