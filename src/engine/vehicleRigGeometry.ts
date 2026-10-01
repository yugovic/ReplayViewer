import type { Group, Object3D } from "three";
import type { VehicleGeometry } from "../replay/vehicleGeometry";

export interface DimensionedWheel {
  pivot: Group;
  wheel: Object3D;
  /** Nominal section width in the pivot frame (before any legacy root scale). */
  tyreWidthMeters: number;
}

export interface DimensionedRig {
  root: Group;
  body: Group;
  wheels: Record<"frontLeft" | "frontRight" | "rearLeft" | "rearRight", DimensionedWheel>;
}

export const GENERIC_WHEEL_DIMENSIONS = {
  radius: 0.33, width: 0.25, wheelbase: 2.6, track: 1.6,
};

/** Keep fitted wheels in metres independently of the visual body mesh. With no
 * explicit geometry, restore the old rig (including its study-wide X scale). */
export function applyVehicleRigGeometry(rig: DimensionedRig, geometry: VehicleGeometry | null,
  normalizedBodyWidth: number, legacyWidth: number | null = null): void {
  const defaults = GENERIC_WHEEL_DIMENSIONS;
  rig.root.scale.set(geometry ? 1 : legacyWidth ? legacyWidth / normalizedBodyWidth : 1, 1, 1);
  rig.body.scale.set(geometry ? geometry.bodyWidthMeters / normalizedBodyWidth : 1, 1, 1);
  for (const name of ["frontLeft", "frontRight", "rearLeft", "rearRight"] as const) {
    const front = name.startsWith("front");
    const side = name.endsWith("Left") ? -1 : 1;
    const track = geometry ? (front ? geometry.frontTrackMeters : geometry.rearTrackMeters) : defaults.track;
    const width = geometry ? (front ? geometry.frontTyreWidthMeters : geometry.rearTyreWidthMeters) : defaults.width;
    const wheelbase = geometry?.wheelbaseMeters ?? defaults.wheelbase;
    const w = rig.wheels[name];
    w.pivot.position.set(side * track / 2, defaults.radius, (front ? 1 : -1) * wheelbase / 2);
    w.wheel.scale.set(width / defaults.width, 1, 1);
    w.tyreWidthMeters = width;
  }
}
