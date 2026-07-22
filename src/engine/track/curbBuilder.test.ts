import { describe, expect, it } from "vitest";
import { buildCurbRibbonGeometry, type CurbCenterlinePoint } from "./curbBuilder";
import type { CurbSpan } from "./overlays";

/** Straight centerline along +X: tangent = +X, so normal = (0, +Z). The +Z
 * side is the driver's RIGHT (positive signed lateral, matching the road mesh
 * and projectPointToCenterline). */
function straightCenterline(): CurbCenterlinePoint[] {
  const pts: CurbCenterlinePoint[] = [];
  for (let i = 0; i <= 10; i++) {
    pts.push({ x: i * 10, z: 0, y: 0, dist: i * 10 });
  }
  return pts;
}

const HALF_ROAD = 6;

function span(side: "left" | "right"): CurbSpan {
  return { startDist: 20, endDist: 60, side, width: 1.2, stripeLength: 2.5 };
}

describe("buildCurbRibbonGeometry", () => {
  it("produces the expected station and vertex counts", () => {
    const step = 1.5;
    const geo = buildCurbRibbonGeometry(straightCenterline(), span("right"), {
      halfRoadWidth: HALF_ROAD,
      step,
    });
    const expectedStations = Math.ceil((60 - 20) / step) + 1;
    expect(geo.stations).toBe(expectedStations);
    expect(geo.positions.length).toBe(expectedStations * 2 * 3);
    expect(geo.uvs.length).toBe(expectedStations * 2 * 2);
    expect(geo.arcLength.length).toBe(expectedStations * 2);
    // Two triangles (6 indices) per station gap.
    expect(geo.indices.length).toBe((expectedStations - 1) * 6);
  });

  it("places a right-side curb on +Z and a left-side curb on -Z", () => {
    const opts = { halfRoadWidth: HALF_ROAD, overlap: 0.1, step: 1.5, yOffset: 0.03 };
    const right = buildCurbRibbonGeometry(straightCenterline(), span("right"), opts);
    const left = buildCurbRibbonGeometry(straightCenterline(), span("left"), opts);

    const innerOffset = HALF_ROAD - 0.1;
    const outerOffset = innerOffset + 1.2;
    for (let s = 0; s < right.stations; s++) {
      // inner vertex z, outer vertex z
      const innerZ = right.positions[s * 2 * 3 + 2];
      const outerZ = right.positions[(s * 2 + 1) * 3 + 2];
      expect(innerZ).toBeCloseTo(innerOffset, 5);
      expect(outerZ).toBeCloseTo(outerOffset, 5);
      // Left side mirrors to negative Z.
      expect(left.positions[s * 2 * 3 + 2]).toBeCloseTo(-innerOffset, 5);
      expect(left.positions[(s * 2 + 1) * 3 + 2]).toBeCloseTo(-outerOffset, 5);
    }
  });

  it("applies the vertical offset above the centerline elevation", () => {
    const geo = buildCurbRibbonGeometry(straightCenterline(), span("right"), {
      halfRoadWidth: HALF_ROAD,
      yOffset: 0.03,
    });
    for (let i = 0; i < geo.stations * 2; i++) {
      expect(geo.positions[i * 3 + 1]).toBeCloseTo(0.03, 5);
    }
  });

  it("clamps every station's arc-length to within the span", () => {
    const geo = buildCurbRibbonGeometry(straightCenterline(), span("right"), {
      halfRoadWidth: HALF_ROAD,
    });
    for (let i = 0; i < geo.arcLength.length; i++) {
      expect(geo.arcLength[i]).toBeGreaterThanOrEqual(20 - 1e-6);
      expect(geo.arcLength[i]).toBeLessThanOrEqual(60 + 1e-6);
    }
    // Endpoints land exactly on the span boundaries.
    expect(geo.arcLength[0]).toBeCloseTo(20, 5);
    expect(geo.arcLength[geo.arcLength.length - 1]).toBeCloseTo(60, 5);
  });

  it("baked arc-length is monotonic so the shader stripe phase advances cleanly", () => {
    const geo = buildCurbRibbonGeometry(straightCenterline(), span("right"), {
      halfRoadWidth: HALF_ROAD,
    });
    for (let s = 1; s < geo.stations; s++) {
      expect(geo.arcLength[s * 2]).toBeGreaterThan(geo.arcLength[(s - 1) * 2]);
    }
  });
});
