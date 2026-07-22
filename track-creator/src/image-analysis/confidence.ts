/**
 * Confidence scoring for width extraction proposals.
 * Confidence is an acceptance indicator, not a probability.
 */

import type { StationEvidence, CandidateScore } from "./widthEvidence.ts";

export interface ConfidenceInput {
  evidence: StationEvidence[];
  offsets: number[];
  resolutionMetersPerPixel: number;
  side: "left" | "right";
}

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

function nearestCandidate(candidates: CandidateScore[], offset: number): CandidateScore {
  let nearest = candidates[0];
  for (let i = 1; i < candidates.length; i++) {
    if (Math.abs(candidates[i].offset - offset) < Math.abs(nearest.offset - offset)) {
      nearest = candidates[i];
    }
  }
  return nearest;
}

/** Compute per-station confidence scores (0..1) for one side. */
export function computeConfidence(input: ConfidenceInput): number[] {
  const { evidence, offsets, resolutionMetersPerPixel } = input;
  const n = evidence.length;
  const result: number[] = new Array(n);
  const distinctSeparation = Math.max(1, resolutionMetersPerPixel * 2);

  for (let i = 0; i < n; i++) {
    if (!evidence[i].available) {
      result[i] = 0;
      continue;
    }

    const candidates =
      input.side === "left" ? evidence[i].leftCandidates : evidence[i].rightCandidates;
    if (candidates.length === 0) {
      result[i] = 0;
      continue;
    }

    const chosen = nearestCandidate(candidates, offsets[i]);
    if ((chosen.coverage ?? 1) < 0.45 || chosen.score < 0) {
      result[i] = 0;
      continue;
    }

    // Adjacent 0.25m candidates are samples of the same broad edge peak, not
    // competing interpretations. Compare against the best distinct peak.
    const alternatives = candidates.filter(
      (candidate) => Math.abs(candidate.offset - chosen.offset) >= distinctSeparation,
    );
    const bestAlternative = alternatives.reduce(
      (best, candidate) => Math.max(best, candidate.score),
      -Infinity,
    );
    const distinctMargin = Number.isFinite(bestAlternative)
      ? Math.max(0, chosen.score - bestAlternative)
      : Math.max(0, chosen.score);
    const marginScore = clamp01(distinctMargin / 2);

    // Penalise a globally-smoothed path when it ignores much stronger local
    // evidence, even if the resulting path itself looks smooth.
    const localBest = candidates.reduce((best, candidate) => Math.max(best, candidate.score), -Infinity);
    const selectedSupport = clamp01(chosen.score / 6) * clamp01(1 - (localBest - chosen.score) / 3);
    const boundaryScore = clamp01(chosen.boundaryStrength ?? chosen.score / 7);

    let neighbourSlope = 0;
    let neighbourCount = 0;
    if (i > 0) {
      const gap = Math.max(0.1, evidence[i].dist - evidence[i - 1].dist);
      neighbourSlope += Math.abs(offsets[i] - offsets[i - 1]) / gap;
      neighbourCount++;
    }
    if (i + 1 < n) {
      const gap = Math.max(0.1, evidence[i + 1].dist - evidence[i].dist);
      neighbourSlope += Math.abs(offsets[i + 1] - offsets[i]) / gap;
      neighbourCount++;
    }
    const averageSlope = neighbourCount > 0 ? neighbourSlope / neighbourCount : 0;
    const consistencyScore = clamp01(1 - averageSlope / 0.75);

    const qualityScore = clamp01(1.15 - resolutionMetersPerPixel * 0.55);
    const coverageScore = clamp01(chosen.coverage ?? 1);

    result[i] = clamp01(
      marginScore * 0.20 +
      selectedSupport * 0.20 +
      boundaryScore * 0.30 +
      consistencyScore * 0.12 +
      qualityScore * 0.08 +
      coverageScore * 0.10,
    );
  }

  return result;
}

/** Classify confidence into three tiers for display. */
export type ConfidenceTier = "high" | "medium" | "low";

export function classifyConfidence(confidence: number, threshold: number): ConfidenceTier {
  if (confidence >= threshold) return "high";
  if (confidence >= threshold * 0.5) return "medium";
  return "low";
}

/** Hex colours for each tier (for overlay rendering). */
export const CONFIDENCE_COLORS: Record<ConfidenceTier, string> = {
  high: "#22c55e",
  medium: "#eab308",
  low: "#ef4444",
};
