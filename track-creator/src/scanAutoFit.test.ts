import { describe, expect, it } from "vitest";
import {
  confidenceForFit,
  lateralResponse,
  nearestValueInGapRange,
  normalizeGapRange,
  residualToGapRange,
  solveLeastSquares2D,
  solveTranslationFit,
  type TranslationConstraint,
} from "../preview/scanAutoFit.ts";

describe("scan translation auto-fit", () => {
  it("recovers a shared X/Z translation from three differently oriented points", () => {
    const expected = { x: -3.5, z: -4.4 };
    const definitions = [
      { id: "A", heading: 2.26, side: "left" as const, currentGapM: 2.0 },
      { id: "B", heading: 1.54, side: "right" as const, currentGapM: 1.0 },
      { id: "C", heading: -0.7, side: "right" as const, currentGapM: 4.0 },
    ];
    const constraints: TranslationConstraint[] = definitions.map((definition) => {
      const response = lateralResponse(definition.heading, definition.side);
      return {
        ...definition,
        targetGapM: definition.currentGapM + response.x * expected.x + response.z * expected.z,
      };
    });

    const result = solveTranslationFit(constraints);
    expect(result).not.toBeNull();
    expect(result!.x).toBeCloseTo(expected.x, 5);
    expect(result!.z).toBeCloseTo(expected.z, 5);
    expect(result!.rmseM).toBeLessThan(1e-5);
    expect(result!.confidence).toBe("HIGH");
  });

  it("returns no solution when every point observes the same lateral direction", () => {
    const rows = [0, 1, 2].map(() => ({ ax: 1, az: 0, b: 2 }));
    expect(solveLeastSquares2D(rows)).toBeNull();
  });

  it("uses opposite gap response signs for left and right edges", () => {
    const left = lateralResponse(0.7, "left");
    const right = lateralResponse(0.7, "right");
    expect(left.x).toBeCloseTo(-right.x);
    expect(left.z).toBeCloseTo(-right.z);
  });

  it("reduces confidence for high residuals or poor geometry", () => {
    expect(confidenceForFit(2, 0.1)).toBe("HIGH");
    expect(confidenceForFit(5, 0.5)).toBe("MEDIUM");
    expect(confidenceForFit(12, 0.1)).toBe("LOW");
    expect(confidenceForFit(2, 1)).toBe("LOW");
  });
  it("accepts values inside an interval target", () => {
    expect(residualToGapRange(0.1, -0.05, 0.15)).toBe(0);
  });
});
