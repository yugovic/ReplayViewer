import type { ProjectionOrigin } from "./types";

const METERS_PER_DEGREE = 111_320;

/**
 * Local scene-space convention (ENU, Y-up, right-handed — matches three.js):
 *   east  → +X
 *   north → −Z
 *   up    → +Y
 *
 * This is the only assignment under which the scene's handedness matches
 * reality: East × North = Up, and in the scene X × (−Z) = +Y. Mapping north
 * to +Z instead (as an earlier version of this function did) pairs a
 * right-handed physical ENU frame with a left-handed placement inside
 * three.js's right-handed XYZ space — a parity flip that mirrors the whole
 * world (every real right-hander renders as a left-hander, and vice versa).
 */
export function latLngToLocal(lat: number, lng: number, origin: ProjectionOrigin) {
  const lngScale = METERS_PER_DEGREE * Math.cos((origin.lat * Math.PI) / 180);
  return {
    x: (lng - origin.lng) * lngScale,
    z: -(lat - origin.lat) * METERS_PER_DEGREE,
  };
}

/** Exact inverse of latLngToLocal. */
export function localToLatLng(x: number, z: number, origin: ProjectionOrigin) {
  const lngScale = METERS_PER_DEGREE * Math.cos((origin.lat * Math.PI) / 180);
  return {
    lat: origin.lat - z / METERS_PER_DEGREE,
    lng: origin.lng + x / lngScale,
  };
}
