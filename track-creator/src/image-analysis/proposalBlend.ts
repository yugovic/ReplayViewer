export interface HighConfidenceBlendOptions {
  threshold: number;
  step: number;
  closed: boolean;
  /** Majority-filter radius used to suppress isolated confidence flicker. */
  stabilizeRadiusMeters?: number;
  /** Radius of each of two box-blur passes at acceptance boundaries. */
  featherRadiusMeters?: number;
  /** Maximum lateral edge movement per metre along the track. */
  maxOffsetSlope?: number;
}

function sample(values: ArrayLike<number>, index: number, closed: boolean): number {
  const n = values.length;
  if (closed) return values[((index % n) + n) % n];
  return values[Math.max(0, Math.min(n - 1, index))];
}

function majorityFilter(values: number[], radius: number, closed: boolean): number[] {
  if (radius <= 0) return values.slice();
  const out = new Array<number>(values.length);
  const width = radius * 2 + 1;
  for (let i = 0; i < values.length; i++) {
    let sum = 0;
    for (let j = -radius; j <= radius; j++) sum += sample(values, i + j, closed);
    out[i] = sum * 2 >= width ? 1 : 0;
  }
  return out;
}

function boxBlur(values: number[], radius: number, closed: boolean): number[] {
  if (radius <= 0) return values.slice();
  const out = new Array<number>(values.length);
  const width = radius * 2 + 1;
  for (let i = 0; i < values.length; i++) {
    let sum = 0;
    for (let j = -radius; j <= radius; j++) sum += sample(values, i + j, closed);
    out[i] = sum / width;
  }
  return out;
}

function limitOffsetSlope(values: number[], maxDelta: number, closed: boolean): number[] {
  if (values.length < 2 || !Number.isFinite(maxDelta) || maxDelta <= 0) return values.slice();
  const out = values.slice();
  const clampTo = (index: number, neighbour: number) => {
    const lo = out[neighbour] - maxDelta;
    const hi = out[neighbour] + maxDelta;
    const next = Math.max(lo, Math.min(hi, out[index]));
    const changed = Math.abs(next - out[index]) > 1e-12;
    out[index] = next;
    return changed;
  };

  // One forward/backward pass is sufficient for an open chain. A closed loop
  // may need the seam constraint to propagate, but convergence is fast for the
  // short transitions produced above; cap at n passes for a hard bound.
  const maxPasses = closed ? out.length : 1;
  for (let pass = 0; pass < maxPasses; pass++) {
    let changed = false;
    const forwardStart = closed ? 0 : 1;
    for (let i = forwardStart; i < out.length; i++) {
      const previous = (i - 1 + out.length) % out.length;
      changed = clampTo(i, previous) || changed;
    }
    const backwardStart = closed ? out.length - 1 : out.length - 2;
    const backwardEnd = closed ? 0 : -1;
    for (let i = backwardStart; i > backwardEnd; i--) {
      const next = (i + 1) % out.length;
      changed = clampTo(i, next) || changed;
    }
    if (!changed) break;
  }
  return out;
}

/**
 * Replace low-confidence stations with a linear interpolation between the
 * nearest high-confidence neighbours.
 *
 * Rationale: reverting a rejected span to the "current" edge re-introduces
 * whatever placeholder was there before (e.g. a uniform 6.5 m), which shows up
 * as an abrupt narrow band between two accepted measured spans. Bridging the
 * gap between the accepted values is wrong by at most the real width trend,
 * and never by the placeholder's arbitrary value.
 *
 * Open-track ends (or a fully low-confidence input) fall back to the nearest
 * accepted value / the proposal itself.
 */
export function interpolateAcrossLowConfidence(
  proposed: number[],
  confidence: number[],
  threshold: number,
  closed: boolean,
): number[] {
  const n = proposed.length;
  if (n === 0) return [];
  if (proposed.length !== confidence.length) {
    throw new Error("interpolation arrays must have matching lengths");
  }
  const accepted = confidence.map(
    (value, i) => Number.isFinite(value) && value >= threshold && Number.isFinite(proposed[i]),
  );
  const acceptedCount = accepted.filter(Boolean).length;
  if (acceptedCount === 0 || acceptedCount === n) return proposed.slice();

  // Rotate a closed loop so index 0 is accepted: every low-confidence run then
  // has an accepted predecessor, and a run touching the end wraps to index 0.
  // This interpolates seam-crossing runs linearly instead of leaving a step at
  // the start/finish line.
  const rotation = closed ? accepted.findIndex(Boolean) : 0;
  const rotate = <T>(a: T[]) => a.slice(rotation).concat(a.slice(0, rotation));
  const values = rotate(proposed.slice());
  const mask = rotate(accepted);

  let i = 0;
  while (i < n) {
    if (mask[i]) {
      i++;
      continue;
    }
    const runStart = i;
    let runEnd = i;
    while (runEnd + 1 < n && !mask[runEnd + 1]) runEnd++;

    const prevValue = runStart > 0 ? values[runStart - 1] : undefined;
    const nextValue =
      runEnd + 1 < n ? values[runEnd + 1] : closed ? values[0] : undefined;

    const runLength = runEnd - runStart + 1;
    for (let k = 0; k < runLength; k++) {
      const index = runStart + k;
      if (prevValue !== undefined && nextValue !== undefined) {
        const t = (k + 1) / (runLength + 1);
        values[index] = prevValue + (nextValue - prevValue) * t;
      } else if (prevValue !== undefined) {
        values[index] = prevValue;
      } else if (nextValue !== undefined) {
        values[index] = nextValue;
      }
    }
    i = runEnd + 1;
  }

  return values.slice(n - rotation).concat(values.slice(0, n - rotation));
}

/**
 * Blend a proposal into the current edge using a spatially coherent version
 * of the high-confidence mask.
 *
 * A raw per-station threshold is unsafe for geometry: confidence can alternate
 * above/below the threshold every sample, which switches between the proposal
 * and current width every two metres and produces saw-tooth road edges. A
 * short majority filter removes isolated decisions, then two box passes form a
 * tent-shaped transition at genuine accepted/rejected boundaries. Broad
 * high-confidence spans still reach weight 1; broad low-confidence spans stay
 * at the current edge.
 */
export function blendHighConfidenceOffsets(
  current: number[],
  proposed: number[],
  confidence: number[],
  options: HighConfidenceBlendOptions,
): number[] {
  if (current.length !== proposed.length || current.length !== confidence.length) {
    throw new Error("high-confidence blend arrays must have matching lengths");
  }
  if (current.length === 0) return [];
  if (!Number.isFinite(options.step) || options.step <= 0) {
    throw new Error("high-confidence blend requires a positive step");
  }

  const stableRadius = Math.max(
    0,
    Math.round((options.stabilizeRadiusMeters ?? 4) / options.step),
  );
  const featherRadius = Math.max(
    0,
    Math.round((options.featherRadiusMeters ?? 20) / options.step),
  );
  const rawMask = confidence.map((value) =>
    Number.isFinite(value) && value >= options.threshold ? 1 : 0,
  );
  const stableMask = majorityFilter(rawMask, stableRadius, options.closed);
  const weights = boxBlur(
    boxBlur(stableMask, featherRadius, options.closed),
    featherRadius,
    options.closed,
  );

  const blended = current.map((value, index) => {
    const target = proposed[index];
    if (!Number.isFinite(value) || !Number.isFinite(target)) return value;
    return value + (target - value) * weights[index];
  });
  const maxDelta = (options.maxOffsetSlope ?? 0.25) * options.step;
  return limitOffsetSlope(blended, maxDelta, options.closed);
}
