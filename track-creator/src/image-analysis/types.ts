/**
 * Input/output types for the image-assisted track generation module.
 * Pure data — no THREE, no IO — so everything is unit-testable.
 */

/** sRGB byte triplet (0..255). */
export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/** CIE Lab color (D65 illuminant). */
export interface Lab {
  L: number;
  a: number;
  b: number;
}

/** Metadata for a georeferenced raster image (satellite_meta.json format). */
export interface GeoImageMeta {
  imageFile: string;
  imageWidth: number;
  imageHeight: number;
  bbox: {
    minLng: number;
    maxLng: number;
    minLat: number;
    maxLat: number;
  };
  mercator: boolean;
}

/**
 * Abstract raster source: algorithms sample pixels by local ENU coordinates
 * without knowing where the image came from.
 */
export interface RasterSource {
  id: string;
  /** Metres per pixel (effective resolution, not pixel count). */
  resolutionMetersPerPixel: number;
  /** sRGB byte triplet at a local (x, z) position, or null if outside the image. */
  sampleLocal(x: number, z: number): Rgb | null;
  /** Whether the local coordinate falls within the image bounds. */
  containsLocal(x: number, z: number): boolean;
}

// ── width analysis ────────────────────────────────────────────────────────────

/**
 * Dist-range constraint that caps the lateral search on one side. Used where
 * contiguous paving (pit lane, paved run-off) would otherwise pull the edge
 * far beyond the racing surface. On closed tracks a corridor may wrap:
 * startDist > endDist means [startDist..total] ∪ [0..endDist].
 */
export interface CorridorConstraint {
  startDist: number;
  endDist: number;
  side: "left" | "right";
  /** Maximum lateral offset to allow inside the range, metres. */
  maxOffset: number;
}

export interface WidthAnalysisOptions {
  /** Sampling interval along the centerline, metres. */
  step: number;
  /** Minimum lateral offset to search, metres. */
  minOffset: number;
  /** Maximum lateral offset to search, metres. */
  maxOffset: number;
  /** Candidate spacing along the normal, metres. */
  candidateStep: number;
  /** Smoothness penalty weight (λ1 in the DP objective). */
  smoothness: number;
  /** Excessive slope penalty weight (λ2). */
  slopePenalty: number;
  /** Prior distance penalty weight (λ3). */
  priorStrength: number;
  /** Confidence threshold for "high confidence" (0..1). */
  confidenceThreshold: number;
  /** Per-range lateral search caps (pit lane etc.). */
  corridors?: CorridorConstraint[];
  /**
   * What counts as the edge.
   * "boundary"    — the asphalt/terrain material boundary (default).
   * "stripeFirst" — the innermost painted edge line where one is visible,
   *                 falling back to the boundary where none is.
   */
  objective?: "boundary" | "stripeFirst";
}

export interface WidthDiagnostics {
  /** Number of stations analysed. */
  stationCount: number;
  /** Number of stations where the image was unavailable. */
  missingCount: number;
  /** Effective resolution used. */
  resolutionMetersPerPixel: number;
  /** Analyzer version string. */
  analyzerVersion: string;
}

export interface WidthProposal {
  step: number;
  left: number[];
  right: number[];
  confidenceLeft: number[];
  confidenceRight: number[];
  diagnostics: WidthDiagnostics;
}

// ── curb proposals ────────────────────────────────────────────────────────────

export interface CurbProposal {
  startDist: number;
  endDist: number;
  side: "left" | "right";
  width: number;
  stripeLength: number;
  /** Overall interval confidence (0..1). */
  confidence: number;
  /** Boundary confidence at start and end (0..1 each). */
  confidenceStart: number;
  confidenceEnd: number;
}

// ── analysis session ──────────────────────────────────────────────────────────

export interface AnalysisSession {
  version: 1;
  trackName: string;
  sourceId: string;
  sourceSha256?: string;
  effectiveResolution: number;
  centerlineFingerprint: string;
  options: WidthAnalysisOptions;
  widthProposal?: WidthProposal;
  curbProposals?: CurbProposal[];
  createdAt: string;
  analyzerVersion: string;
}

// ── defaults ──────────────────────────────────────────────────────────────────

export const DEFAULT_WIDTH_OPTIONS: WidthAnalysisOptions = {
  step: 2,
  minOffset: 3,
  maxOffset: 20,
  candidateStep: 0.25,
  smoothness: 1.5,
  slopePenalty: 3.0,
  priorStrength: 0.5,
  confidenceThreshold: 0.6,
};

export const ANALYZER_VERSION = "0.2.0";
