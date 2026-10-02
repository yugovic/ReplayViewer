/**
 * Delta time and distance-alignment utilities for ghost comparison.
 *
 * Distance-based alignment: to avoid ECU clock drift between laps, we find
 * each lap's timestamp at a given distance value by inverting the dist[] array
 * (which is monotonically increasing within a lap).
 *
 * Lap-to-lap comparisons use the TRUE lap basis (see lapClock.ts): each lap's
 * t/dist start at its first sample, which is δ seconds / s0 metres past the
 * timing line, and δ differs per lap (7–97 ms on Fuji). Comparing raw t and
 * dist would mix δ_main − δ_ghost and (s0_main − s0_ghost) / v into the delta.
 * The arrays are never modified; offsets are applied at query time.
 */

import { sampleReplay } from "./interpolation";
import { lapStartOffsetMeters, lapStartOffsetSeconds } from "./lapClock";
import type { LapData, ReplaySample, TrackData } from "./types";

/**
 * Binary-search for the time at which a lap reaches a given distance.
 * If dist is beyond the lap's total distance, returns the final timestamp.
 */
export function timeAtDist(lap: LapData, targetDist: number): number {
  const { dist, t } = lap;
  const n = dist.length;
  if (n === 0) return 0;
  if (targetDist <= dist[0]) return t[0];
  if (targetDist >= dist[n - 1]) return t[n - 1];

  let lo = 0;
  let hi = n - 2;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (dist[mid] <= targetDist && targetDist <= dist[mid + 1]) {
      const span = dist[mid + 1] - dist[mid];
      const ratio = span > 0 ? (targetDist - dist[mid]) / span : 0;
      return t[mid] + (t[mid + 1] - t[mid]) * ratio;
    }
    if (dist[mid] < targetDist) lo = mid + 1;
    else hi = mid - 1;
  }
  return t[n - 1];
}

/**
 * True lap time (recorded t + δ) at a TRUE lap distance (metres from the
 * timing line, = recorded dist + s0). Between the line and the first sample
 * the time is interpolated linearly from t[0] (the line) to t[0] + δ.
 * With δ = 0 this is exactly timeAtDist(lap, trueDist).
 */
export function trueTimeAtTrueDist(lap: LapData, trueDist: number): number {
  const delta = lapStartOffsetSeconds(lap.meta);
  const s0 = lapStartOffsetMeters(lap);
  const recordedDist = trueDist - s0;
  const firstDist = lap.dist[0] ?? 0;
  if (s0 > 0 && recordedDist < firstDist) {
    const fraction = Math.min(1, Math.max(0, (trueDist - firstDist) / s0));
    return (lap.t[0] ?? 0) + delta * fraction;
  }
  return timeAtDist(lap, recordedDist) + delta;
}

/**
 * Returns the delta time (seconds) between main and ghost at a given distance,
 * in true lap time at the same true lap distance.
 * Positive = main is behind (slower); Negative = main is ahead (faster).
 *
 * @param mainLap  The primary lap being replayed.
 * @param ghostLap The reference (ghost) lap.
 * @param dist     The MAIN lap's recorded distance in metres (telemetry.dist).
 */
export function computeDeltaAtDist(mainLap: LapData, ghostLap: LapData, dist: number): number {
  const trueDist = dist + lapStartOffsetMeters(mainLap);
  return trueTimeAtTrueDist(mainLap, trueDist) - trueTimeAtTrueDist(ghostLap, trueDist);
}

/**
 * Playback time (this lap's recorded t basis) at a TRUE lap distance, i.e.
 * trueTimeAtTrueDist − δ. Over the first s0 metres — between the timing line
 * and the first sample, where the lap has no data — it runs linearly from
 * t[0] − δ (the line) to t[0], so it can precede t[0] by up to δ. Sample such
 * a time with sampleReplayWithLeadIn; sampleReplay clamps it to the first
 * sample. Beyond the last sample it is clamped to t[n−1].
 */
export function playbackTimeAtTrueDist(lap: LapData, trueDist: number): number {
  return trueTimeAtTrueDist(lap, trueDist) - lapStartOffsetSeconds(lap.meta);
}

/**
 * Given the main lap's current playback time, returns the ghost lap's
 * equivalent playback time (ghost t basis) at the same true lap distance.
 *
 * At the lap start the main car can be at a true distance the ghost has no
 * sample for yet (s0_main < s0_ghost: the ghost's first sample was taken
 * further past the line). The returned time then precedes the ghost's t[0]
 * (lead-in) instead of being clamped to it; clamping parked the ghost at its
 * first sample, s0_ghost − s0_main (up to 4.3 m on Fuji) ahead of the
 * same-distance point, until the main car caught up.
 */
export function ghostTimeForMainTime(mainLap: LapData, ghostLap: LapData, mainTime: number): number {
  // Find the current distance of the main lap at mainTime.
  const { t, dist } = mainLap;
  const n = t.length;
  if (n === 0) return 0;

  // Interpolate dist at mainTime using binary search on t[].
  let currentDist: number;
  if (mainTime <= t[0]) {
    currentDist = dist[0];
  } else if (mainTime >= t[n - 1]) {
    currentDist = dist[n - 1];
  } else {
    let lo = 0;
    let hi = n - 2;
    currentDist = dist[n - 1];
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= mainTime && mainTime <= t[mid + 1]) {
        const span = t[mid + 1] - t[mid];
        const ratio = span > 0 ? (mainTime - t[mid]) / span : 0;
        currentDist = dist[mid] + (dist[mid + 1] - dist[mid]) * ratio;
        break;
      }
      if (t[mid] < mainTime) lo = mid + 1;
      else hi = mid - 1;
    }
  }

  // Same physical point: the main lap's true distance (from the timing line).
  return playbackTimeAtTrueDist(ghostLap, currentDist + lapStartOffsetMeters(mainLap));
}

