import { describe, expect, it } from "vitest";
import {
  estimatePerspectiveScreenPixels,
  isEnhancedCorridorManifest,
  manifestTileToBbox,
  type EnhancedCorridorTile,
} from "./EnhancedCorridorGround";

const BBOX = { minLng: 138, maxLng: 140, minLat: 34, maxLat: 36 };

describe("estimatePerspectiveScreenPixels", () => {
  it("estimates apparent tile size from distance and vertical field of view", () => {
    expect(estimatePerspectiveScreenPixels(20, 100, 720, 60)).toBeCloseTo(124.708, 2);
  });

  it("shrinks linearly with distance", () => {
    const near = estimatePerspectiveScreenPixels(20, 100, 720, 60);
    const far = estimatePerspectiveScreenPixels(20, 200, 720, 60);
    expect(far).toBeCloseTo(near / 2, 10);
  });

  it("returns zero for invalid viewport or tile sizes", () => {
    expect(estimatePerspectiveScreenPixels(0, 100, 720, 60)).toBe(0);
    expect(estimatePerspectiveScreenPixels(20, 100, 0, 60)).toBe(0);
  });
});

describe("manifestTileToBbox", () => {
  it("maps a full-image tile back to the manifest bbox", () => {
    const tile: EnhancedCorridorTile = {
      file: "tiles/0_0.webp",
      outputPixel: { width: 1024, height: 1024 },
      uv: { u0: 0, v0: 0, u1: 1, v1: 1 },
    };
    const result = manifestTileToBbox(tile, BBOX, true);
    expect(result.minLng).toBeCloseTo(BBOX.minLng, 10);
    expect(result.maxLng).toBeCloseTo(BBOX.maxLng, 10);
    expect(result.minLat).toBeCloseTo(BBOX.minLat, 10);
    expect(result.maxLat).toBeCloseTo(BBOX.maxLat, 10);
  });

  it("keeps northern and southern halves adjacent in Mercator space", () => {
    const north = manifestTileToBbox(
      { file: "n.webp", outputPixel: { width: 1, height: 1 }, uv: { u0: 0, v0: 0, u1: 1, v1: 0.5 } },
      BBOX,
      true,
    );
    const south = manifestTileToBbox(
      { file: "s.webp", outputPixel: { width: 1, height: 1 }, uv: { u0: 0, v0: 0.5, u1: 1, v1: 1 } },
      BBOX,
      true,
    );
    expect(north.minLat).toBeCloseTo(south.maxLat, 10);
  });
});

describe("isEnhancedCorridorManifest", () => {
  it("accepts the visualization-only v1 manifest shape", () => {
    expect(
      isEnhancedCorridorManifest({
        schemaVersion: 1,
        kind: "track-corridor-enhancement",
        visualizationOnly: true,
        bbox: BBOX,
        mercator: true,
        tileCount: 1,
        tiles: [
          {
            file: "tiles/0_0.webp",
            outputPixel: { width: 1024, height: 1024 },
            uv: { u0: 0, v0: 0, u1: 0.1, v1: 0.1 },
          },
        ],
      }),
    ).toBe(true);
  });

  it("rejects a manifest that is not explicitly visualization-only", () => {
    expect(
      isEnhancedCorridorManifest({
        schemaVersion: 1,
        kind: "track-corridor-enhancement",
        visualizationOnly: false,
        bbox: BBOX,
        tileCount: 0,
        tiles: [],
      }),
    ).toBe(false);
  });
});
