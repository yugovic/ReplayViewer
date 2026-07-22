import { describe, expect, it, vi } from "vitest";
import { buildAnalysisStationGrid } from "./stationGrid.ts";

describe("buildAnalysisStationGrid", () => {
  it("uses the configured proposal step instead of the editor mesh spacing", () => {
    const sampleAt = vi.fn((dist: number) => ({
      x: dist,
      z: -dist,
      tx: 1,
      tz: 0,
      offsetLeft: 5 + dist / 1000,
      offsetRight: 6 + dist / 1000,
    }));

    const grid = buildAnalysisStationGrid(4559, true, 2, sampleAt);

    expect(grid.stations).toHaveLength(2280);
    expect(grid.currentLeft).toHaveLength(2280);
    expect(grid.currentRight).toHaveLength(2280);
    expect(grid.stations[1].dist).toBe(2);
    expect(grid.stations.at(-1)?.dist).toBe(4558);
    expect(sampleAt).toHaveBeenLastCalledWith(4558);
  });

  it("rejects invalid grid dimensions", () => {
    const sampleAt = () => ({
      x: 0,
      z: 0,
      tx: 1,
      tz: 0,
      offsetLeft: 5,
      offsetRight: 5,
    });

    expect(() => buildAnalysisStationGrid(0, true, 2, sampleAt)).toThrow(/track length/);
    expect(() => buildAnalysisStationGrid(100, true, 0, sampleAt)).toThrow(/step/);
  });
});
