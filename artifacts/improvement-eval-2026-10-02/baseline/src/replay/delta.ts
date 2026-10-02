/**
 * Delta time and distance-alignment utilities for ghost comparison.
 *
 * Distance-based alignment: to avoid ECU clock drift between laps, we find
 * each lap's timestamp at a given distance value by inverting the dist[] array
 * (which is monotonically increasing within a lap).
 */

import type { LapData } from "./types";

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
 * Returns the delta time (seconds) between main and ghost at a given distance.
 * Positive = main is behind (slower); Negative = main is ahead (faster).
 *
 * @param mainLap  The primary lap being replayed.
 * @param ghostLap The reference (ghost) lap.
 * @param dist     Track distance in metres.
 */
export function computeDeltaAtDist(mainLap: LapData, ghostLap: LapData, dist: number): number {
  const mainTime = timeAtDist(mainLap, dist);
  const ghostTime = timeAtDist(ghostLap, dist);
  return mainTime - ghostTime;
}

/**
 * Given the main lap's current playback time, returns the ghost lap's
 * equivalent playback time based on shared distance.
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

  return timeAtDist(ghostLap, currentDist);
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
 */
export function computeSectorTimes(
  lap: LapData,
  boundaries: number[],
  currentDist: number,
): Array<number | undefined> {
  const sectorCount = boundaries.length - 1;
  const result: Array<number | undefined> = Array(sectorCount).fill(undefined);
  const lapStart = lap.t[0] ?? 0;

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
