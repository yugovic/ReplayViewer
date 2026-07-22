import type { StationInput } from "./analyzeWidth.ts";

export interface AnalysisTrackSample {
  x: number;
  z: number;
  tx: number;
  tz: number;
  offsetLeft: number;
  offsetRight: number;
}

export interface AnalysisStationGrid {
  stations: StationInput[];
  currentLeft: number[];
  currentRight: number[];
}

/**
 * Sample the editor track on the exact uniform grid described by the analysis
 * options. WidthProposal.step and every proposal array index use this same
 * grid, so applying a proposal cannot shift observations to different arc
 * lengths when the editor road mesh uses another station spacing.
 */
export function buildAnalysisStationGrid(
  totalLength: number,
  closed: boolean,
  step: number,
  sampleAt: (dist: number) => AnalysisTrackSample,
): AnalysisStationGrid {
  if (!Number.isFinite(totalLength) || totalLength <= 0) {
    throw new Error("analysis station grid requires a positive track length");
  }
  if (!Number.isFinite(step) || step <= 0) {
    throw new Error("analysis station grid requires a positive step");
  }

  const count = closed
    ? Math.ceil(totalLength / step)
    : Math.floor(totalLength / step) + 1;
  const stations: StationInput[] = new Array(count);
  const currentLeft: number[] = new Array(count);
  const currentRight: number[] = new Array(count);

  for (let i = 0; i < count; i++) {
    const dist = Math.min(i * step, totalLength);
    const sample = sampleAt(dist);
    stations[i] = {
      x: sample.x,
      z: sample.z,
      tx: sample.tx,
      tz: sample.tz,
      dist,
    };
    currentLeft[i] = sample.offsetLeft;
    currentRight[i] = sample.offsetRight;
  }

  return { stations, currentLeft, currentRight };
}
