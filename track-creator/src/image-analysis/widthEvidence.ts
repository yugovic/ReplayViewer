/**
 * Per-station evidence scoring for width extraction.
 *
 * A single high-contrast pixel is a poor road-edge detector: lane markings,
 * kerbs, shadows and cars all create stronger gradients than the actual
 * asphalt boundary in many aerial images. The scorer therefore uses robust
 * colour bands on both sides of every candidate and samples several points
 * along the driving direction before it scores a cross-section.
 */

import type { RasterSource, Rgb, Lab, WidthAnalysisOptions } from "./types.ts";
import { rgbToLab, labDistance } from "./raster.ts";
import { scoreStripeStrengths, STRIPE_SCORE_WEIGHT } from "./stripeEvidence.ts";

export interface CandidateScore {
  offset: number;
  score: number;
  /** Normalised strength of a persistent inner-to-outer transition. */
  boundaryStrength?: number;
  /** Fraction of valid samples in the bands used for this candidate. */
  coverage?: number;
}

export interface StationEvidence {
  /** Arc-length distance of this station. */
  dist: number;
  /** Candidate scores for the left edge (offset > 0, left side). */
  leftCandidates: CandidateScore[];
  /** Candidate scores for the right edge (offset > 0, right side). */
  rightCandidates: CandidateScore[];
  /** Whether a reliable road-colour model could be built at this station. */
  available: boolean;
}

interface ProfileSample {
  offset: number;
  lab: Lab | null;
  coverage: number;
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2;
}

function medianRgb(values: Rgb[]): Rgb | null {
  if (values.length === 0) return null;
  return {
    r: median(values.map((v) => v.r)),
    g: median(values.map((v) => v.g)),
    b: median(values.map((v) => v.b)),
  };
}

function medianLab(values: Lab[]): Lab | null {
  if (values.length === 0) return null;
  return {
    L: median(values.map((v) => v.L)),
    a: median(values.map((v) => v.a)),
    b: median(values.map((v) => v.b)),
  };
}

/**
 * Build one cross-track colour profile. At every lateral position we sample
 * seven points over a short along-track window and take the channel median.
 * This removes isolated vehicles, painted text and small shadow fragments
 * without blurring the lateral edge itself.
 */
function sampleRobustProfile(
  source: RasterSource,
  station: { x: number; z: number; tx: number; tz: number },
  maxOffset: number,
  candidateStep: number,
): ProfileSample[] {
  const normalX = -station.tz;
  const normalZ = station.tx;
  const alongStep = Math.max(1, Math.min(1.5, source.resolutionMetersPerPixel * 3));
  const alongOffsets = [-3, -2, -1, 0, 1, 2, 3].map((v) => v * alongStep);
  const required = Math.ceil(alongOffsets.length / 2);
  const result: ProfileSample[] = [];

  for (let off = -maxOffset; off <= maxOffset + candidateStep * 0.1; off += candidateStep) {
    const colours: Rgb[] = [];
    for (const along of alongOffsets) {
      const rgb = source.sampleLocal(
        station.x + normalX * off + station.tx * along,
        station.z + normalZ * off + station.tz * along,
      );
      if (rgb) colours.push(rgb);
    }
    const robustRgb = colours.length >= required ? medianRgb(colours) : null;
    result.push({
      offset: off,
      lab: robustRgb ? rgbToLab(robustRgb) : null,
      coverage: colours.length / alongOffsets.length,
    });
  }
  return result;
}

/** Estimate the road colour from a band around (not just on) the centreline. */
function estimateRoadSurface(samples: ProfileSample[], innerRadius: number): Lab | null {
  return medianLab(
    samples
      .filter((sample) => Math.abs(sample.offset) <= innerRadius && sample.lab)
      .map((sample) => sample.lab!),
  );
}

