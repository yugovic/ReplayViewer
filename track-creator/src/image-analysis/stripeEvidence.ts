/**
 * Painted edge-line ("stripe") evidence for width extraction.
 *
 * The racing surface of a circuit is bounded by painted white lines, and on
 * tracks with contiguous paving (pit lanes, paved run-off) the *asphalt*
 * boundary is metres beyond the *racing surface* boundary. The boundary
 * scorer in widthEvidence.ts deliberately penalises paint (it looks for a
 * persistent material change), so a separate scorer measures the opposite
 * signal: a narrow, bright luminance ridge over road-coloured paving.
 *
 * Scores are produced on exactly the candidate grid used by scoreSide()
 * (offset = minOffset .. maxOffset step candidateStep) so callers can zip the
 * two candidate lists together.
 */

import type { Lab, WidthAnalysisOptions } from "./types.ts";
import { labDistance } from "./raster.ts";

export interface StripeProfileSample {
  offset: number;
  lab: Lab | null;
  coverage: number;
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Per-candidate stripe strength (0..1) for one side of one station.
 *
 * A candidate scores high when its luminance rises clearly above BOTH lateral
 * neighbours (a ridge, not a step), it is brighter than the road surface, and
 * the paving between the centreline and the candidate still looks like road
 * (so lines beyond grass or gravel never win).
 */
export function scoreStripeStrengths(
  samples: StripeProfileSample[],
  side: "left" | "right",
  roadModel: Lab,
  sourceResolution: number,
  options: WidthAnalysisOptions,
): number[] {
  const sign = side === "left" ? -1 : 1;
  const sideSamples = samples
    .filter((sample) => sample.offset * sign >= -1e-9)
    .map((sample) => ({ ...sample, distance: Math.abs(sample.offset) }))
    .sort((a, b) => a.distance - b.distance);

  const byDistance = (d: number) => {
    // The profile is sampled on the candidateStep grid; pick the nearest.
    let best = sideSamples[0];
    for (const sample of sideSamples) {
      if (Math.abs(sample.distance - d) < Math.abs(best.distance - d)) best = sample;
    }
    return best;
  };

  // Wide enough to sit beside a 0.2–0.5 m painted line even after the image
  // has blurred it over a pixel or two.
  const ridgeSpacing = Math.max(0.5, sourceResolution * 1.5, options.candidateStep * 2);
  const innerStart = Math.max(0.5, options.minOffset * 0.5);

  const strengths: number[] = [];
  for (
    let off = options.minOffset;
    off <= options.maxOffset + options.candidateStep * 0.1;
    off += options.candidateStep
  ) {
    const centre = byDistance(off);
    const inner = byDistance(off - ridgeSpacing);
    const outer = byDistance(off + ridgeSpacing);
    if (!centre?.lab || !inner?.lab || !outer?.lab) {
      strengths.push(0);
      continue;
    }
    const coverage = Math.min(centre.coverage, inner.coverage, outer.coverage);
    if (coverage < 0.45) {
      strengths.push(0);
      continue;
    }

    // Ridge: brighter than both lateral neighbours (a step up to bright
    // concrete or a building would only clear one side).
    const rise = Math.min(centre.lab.L - inner.lab.L, centre.lab.L - outer.lab.L);
    // And brighter than the road the line is painted on.
    const brightness = centre.lab.L - roadModel.L;

    // Everything between the centreline and the line must still be paving —
    // this is what makes the *racing-surface* line win over paint or bright
    // objects beyond the asphalt.
    const insideDistances = sideSamples
      .filter((s) => s.distance >= innerStart && s.distance <= off - ridgeSpacing && s.lab)
      .map((s) => labDistance(roadModel, s.lab!));
    const insideSupport =
      insideDistances.length > 0 ? Math.exp(-median(insideDistances) / 18) : 0;

    const strength =
      clamp01(rise / 18) * clamp01(brightness / 15) * insideSupport * clamp01(coverage / 0.9);
    strengths.push(strength);
  }
  return strengths;
}

/**
 * Weight that converts a stripe strength into a candidate-score bonus.
 * The boundary scorer tops out around 6–7; a clear stripe (strength ≈ 1)
 * must beat a clear asphalt boundary elsewhere on the section, so the bonus
 * is scaled past that ceiling. A weak stripe (strength ≲ 0.3) only breaks
 * ties and cannot override a strong boundary.
 */
export const STRIPE_SCORE_WEIGHT = 9;
