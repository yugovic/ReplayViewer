/**
 * Main width analysis entry point: takes a RasterSource and centerline stations,
 * produces a WidthProposal with per-station left/right offsets and confidence.
 */

import type {
  RasterSource,
  WidthAnalysisOptions,
  WidthProposal,
  WidthDiagnostics,
} from "./types.ts";
import { ANALYZER_VERSION, DEFAULT_WIDTH_OPTIONS } from "./types.ts";
import { computeStationEvidence } from "./widthEvidence.ts";
import { optimizeEdges } from "./optimizeEdges.ts";
import { computeConfidence } from "./confidence.ts";

export interface StationInput {
  x: number;
  z: number;
  tx: number;
  tz: number;
  dist: number;
}

export interface AnalyzeWidthParams {
  source: RasterSource;
  stations: StationInput[];
  closed: boolean;
  options?: Partial<WidthAnalysisOptions>;
  /** Current left/right offsets per station (for prior). */
  currentLeft: number[];
  currentRight: number[];
}

/**
 * Run the full width analysis pipeline:
 * 1. Compute per-station evidence (normal-section scoring)
 * 2. DP optimisation for left and right edges
 * 3. Confidence scoring
 * 4. Assemble WidthProposal
 */
export function analyzeWidth(params: AnalyzeWidthParams): WidthProposal {
  const options = { ...DEFAULT_WIDTH_OPTIONS, ...params.options };
  const { source, stations, closed, currentLeft, currentRight } = params;

  // Step 1: evidence
  const evidence = computeStationEvidence(source, stations, options, currentLeft, currentRight);

  // Step 2: DP optimisation
  const { left, right } = optimizeEdges(evidence, options, closed, currentLeft, currentRight);

  // Step 3: confidence
  const confidenceLeft = computeConfidence({
    evidence,
    offsets: left,
    resolutionMetersPerPixel: source.resolutionMetersPerPixel,
    side: "left",
  });
  const confidenceRight = computeConfidence({
    evidence,
    offsets: right,
    resolutionMetersPerPixel: source.resolutionMetersPerPixel,
    side: "right",
  });

  // Step 4: diagnostics
  const missingCount = evidence.filter((e) => !e.available).length;
  const diagnostics: WidthDiagnostics = {
    stationCount: stations.length,
    missingCount,
    resolutionMetersPerPixel: source.resolutionMetersPerPixel,
    analyzerVersion: ANALYZER_VERSION,
  };

  return {
    step: options.step,
    left,
    right,
    confidenceLeft,
    confidenceRight,
    diagnostics,
  };
}