function scoreSide(
  samples: ProfileSample[],
  side: "left" | "right",
  roadModel: Lab,
  sourceResolution: number,
  options: WidthAnalysisOptions,
): CandidateScore[] {
  const sign = side === "left" ? -1 : 1;
  const sideSamples = samples
    .filter((sample) => sample.offset * sign >= -1e-9)
    .map((sample) => ({ ...sample, distance: Math.abs(sample.offset) }))
    .sort((a, b) => a.distance - b.distance);
  const candidates: CandidateScore[] = [];

  // Wide enough to reject a narrow painted line, but still local enough to
  // place an edge to roughly one source pixel.
  const bandWidth = Math.max(1, sourceResolution * 2, options.candidateStep * 4);
  const margin = Math.max(options.candidateStep * 0.5, sourceResolution * 0.25);

  const band = (from: number, to: number) =>
    sideSamples.filter((sample) => sample.distance >= from - 1e-9 && sample.distance <= to + 1e-9);
  const colourOf = (items: typeof sideSamples) =>
    medianLab(items.filter((v) => v.lab).map((v) => v.lab!));
  const coverageOf = (items: typeof sideSamples) =>
    items.length === 0 ? 0 : items.reduce((sum, item) => sum + item.coverage, 0) / items.length;

  for (let off = options.minOffset; off <= options.maxOffset + options.candidateStep * 0.1; off += options.candidateStep) {
    const innerBand = band(Math.max(0, off - bandWidth), Math.max(0, off - margin));
    const outerBand = band(off + margin, off + bandWidth);
    const farOuterBand = band(off + bandWidth, Math.min(options.maxOffset, off + bandWidth * 2.5));
    const innerLab = colourOf(innerBand);
    const outerLab = colourOf(outerBand);
    const farOuterLab = colourOf(farOuterBand) ?? outerLab;
    const coverage = Math.min(coverageOf(innerBand), coverageOf(outerBand));

    if (!innerLab || !outerLab || coverage < 0.45) {
      candidates.push({ offset: off, score: -6, boundaryStrength: 0, coverage });
      continue;
    }

    // A real paved edge has a different colour on both the near and far
    // outside. A painted line usually returns to the road colour immediately.
    const transition = labDistance(innerLab, outerLab);
    const nearOutsideDifference = labDistance(roadModel, outerLab);
    const farOutsideDifference = farOuterLab ? labDistance(roadModel, farOuterLab) : nearOutsideDifference;
    const persistentDifference = Math.min(nearOutsideDifference, farOutsideDifference);

    const insideEnd = Math.max(0.5, off - bandWidth * 0.5);
    const insideDistances = band(0.5, insideEnd)
      .filter((sample) => sample.lab)
      .map((sample) => labDistance(roadModel, sample.lab!));
    const insideMedianDistance = insideDistances.length > 0 ? median(insideDistances) : 40;
    const insideSupport = Math.exp(-insideMedianDistance / 18);

    const transitionScore = Math.min(transition / 14, 3);
    const outsideScore = Math.min((nearOutsideDifference * 0.65 + farOutsideDifference * 0.35) / 18, 2);
    const persistenceScore = Math.min(persistentDifference / 18, 1);
    const transientMarkingPenalty = Math.min(
      Math.max(0, nearOutsideDifference - farOutsideDifference) / 18,
      2,
    );

    const score =
      transitionScore +
      outsideScore +
      persistenceScore +
      insideSupport * 1.25 -
      transientMarkingPenalty;
    const boundaryStrength = clamp01(
      ((transition * 0.65 + persistentDifference * 0.35) / 28) * coverage,
    );

    candidates.push({ offset: off, score, boundaryStrength, coverage });
  }

  return candidates;
}

/**
 * Effective lateral search cap at one station/side after corridor constraints.
 * A wrapping range (startDist > endDist, closed tracks) covers
 * [startDist..total] ∪ [0..endDist]; membership needs no total length.
 */
export function effectiveMaxOffset(
  options: WidthAnalysisOptions,
  dist: number,
  side: "left" | "right",
): number {
  let max = options.maxOffset;
  for (const corridor of options.corridors ?? []) {
    if (corridor.side !== side) continue;
    const inRange =
      corridor.startDist <= corridor.endDist
        ? dist >= corridor.startDist && dist <= corridor.endDist
        : dist >= corridor.startDist || dist <= corridor.endDist;
    if (inRange) max = Math.min(max, corridor.maxOffset);
  }
  return Math.max(options.minOffset, max);
}

/** Compute robust cross-section evidence for all centreline stations. */
export function computeStationEvidence(
  source: RasterSource,
  stations: { x: number; z: number; tx: number; tz: number; dist: number }[],
  options: WidthAnalysisOptions,
  _currentLeft: number[],
  _currentRight: number[],
): StationEvidence[] {
  const innerRadius = Math.min(2, options.minOffset * 0.65);
  const results: StationEvidence[] = [];

  for (const station of stations) {
    const samples = sampleRobustProfile(source, station, options.maxOffset, options.candidateStep);
    const roadModel = estimateRoadSurface(samples, innerRadius);
    if (!roadModel) {
      results.push({
        dist: station.dist,
        leftCandidates: [],
        rightCandidates: [],
        available: false,
      });
      continue;
    }

    const leftOptions = { ...options, maxOffset: effectiveMaxOffset(options, station.dist, "left") };
    const rightOptions = { ...options, maxOffset: effectiveMaxOffset(options, station.dist, "right") };
    const leftCandidates = scoreSide(samples, "left", roadModel, source.resolutionMetersPerPixel, leftOptions);
    const rightCandidates = scoreSide(samples, "right", roadModel, source.resolutionMetersPerPixel, rightOptions);

    if (options.objective === "stripeFirst") {
      // A visible painted line outranks the asphalt boundary; where none is
      // visible the stripe strengths are ~0 and the boundary scores stand.
      fuseStripes(leftCandidates, samples, "left", roadModel, source.resolutionMetersPerPixel, leftOptions);
      fuseStripes(rightCandidates, samples, "right", roadModel, source.resolutionMetersPerPixel, rightOptions);
    }

    results.push({
      dist: station.dist,
      leftCandidates,
      rightCandidates,
      available: true,
    });
  }

  return results;
}

/** Add stripe bonuses onto boundary candidates (same offset grid by construction). */
function fuseStripes(
  candidates: CandidateScore[],
  samples: ProfileSample[],
  side: "left" | "right",
  roadModel: Lab,
  sourceResolution: number,
  options: WidthAnalysisOptions,
): void {
  const strengths = scoreStripeStrengths(samples, side, roadModel, sourceResolution, options);
  const count = Math.min(candidates.length, strengths.length);
  for (let i = 0; i < count; i++) {
    const strength = strengths[i];
    if (strength <= 0) continue;
    candidates[i].score += strength * STRIPE_SCORE_WEIGHT;
    candidates[i].boundaryStrength = Math.max(candidates[i].boundaryStrength ?? 0, strength);
  }
}
