/**
 * Cinematic auto-director – pure shot-selection logic for camera mode 6.
 *
 * Given the car's current channel values (speed / throttle / brake / dist) and
 * the running cut index, returns which shot the "TV director" should cut to:
 *
 *   braking into a corner   → fixed TV point (classic braking-zone shot)
 *   corner-exit acceleration → chase camera
 *   flat-out high speed     → low TV pan (camera close to the ground)
 *   every few cuts          → cockpit for variety
 *
 * All functions are deterministic and side-effect free so they can be unit
 * tested; the CinematicCamera controller consumes them at runtime.
 */

export type ShotType = "tv" | "tv_low" | "chase" | "cockpit";

export interface DirectorInput {
  /** Current lap distance in metres */
  dist: number;
  /** Speed in km/h */
  speedKmh: number;
  /** Throttle 0–100 (%) */
  aps: number;
  /** Brake 0–100+ (%) */
  brake: number;
  /** Number of cuts made so far (0 = first cut) */
  cutIndex: number;
}

/** Every N-th cut goes to the cockpit for variety */
export const COCKPIT_EVERY_N_CUTS = 5;
/** Brake threshold (%) treated as "braking into a corner" */
export const BRAKING_THRESHOLD = 18;
/** Throttle threshold (%) treated as "accelerating out of a corner" */
export const ACCEL_THRESHOLD = 55;
/** Below this speed (km/h) heavy throttle is considered corner exit */
export const CORNER_EXIT_MAX_SPEED = 145;
/** Above this speed (km/h) we treat the section as flat-out */
export const HIGH_SPEED_THRESHOLD = 170;

/** Minimum and maximum cut length in seconds */
export const CUT_MIN_S = 4;
export const CUT_MAX_S = 8;

/**
 * Pick the shot type for the next cut based on what the car is doing.
 * Pure function: same inputs always produce the same shot.
 */
export function selectShot(input: DirectorInput): ShotType {
  const { speedKmh, aps, brake, cutIndex } = input;

  // Occasional cockpit shot for variety (never as the very first cut)
  if (cutIndex > 0 && cutIndex % COCKPIT_EVERY_N_CUTS === 0) {
    return "cockpit";
  }

  // Braking into a corner → fixed TV point watching the entry
  if (brake >= BRAKING_THRESHOLD) {
    return "tv";
  }

  // Flat-out high-speed section → low, ground-hugging TV pan
  if (speedKmh >= HIGH_SPEED_THRESHOLD) {
    return "tv_low";
  }

  // Accelerating out of a corner → chase camera behind the car
  if (aps >= ACCEL_THRESHOLD && speedKmh <= CORNER_EXIT_MAX_SPEED) {
    return "chase";
  }

  // Default: alternate between TV and chase so it never gets static
  return cutIndex % 2 === 0 ? "tv" : "chase";
}

/** Deterministic hash of an integer to [0, 1) (mulberry-style mix) */
export function hash01(n: number): number {
  let t = (n + 0x6d2b79f5) >>> 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 0x100000000;
}

/**
 * Length of the given cut in seconds, deterministically pseudo-random
 * within [CUT_MIN_S, CUT_MAX_S).
 */
export function cutDuration(cutIndex: number, minS = CUT_MIN_S, maxS = CUT_MAX_S): number {
  return minS + (maxS - minS) * hash01(cutIndex);
}
