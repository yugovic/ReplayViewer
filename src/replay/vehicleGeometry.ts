/** Dimensions are separate: body width is not the tyre outside-to-outside span. */
export interface VehicleGeometry {
  id: string;
  bodyWidthMeters: number;
  wheelbaseMeters: number;
  frontTrackMeters: number;
  rearTrackMeters: number;
  frontTyreWidthMeters: number;
  rearTyreWidthMeters: number;
}

export function parseVehicleGeometry(value: unknown): VehicleGeometry | null {
  if (!value || typeof value !== "object") return null;
  const g = value as VehicleGeometry;
  const inRange = (n: unknown, min: number, max: number) =>
    typeof n === "number" && Number.isFinite(n) && n >= min && n <= max;
  if (typeof g.id !== "string" || !g.id || !inRange(g.bodyWidthMeters, 1, 3) ||
    !inRange(g.wheelbaseMeters, 1, 5) || !inRange(g.frontTrackMeters, 0.8, 3) ||
    !inRange(g.rearTrackMeters, 0.8, 3) || !inRange(g.frontTyreWidthMeters, 0.1, 0.6) ||
    !inRange(g.rearTyreWidthMeters, 0.1, 0.6)) return null;
  return {
    id: g.id, bodyWidthMeters: g.bodyWidthMeters, wheelbaseMeters: g.wheelbaseMeters,
    frontTrackMeters: g.frontTrackMeters, rearTrackMeters: g.rearTrackMeters,
    frontTyreWidthMeters: g.frontTyreWidthMeters, rearTyreWidthMeters: g.rearTyreWidthMeters,
  };
}

/** Nominal section width, not a measured loaded contact-patch width. */
export function tyreOuterHalfSpan(g: VehicleGeometry, axle: "front" | "rear"): number {
  return axle === "front" ? (g.frontTrackMeters + g.frontTyreWidthMeters) / 2
    : (g.rearTrackMeters + g.rearTyreWidthMeters) / 2;
}
