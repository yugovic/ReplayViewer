/**
 * Dynamic-programming optimisation for edge offsets along the full track.
 *
 * Closed tracks are solved as a loop. The previous implementation solved an
 * open chain and altered a few output values at the seam afterwards, which
 * could create non-candidate offsets and visible start/finish kinks.
 */

import type { StationEvidence, CandidateScore } from "./widthEvidence.ts";
import type { WidthAnalysisOptions } from "./types.ts";

interface DPState {
  /** Best cumulative score ending at candidate j for station i. */
  cost: number;
  /** Backpointer: candidate index at station i-1. */
  prev: number;
}

interface DPResult {
  offsets: number[];
  cost: number;
}

function transitionPenalty(a: number, b: number, options: WidthAnalysisOptions): number {
  const offsetDiff = Math.abs(a - b);
  let penalty = options.smoothness * offsetDiff;
  if (offsetDiff > 3) {
    penalty += options.slopePenalty * (offsetDiff - 3) ** 2;
  }
  return penalty;
}

function priorPenalty(offset: number, current: number, options: WidthAnalysisOptions): number {
  if (!Number.isFinite(current)) return 0;
  return options.priorStrength * Math.abs(offset - current) * 0.1;
}

/** Solve an open chain, optionally fixing its first candidate and closing it. */
function runDp(
  candidates: CandidateScore[][],
  currentOffsets: number[],
  options: WidthAnalysisOptions,
  fixedStart: number | null,
  closeLoop: boolean,
): DPResult {
  const n = candidates.length;
  const dp: DPState[][] = new Array(n);
  dp[0] = candidates[0].map((candidate, index) => ({
    cost:
      fixedStart === null || fixedStart === index
        ? candidate.score - priorPenalty(candidate.offset, currentOffsets[0], options)
        : -Infinity,
    prev: -1,
  }));

  for (let i = 1; i < n; i++) {
    const previous = candidates[i - 1];
    const current = candidates[i];
    dp[i] = new Array(current.length);

    for (let j = 0; j < current.length; j++) {
      let bestCost = -Infinity;
      let bestPrevious = 0;
      const localCost =
        current[j].score - priorPenalty(current[j].offset, currentOffsets[i], options);

      for (let k = 0; k < previous.length; k++) {
        const total =
          dp[i - 1][k].cost -
          transitionPenalty(current[j].offset, previous[k].offset, options) +
          localCost;
        if (total > bestCost) {
          bestCost = total;
          bestPrevious = k;
        }
      }
      dp[i][j] = { cost: bestCost, prev: bestPrevious };
    }
  }

  let bestEnd = 0;
  let bestCost = -Infinity;
  const startOffset = fixedStart === null ? 0 : candidates[0][fixedStart].offset;
  for (let j = 0; j < dp[n - 1].length; j++) {
    const closePenalty = closeLoop
      ? transitionPenalty(candidates[n - 1][j].offset, startOffset, options)
      : 0;
    const total = dp[n - 1][j].cost - closePenalty;
    if (total > bestCost) {
      bestCost = total;
      bestEnd = j;
    }
  }

  const path: number[] = new Array(n);
  path[n - 1] = bestEnd;
  for (let i = n - 1; i > 0; i--) {
    path[i - 1] = dp[i][path[i]].prev;
  }
  return {
    offsets: path.map((candidateIndex, stationIndex) =>
      candidates[stationIndex][candidateIndex].offset),
    cost: bestCost,
  };
}

/** Pick a well-observed station at which to condition the circular DP. */
function strongestAnchor(candidates: CandidateScore[][]): number {
  let bestIndex = 0;
  let bestStrength = -Infinity;
  for (let i = 0; i < candidates.length; i++) {
    const cands = candidates[i];
    if (cands.length <= 1) continue;
    const ranked = cands.slice().sort((a, b) => b.score - a.score);
    const best = ranked[0];
    const distinct = ranked.find((candidate) => Math.abs(candidate.offset - best.offset) >= 1);
    const prominence = best.score - (distinct?.score ?? best.score);
    const strength = (best.boundaryStrength ?? 0) * 2 + Math.max(0, prominence) * 0.15;
    if (strength > bestStrength) {
      bestStrength = strength;
      bestIndex = i;
    }
  }
  return bestIndex;
}

/**
 * The anchor is strongly observed, so evaluating its two best distinct peaks
 * plus the current-width prior gives circular behaviour at a fraction of the
 * cost of fixing every possible start candidate.
 */
function startCandidates(
  candidates: CandidateScore[],
  currentOffset: number,
  options: WidthAnalysisOptions,
): number[] {
  if (candidates.length <= 1) return [0];
  const ranked = candidates
    .map((candidate, index) => ({ candidate, index }))
    .sort((a, b) => b.candidate.score - a.candidate.score);
  const result: number[] = [ranked[0].index];
  const separation = Math.max(1, options.candidateStep * 3);
  const secondPeak = ranked.find(
    ({ candidate }) =>
      Math.abs(candidate.offset - candidates[result[0]].offset) >= separation,
  );
  if (secondPeak) result.push(secondPeak.index);

  let closest = 0;
  for (let i = 1; i < candidates.length; i++) {
    if (
      Math.abs(candidates[i].offset - currentOffset) <
      Math.abs(candidates[closest].offset - currentOffset)
    ) {
      closest = i;
    }
  }
  if (!result.includes(closest)) result.push(closest);
  return result.slice(0, 3);
}

function optimizeSide(
  evidence: StationEvidence[],
  sideCandidates: CandidateScore[][],
  options: WidthAnalysisOptions,
  closed: boolean,
  currentOffsets: number[],
): number[] {
  const n = evidence.length;
  if (n === 0) return [];

  const candidates = evidence.map((station, i) => {
    const available = station.available && sideCandidates[i]?.length > 0;
    if (available) return sideCandidates[i];
    return [{
      offset: Number.isFinite(currentOffsets[i]) ? currentOffsets[i] : 10,
      score: -5,
      boundaryStrength: 0,
      coverage: 0,
    }];
  });

  if (!closed || n < 2) {
    return runDp(candidates, currentOffsets, options, null, false).offsets;
  }

  const anchor = strongestAnchor(candidates);
  const rotatedCandidates = candidates.slice(anchor).concat(candidates.slice(0, anchor));
  const rotatedCurrent = currentOffsets.slice(anchor).concat(currentOffsets.slice(0, anchor));
  const possibleStarts = startCandidates(rotatedCandidates[0], rotatedCurrent[0], options);
  let best: DPResult | null = null;
  for (const start of possibleStarts) {
    const result = runDp(rotatedCandidates, rotatedCurrent, options, start, true);
    if (!best || result.cost > best.cost) best = result;
  }

  const offsets = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    offsets[(anchor + i) % n] = best!.offsets[i];
  }
  return offsets;
}

/** Run full-track optimisation for both left and right edges. */
export function optimizeEdges(
  evidence: StationEvidence[],
  options: WidthAnalysisOptions,
  closed: boolean,
  currentLeft: number[],
  currentRight: number[],
): { left: number[]; right: number[] } {
  return {
    left: optimizeSide(
      evidence,
      evidence.map((station) => station.leftCandidates),
      options,
      closed,
      currentLeft,
    ),
    right: optimizeSide(
      evidence,
      evidence.map((station) => station.rightCandidates),
      options,
      closed,
      currentRight,
    ),
  };
}
