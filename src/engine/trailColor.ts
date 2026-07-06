/**
 * Trail color helpers – pure functions used by the data trail mesh and tested by vitest.
 *
 * Color encoding:
 *   brake > 5 %   → red    (0xff2200)
 *   brake 0–5 %   → speed-based gradient:
 *                     slow (0 km/h)  → orange  (0xff6600)
 *                     fast (200 km/h)→ cyan    (0x00eeff)
 */

export interface TrailColorInput {
  /** Current speed in km/h */
  speedKmh: number;
  /** Brake pedal 0–100 (%) */
  brake: number;
  /** Max expected speed for normalising the colour gradient (default 200 km/h) */
  maxSpeedKmh?: number;
}

export interface RgbColor {
  r: number; // 0–1
  g: number; // 0–1
  b: number; // 0–1
}

/** Linear interpolation between two values */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Clamp a value to [min, max] */
export function clampUnit(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/**
 * Returns an RGB colour (channels 0–1) for the data trail based on speed and brake.
 * Fast path: if brake > 5 %, colour blends toward red proportionally.
 */
export function trailColor(input: TrailColorInput): RgbColor {
  const { speedKmh, brake, maxSpeedKmh = 200 } = input;
  const brakeNorm = clampUnit(brake / 100);
  const speedNorm = clampUnit(speedKmh / maxSpeedKmh);

  // Speed gradient: orange → cyan
  const speedR = lerp(1.0, 0.0, speedNorm);
  const speedG = lerp(0.4, 0.93, speedNorm);
  const speedB = lerp(0.0, 1.0, speedNorm);

  // Brake colour: red
  const brakeR = 1.0;
  const brakeG = 0.13;
  const brakeB = 0.0;

  // Threshold-weighted blend: above 5% brake the brake colour dominates
  const brakeBias = brakeNorm > 0.05 ? clampUnit((brakeNorm - 0.05) / 0.95) : 0;

  return {
    r: lerp(speedR, brakeR, brakeBias),
    g: lerp(speedG, brakeG, brakeBias),
    b: lerp(speedB, brakeB, brakeBias),
  };
}

/** Pack an RgbColor to a 24-bit integer (0xRRGGBB) */
export function rgbToHex(c: RgbColor): number {
  const r = Math.round(clampUnit(c.r) * 255);
  const g = Math.round(clampUnit(c.g) * 255);
  const b = Math.round(clampUnit(c.b) * 255);
  return (r << 16) | (g << 8) | b;
}

/**
 * Select the best TV camera index from a list of camera positions for a given car position.
 * Returns the index whose camera is within [minDist, maxDist] of the car, and has the
 * greatest dot-product between the camera-to-car vector and camera forward direction.
 *
 * @param carX       Car world X
 * @param carZ       Car world Z
 * @param cameras    Array of {x, z, fwdX, fwdZ} camera descriptors
 * @param minDist    Minimum distance for a camera to be considered (default 20 m)
 * @param maxDist    Maximum distance for a camera to be considered (default 220 m)
 */
export interface TvCamera {
  x: number;
  z: number;
  /** Forward-vector X of the camera (normalised) */
  fwdX: number;
  /** Forward-vector Z of the camera (normalised) */
  fwdZ: number;
}

export function selectTvCamera(
  carX: number,
  carZ: number,
  cameras: TvCamera[],
  minDist = 20,
  maxDist = 220,
): number {
  let bestIndex = 0;
  let bestScore = -Infinity;

  for (let i = 0; i < cameras.length; i++) {
    const cam = cameras[i];
    const dx = carX - cam.x;
    const dz = carZ - cam.z;
    const dist = Math.hypot(dx, dz);
    if (dist < minDist || dist > maxDist) {
      continue;
    }
    // Normalised direction from camera toward car
    const nx = dx / dist;
    const nz = dz / dist;
    // Dot product with camera forward: higher = car is more "in frame"
    const dot = nx * cam.fwdX + nz * cam.fwdZ;
    // Weight by closeness (prefer mid-range cameras over very-far ones)
    const distScore = 1 - Math.abs(dist - (minDist + maxDist) / 2) / ((maxDist - minDist) / 2);
    const score = dot * 0.7 + distScore * 0.3;
    if (score > bestScore) {
      bestScore = score;
      bestIndex = i;
    }
  }
  return bestIndex;
}
