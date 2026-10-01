import { describe, expect, it } from "vitest";
import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, Object3D, Vector3 } from "three";
import { readFileSync } from "node:fs";
import { applyVehicleRigGeometry, type DimensionedRig } from "./vehicleRigGeometry";
import { parseVehicleGeometry, tyreOuterHalfSpan } from "../replay/vehicleGeometry";

const profile = JSON.parse(readFileSync("public/data/vehicles/mazda2-dj.json", "utf8"));
const geometry = parseVehicleGeometry(profile)!;

function rig(): DimensionedRig {
  const root = new Group(), body = new Group();
  body.add(new Mesh(new BoxGeometry(2, 0.9, 4), new MeshBasicMaterial()));
  root.add(body);
  const wheel = () => {
    const pivot = new Group();
    const mesh = new Object3D();
    pivot.add(mesh); root.add(pivot);
    return { pivot, wheel: mesh, tyreWidthMeters: 0.25 };
  };
  return { root, body, wheels: { frontLeft: wheel(), frontRight: wheel(), rearLeft: wheel(), rearRight: wheel() } };
}

describe("independent vehicle dimensions", () => {
  it("rejects incomplete/non-finite profiles instead of corrupting a rig", () => {
    for (const bad of [null, {}, { ...profile, frontTrackMeters: NaN }, { ...profile, rearTyreWidthMeters: 0 },
      { ...profile, wheelbaseMeters: "2.57" }, { ...profile, bodyWidthMeters: 20 }]) {
      expect(parseVehicleGeometry(bad)).toBeNull();
    }
    expect(geometry).toMatchObject({ bodyWidthMeters: 1.695, wheelbaseMeters: 2.57 });
    expect(tyreOuterHalfSpan(geometry, "front")).toBeCloseTo(0.84);
    expect(tyreOuterHalfSpan(geometry, "rear")).toBeCloseTo(0.8325);
  });

  it("uses the specified body width, unequal axle tracks, tyre widths and wheelbase in metres", () => {
    const r = rig();
    applyVehicleRigGeometry(r, geometry, 2);
    r.root.updateMatrixWorld(true);
    expect(new Box3().setFromObject(r.body).getSize(new Vector3()).x).toBeCloseTo(1.695);
    expect(r.root.scale.x).toBe(1);
    expect(r.wheels.frontRight.pivot.position.x - r.wheels.frontLeft.pivot.position.x).toBeCloseTo(1.495);
    expect(r.wheels.rearRight.pivot.position.x - r.wheels.rearLeft.pivot.position.x).toBeCloseTo(1.480);
    expect(r.wheels.frontRight.pivot.position.z - r.wheels.rearRight.pivot.position.z).toBeCloseTo(2.570);
    const points = Object.entries(r.wheels).map(([name, w]) => {
      expect(w.wheel.scale.x * 0.25).toBeCloseTo(0.185);
      const x = (name.endsWith("Left") ? -1 : 1) * w.tyreWidthMeters / 2;
      return { name, p: w.pivot.localToWorld(new Vector3(x, -0.33, 0)) };
    });
    for (const { name, p } of points) {
      expect(Math.abs(p.x)).toBeCloseTo(name.startsWith("front") ? 0.840 : 0.8325);
      expect(p.y).toBeCloseTo(0);
      expect(Math.abs(p.z)).toBeCloseTo(1.285);
    }
  });

  it("contact-edge measurements rotate with steering rather than scaling the tyre with the body", () => {
    const r = rig();
    applyVehicleRigGeometry(r, geometry, 2);
    const w = r.wheels.frontRight;
    w.pivot.rotation.y = Math.PI / 6;
    r.root.updateMatrixWorld(true);
    const p = w.pivot.localToWorld(new Vector3(w.tyreWidthMeters / 2, -0.33, 0));
    expect(p.x).toBeCloseTo(1.495 / 2 + 0.185 / 2 * Math.cos(Math.PI / 6));
    expect(p.z).toBeCloseTo(2.570 / 2 - 0.185 / 2 * Math.sin(Math.PI / 6));
  });

  it("resets every dimension when returning to another vehicle or a historical alignment study", () => {
    const r = rig();
    applyVehicleRigGeometry(r, geometry, 2);
    applyVehicleRigGeometry(r, null, 2);
    expect(r.body.scale.x).toBe(1);
    expect(r.root.scale.x).toBe(1);
    expect(r.wheels.frontRight.pivot.position.x).toBe(0.8);
    expect(r.wheels.rearRight.pivot.position.x).toBe(0.8);
    expect(r.wheels.frontRight.pivot.position.z).toBe(1.3);
    expect(r.wheels.frontRight.wheel.scale.x).toBe(1);
    expect(r.wheels.frontRight.tyreWidthMeters).toBe(0.25);
    applyVehicleRigGeometry(r, null, 2, 1.7);
    expect(r.root.scale.x).toBe(0.85);
    expect(r.body.scale.x).toBe(1);
  });
});
