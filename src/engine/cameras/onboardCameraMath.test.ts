import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { CockpitCamera } from "./CockpitCamera";
import { OnboardCamera } from "./OnboardCamera";
import { applyOnboardCameraProjection, computeOnboardCameraFrame, FUJI_ONBOARD_CAMERA_PROFILE,
  projectOnboardPoint, validateOnboardCameraProfile } from "./onboardCameraMath";
import type { OnboardCameraProfile } from "./onboardCameraMath";
import type { CarCameraState } from "./types";

function state(quaternion = new THREE.Quaternion()): CarCameraState {
  return { position: new THREE.Vector3(3, 4, 5), quaternion,
    heading: 0, forward: new THREE.Vector3(0, 0, 1).applyQuaternion(quaternion),
    up: new THREE.Vector3(0, 1, 0).applyQuaternion(quaternion),
    trackCenter: new THREE.Vector3(), trackRadius: 300 };
}
function profile(overrides: Partial<OnboardCameraProfile> = {}): OnboardCameraProfile {
  return { ...FUJI_ONBOARD_CAMERA_PROFILE, f: 600, pitchDeg: 0, yawDeg: 0, rollDeg: 0, ...overrides };
}
function worldPoint(s: CarCameraState, p: OnboardCameraProfile, right: number, down: number, ahead: number) {
  return new THREE.Vector3(-p.rightMeters - right, p.upMeters - down, p.forwardMeters + ahead)
    .applyQuaternion(s.quaternion).add(s.position);
}

describe("source-video onboard camera", () => {
  it("projects driver-right to image-right and forward to the optical centre", () => {
    const s = state(); const p = profile();
    expect(projectOnboardPoint(worldPoint(s,p,0,0,20),s,p).x).toBeCloseTo(640,9);
    const pixel = projectOnboardPoint(worldPoint(s,p,2,1,20),s,p);
    expect(pixel.x).toBeCloseTo(700,9); expect(pixel.y).toBeCloseTo(390,9);
  });
  it("matches an independently evaluated camproj rotation for banked car poses", () => {
    const s = state(new THREE.Quaternion().setFromEuler(new THREE.Euler(.04,1.7,-.08,"YXZ")));
    const p = profile({ pitchDeg: 13, yawDeg: -3, rollDeg: 2, rightMeters:.25, upMeters:1.4, forwardMeters:-.9 });
    const a = THREE.MathUtils.degToRad(p.yawDeg), b=THREE.MathUtils.degToRad(p.pitchDeg), c=THREE.MathUtils.degToRad(p.rollDeg);
    for (const [x,y,z] of [[2,1,20],[-8,-2,50],[0,5,15]]) {
      const xx=Math.cos(a)*x-Math.sin(a)*z, zz=Math.sin(a)*x+Math.cos(a)*z;
      const yy=Math.cos(b)*y-Math.sin(b)*zz, zzz=Math.sin(b)*y+Math.cos(b)*zz;
      const xxx=Math.cos(c)*xx-Math.sin(c)*yy, yyy=Math.sin(c)*xx+Math.cos(c)*yy;
      const got=projectOnboardPoint(worldPoint(s,p,x,y,z),s,p);
      expect(got.x).toBeCloseTo(p.cx+p.f*xxx/zzz,8);
      expect(got.y).toBeCloseTo(p.cy+p.f*yyy/zzz,8);
    }
  });
  it("moves the mount rigidly with full pitch and roll and never mutates state", () => {
    const s=state(new THREE.Quaternion().setFromEuler(new THREE.Euler(.2,.4,.1)));
    const pos=s.position.toArray(), quat=s.quaternion.toArray(); const p=profile({rightMeters:.3});
    const frame=computeOnboardCameraFrame(s,p);
    const expected=new THREE.Vector3(-.3,1.25,-.55).applyQuaternion(s.quaternion).add(s.position);
    expect(frame.position.distanceTo(expected)).toBeLessThan(1e-12);
    expect(s.position.toArray()).toEqual(pos); expect(s.quaternion.toArray()).toEqual(quat);
  });
  it("Three projection matches source pixels including an off-centre principal point", () => {
    const s=state(), p=profile({cx:653,cy:344,pitchDeg:12,yawDeg:1,rollDeg:-2});
    const camera=new THREE.PerspectiveCamera(58,1280/720,.1,5000), frame=computeOnboardCameraFrame(s,p);
    camera.position.copy(frame.position); camera.quaternion.copy(frame.quaternion);
    applyOnboardCameraProjection(camera,p); camera.updateMatrixWorld(true);
    const pt=worldPoint(s,p,3,1,30), v=pt.clone().project(camera), expected=projectOnboardPoint(pt,s,p);
    expect((v.x+1)*640).toBeCloseTo(expected.x,8); expect((1-v.y)*360).toBeCloseTo(expected.y,8);
  });
  it("keeps focal scale per image height at a different viewport aspect", () => {
    const p=profile(), camera=new THREE.PerspectiveCamera(58,1,.1,5000);
    applyOnboardCameraProjection(camera,p);
    expect(camera.projectionMatrix.elements[0]).toBeCloseTo(2*p.f/720,9);
    expect(camera.projectionMatrix.elements[5]).toBeCloseTo(2*p.f/720,9);
  });
  it("leaves standard cockpit position, orientation and projection unchanged after switching", () => {
    const s=state(), reference=new THREE.PerspectiveCamera(60,16/9,.1,5000), changed=reference.clone();
    new CockpitCamera(reference).activate(s);
    const onboard=new OnboardCamera(changed,()=>profile({cx:670,cy:330,rollDeg:4}));
    onboard.activate(s); onboard.deactivate(); new CockpitCamera(changed).activate(s);
    expect(changed.position.toArray()).toEqual(reference.position.toArray());
    expect(changed.quaternion.angleTo(reference.quaternion)).toBeLessThan(1e-7);
    expect(changed.projectionMatrix.elements).toEqual(reference.projectionMatrix.elements);
  });
  it("uses ordinary cockpit when no source profile applies", () => {
    const s=state(), reference=new THREE.PerspectiveCamera(), camera=reference.clone();
    new CockpitCamera(reference).activate(s); new OnboardCamera(camera,()=>null).activate(s);
    expect(camera.position.toArray()).toEqual(reference.position.toArray());
    expect(camera.projectionMatrix.elements).toEqual(reference.projectionMatrix.elements);
  });
  it("rejects non-finite focal length and invalid dimensions", () => {
    expect(()=>validateOnboardCameraProfile(profile({f:NaN}))).toThrow();
    expect(()=>validateOnboardCameraProfile(profile({referenceHeight:0}))).toThrow();
    expect(()=>validateOnboardCameraProfile(profile())).not.toThrow();
  });
});