/**
 * sampleReplay that, for a time before the lap's first sample (the lead-in of
 * ghostTimeForMainTime), continues the first recorded segment backwards at
 * its own velocity instead of clamping. That slope equals the position
 * spline's tangent at t[0], so the pose stays continuous through the first
 * sample. Position (x, y, z) and telemetry.dist are extrapolated; heading,
 * pitch, roll and the other channels hold their first-sample values. The
 * lead-in is at most δ (≤ 1 s by lapClock's validation; 7–97 ms on Fuji).
 */
export function sampleReplayWithLeadIn(lap: LapData, track: TrackData, time: number): ReplaySample {
  const { t } = lap;
  if (t.length < 2 || !(time < t[0])) return sampleReplay(lap, track, time);
  const first = sampleReplay(lap, track, t[0]);
  const span = t[1] - t[0];
  if (!(span > 0)) return first;
  const second = sampleReplay(lap, track, t[1]);
  const k = (time - t[0]) / span; // < 0
  first.x += (second.x - first.x) * k;
  first.y += (second.y - first.y) * k;
  first.z += (second.z - first.z) * k;
  first.telemetry.dist += (second.telemetry.dist - first.telemetry.dist) * k;
  first.time = time;
  return first;
}

/** x axis of the telemetry graph: true lap distance (m) or true lap time (s). */
export type TelemetryAxis = "dist" | "time";

/**
 * Offset from a lap's recorded axis (t or dist, counted from its first
 * sample) to the TRUE lap axis the HUD uses (counted from the timing line):
 * δ seconds for time, s0 metres for distance. 0 when δ is unknown.
 */
export function trueAxisOffset(lap: LapData, axis: TelemetryAxis): number {
  return axis === "time" ? lapStartOffsetSeconds(lap.meta) : lapStartOffsetMeters(lap);
}

/**
 * Playback time (the lap's recorded t basis) at a value of the true lap axis:
 * same true lap time (x − δ) or same true distance (playbackTimeAtTrueDist).
 * Can precede t[0] by up to δ; series interpolation and seek clamp it.
 */
export function playbackTimeAtTrueAxis(lap: LapData, axis: TelemetryAxis, x: number): number {
  return axis === "time" ? x - lapStartOffsetSeconds(lap.meta) : playbackTimeAtTrueDist(lap, x);
}

/**
 * Split total distance into N equal sectors.
 * Returns an array of sector boundaries: [0, totalDist/N, 2*totalDist/N, ..., totalDist]
 */
export function buildSectorBoundaries(totalDist: number, count = 3): number[] {
  const boundaries: number[] = [];
  for (let i = 0; i <= count; i++) {
    boundaries.push((totalDist * i) / count);
  }
  return boundaries;
}

/**
 * Returns 0-based sector index (0, 1, 2…) for a given dist, given a sorted
 * boundaries array from buildSectorBoundaries().
 */
export function getSectorIndex(dist: number, boundaries: number[]): number {
  for (let i = 0; i < boundaries.length - 1; i++) {
    if (dist < boundaries[i + 1]) return i;
  }
  return boundaries.length - 2;
}

/**
 * Given a lap's dist/t arrays and sector boundaries, compute the elapsed time
 * when each sector boundary is first reached. Returns an array of [sectorIndex,
 * timeAtBoundary] pairs for the sectors that have been passed (dist-wise) at
 * the given current dist.
 *
 * Returns per-sector elapsed times (seconds) for completed or in-progress sectors.
 * sectors[i] = time elapsed since lap start when sector i ends; undefined if not yet reached.
 * Sector 1 starts at the true lap start (the timing line, δ before t[0]), so
 * it includes δ and the sector sum equals the true lap clock.
 */
export function computeSectorTimes(
  lap: LapData,
  boundaries: number[],
  currentDist: number,
): Array<number | undefined> {
  const sectorCount = boundaries.length - 1;
  const result: Array<number | undefined> = Array(sectorCount).fill(undefined);
  const lapStart = (lap.t[0] ?? 0) - lapStartOffsetSeconds(lap.meta);

  for (let i = 0; i < sectorCount; i++) {
    const sectorStartDist = boundaries[i];
    const sectorEndDist = boundaries[i + 1];
    const tSectorStart = i === 0 ? lapStart : timeAtDist(lap, sectorStartDist);

    if (currentDist >= sectorEndDist) {
      // Sector completed: return time within this sector
      const tAtBoundary = timeAtDist(lap, sectorEndDist);
      result[i] = tAtBoundary - tSectorStart;
    } else if (currentDist >= sectorStartDist) {
      // Currently in this sector – show in-progress time within sector
      const tNow = timeAtDist(lap, currentDist);
      result[i] = tNow - tSectorStart;
    }
    // else: sector not yet reached → undefined
  }
  return result;
}
