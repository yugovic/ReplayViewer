/**
 * Track definition schema (track.json) and the pre-fetched elevation file
 * (elevation.json). Conventions match the ReplayViewer repo throughout:
 * units are metres, ENU with Y up — x = east, z = -north ("z_north_neg").
 */

export interface TrackDef {
  name: string;
  /** Closed circuit (loop) or open point-to-point course. */
  closed: boolean;
  /**
   * Geographic anchor of local (0, 0). Required when elevation.source is
   * "gsi". `alt` is the ASL height of local y = 0; when omitted it defaults
   * to the sampled elevation at the first centerline station.
   */
  origin?: { lat: number; lng: number; alt?: number };
  /** Centerline control points, local metres. A Catmull-Rom spline runs through them. */
  controlPoints: { x: number; z: number }[];
  elevation: ElevationConfig;
  road: RoadConfig;
  curbs?: CurbSpanDef[];
  /** Editor-side image-analysis knobs (persisted so re-runs reproduce). */
  analysisHints?: AnalysisHints;
}

export interface ElevationConfig {
  /**
   * "gsi"       — 国土地理院 標高タイル (dem5a, fallback dem10b). Needs `origin`.
   * "synthetic" — deterministic rolling hills, for offline demos.
   * "flat"      — everything at y = 0.
   * "imported"  — elevation.json was produced by an importer (e.g. from logger
   *               / LiDAR data); `npm run elevation` must not overwrite it.
   */
  source: "gsi" | "synthetic" | "flat" | "imported";
  /** Sampling interval along the centerline, metres. */
  step: number;
  /** Moving-average window (sample count, odd) applied to elevations. Default 9. */
  smooth?: number;
  /** GSI tile zoom (default 15; dem5a exists only at z15). */
  gsiZoom?: number;
}

export interface RoadConfig {
  /** Default paved width, metres. */
  width: number;
  /** Station spacing of the generated road mesh, metres. */
  step: number;
  /**
   * Optional width keys `{ dist, width }`, linearly interpolated by arc-length.
   * On closed tracks the interpolation wraps from the last key back to the first.
   */
  widthProfile?: { dist: number; width: number }[];
  /**
   * Free-form edge shape: per-side lateral offsets from the centerline (m,
   * positive outward), sampled uniformly every `step` metres along the track
   * (sample i sits at dist = i * step; closed tracks wrap). When present this
   * OVERRIDES width/widthProfile. The editor's edge-point tool writes this.
   */
  edges?: EdgeOffsets;
}

export interface EdgeOffsets {
  /** Sample spacing, metres (e.g. 1). */
  step: number;
  left: number[];
  right: number[];
  /**
   * Per-sample acceptance confidence (0..1) recorded when the arrays were
   * written by the image analysis. Absent on hand-edited or legacy edges.
   * Builders ignore these; they let later passes (runoff layer, re-analysis)
   * tell measured spans from interpolated ones.
   */
  confidenceLeft?: number[];
  confidenceRight?: number[];
}

/** Editor-side knobs for the image analysis, persisted with the track. */
export interface AnalysisHints {
  /**
   * Lateral search caps by dist range and side, for places where contiguous
   * paving (pit lane, paved run-off) would drag the detected edge beyond the
   * racing surface. startDist > endDist wraps across the start/finish line.
   */
  corridors?: {
    startDist: number;
    endDist: number;
    side: "left" | "right";
    maxOffset: number;
  }[];
  /** Edge definition: painted line first, or raw asphalt boundary. */
  objective?: "boundary" | "stripeFirst";
}

export interface CurbSpanDef {
  /** Arc-length range along the centerline, metres. */
  startDist: number;
  endDist: number;
  /** Side relative to increasing centerline distance (driving direction). */
  side: "left" | "right";
  /** Ribbon width, metres. */
  width: number;
  /** Length of ONE stripe (one red or one white block), metres. */
  stripeLength: number;
}

// ─── refs.json (optional, per track) ─────────────────────────────────────────

/** Reference polyline shown in the editor (logger lap, GPS trace, …). Local ENU metres. */
export interface RefLine {
  name: string;
  /** CSS hex color, e.g. "#4da3ff". */
  color?: string;
  closed?: boolean;
  /** [x, y, z] triplets. */
  points: [number, number, number][];
}

export interface RefsData {
  lines: RefLine[];
}

// ─── elevation.json ──────────────────────────────────────────────────────────

export interface ElevationSample {
  dist: number;
  x: number;
  z: number;
  /** Local-Y elevations at the left corridor edge, centerline, right corridor edge. */
  yLeft: number;
  yCenter: number;
  yRight: number;
}

export interface ElevationData {
  source: string;
  /** Sampling interval used, metres. */
  step: number;
  /** Lateral offset at which yLeft / yRight were sampled, metres. */
  corridorHalfWidth: number;
  /** Total centerline length at sampling time, metres. */
  totalLength: number;
  /** ASL height of local y = 0 (only meaningful for source "gsi"). */
  originAlt?: number;
  samples: ElevationSample[];
}
