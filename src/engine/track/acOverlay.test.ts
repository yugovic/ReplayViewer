import { describe, expect, it } from "vitest";
import { buildAcOverlay } from "./AcOverlay";

/** One flat quad spanning x∈[0,10], z∈[-1,1] at the BAKED (lifted) Y. */
function flatQuad(bakedY: number, lift?: number) {
  return {
    trackId: "test",
    left: [
      [0, bakedY, 1],
      [10, bakedY, 1],
    ] as [number, number, number][],
    right: [
      [0, bakedY, -1],
      [10, bakedY, -1],
    ] as [number, number, number][],
    center: [
      [0, bakedY, 0],
      [10, bakedY, 0],
    ] as [number, number, number][],
    ...(lift !== undefined ? { lift } : {}),
  };
}

describe("AcOverlay heightAt", () => {
  it("returns the true surface (baked lift removed) under a point on the ribbon", () => {
    // Baked at 10.35; default lift 0.35 → true surface at 10.0.
    const h = buildAcOverlay(flatQuad(10.35));
    const y = h.heightAt(5, 0);
    expect(y).not.toBeNull();
    expect(y!).toBeCloseTo(10.0, 3);
    h.dispose();
  });

  it("returns null off the ribbon", () => {
    const h = buildAcOverlay(flatQuad(10.35));
    expect(h.heightAt(100, 100)).toBeNull();
    h.dispose();
  });

  it("honors an explicit lift field", () => {
    // lift:0 → true surface equals the baked Y.
    const h = buildAcOverlay(flatQuad(12.0, 0));
    expect(h.heightAt(5, 0)!).toBeCloseTo(12.0, 3);
    h.dispose();
  });
});
