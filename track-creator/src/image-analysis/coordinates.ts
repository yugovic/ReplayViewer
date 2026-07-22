/**
 * Coordinate conversions between local ENU (metres), lat/lng, and image pixels.
 * Conventions match the rest of the repo:
 *   x = east, z = -north (south positive), Y up.
 *   lat = origin.lat - z / 111320
 *   lng = origin.lng + x / (111320 * cos(origin.lat))
 */

const DEG = Math.PI / 180;
const EARTH_METRES_PER_DEG = 111320;

export interface Origin {
  lat: number;
  lng: number;
}

/** Local ENU (x, z) to geographic (lat, lng). */
export function localToLatLng(o: Origin, x: number, z: number): { lat: number; lng: number } {
  return {
    lat: o.lat - z / EARTH_METRES_PER_DEG,
    lng: o.lng + x / (EARTH_METRES_PER_DEG * Math.cos(o.lat * DEG)),
  };
}

/** Geographic (lat, lng) to local ENU (x, z). */
export function latLngToLocal(o: Origin, lat: number, lng: number): { x: number; z: number } {
  return {
    x: (lng - o.lng) * EARTH_METRES_PER_DEG * Math.cos(o.lat * DEG),
    z: -((lat - o.lat) * EARTH_METRES_PER_DEG),
  };
}

// ── Web Mercator normalised tile coordinates ──────────────────────────────────

/** Normalised Mercator X (0..1) for a longitude. */
export function mercX(lng: number): number {
  return (lng + 180) / 360;
}

/** Normalised Mercator Y (0..1) for a latitude. */
export function mercY(lat: number): number {
  const latRad = lat * DEG;
  return (1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2;
}

/**
 * Convert lat/lng to pixel coordinates within a georeferenced image with a
 * Mercator bbox. Returns null if the point falls outside the image.
 */
export function latLngToPixelMercator(
  lat: number,
  lng: number,
  bbox: { minLng: number; maxLng: number; minLat: number; maxLat: number },
  imageWidth: number,
  imageHeight: number,
): { px: number; py: number } | null {
  const mxMin = mercX(bbox.minLng);
  const mxMax = mercX(bbox.maxLng);
  const myMin = mercY(bbox.maxLat); // top of image = max latitude
  const myMax = mercY(bbox.minLat); // bottom = min latitude

  const mx = mercX(lng);
  const my = mercY(lat);

  if (mx < mxMin || mx > mxMax || my < myMin || my > myMax) return null;

  const u = (mx - mxMin) / (mxMax - mxMin);
  const v = (my - myMin) / (myMax - myMin);

  return {
    px: u * (imageWidth - 1),
    py: v * (imageHeight - 1),
  };
}

/**
 * Convert lat/lng to pixel coordinates within a georeferenced image with a
 * simple equirectangular bbox (non-Mercator). Returns null if outside.
 */
export function latLngToPixelEquirect(
  lat: number,
  lng: number,
  bbox: { minLng: number; maxLng: number; minLat: number; maxLat: number },
  imageWidth: number,
  imageHeight: number,
): { px: number; py: number } | null {
  if (
    lng < bbox.minLng || lng > bbox.maxLng ||
    lat < bbox.minLat || lat > bbox.maxLat
  )
    return null;

  const u = (lng - bbox.minLng) / (bbox.maxLng - bbox.minLng);
  const v = (bbox.maxLat - lat) / (bbox.maxLat - bbox.minLat);

  return {
    px: u * (imageWidth - 1),
    py: v * (imageHeight - 1),
  };
}

/**
 * Convert local ENU to pixel coordinates using a GeoImageMeta and an origin.
 * Dispatches to Mercator or equirectangular based on the meta flag.
 */
export function localToPixel(
  x: number,
  z: number,
  origin: Origin,
  meta: GeoImageMetaLike,
): { px: number; py: number } | null {
  const { lat, lng } = localToLatLng(origin, x, z);
  return meta.mercator
    ? latLngToPixelMercator(lat, lng, meta.bbox, meta.imageWidth, meta.imageHeight)
    : latLngToPixelEquirect(lat, lng, meta.bbox, meta.imageWidth, meta.imageHeight);
}

/** Subset of GeoImageMeta needed for coordinate conversion. */
export interface GeoImageMetaLike {
  bbox: { minLng: number; maxLng: number; minLat: number; maxLat: number };
  imageWidth: number;
  imageHeight: number;
  mercator: boolean;
}

// ── centerline geometry helpers ───────────────────────────────────────────────

/**
 * Right normal of a centerline tangent (tx, tz).
 * Convention: n = (-tz, tx), same as road.ts.
 * +normal = driver's right.
 */
export function rightNormal(tx: number, tz: number): { nx: number; nz: number } {
  return { nx: -tz, nz: tx };
}

/**
 * Sample a point at a lateral offset from a centerline station.
 * offset > 0 → right side, offset < 0 → left side.
 */
export function offsetPoint(
  cx: number,
  cz: number,
  tx: number,
  tz: number,
  offset: number,
): { x: number; z: number } {
  const { nx, nz } = rightNormal(tx, tz);
  return { x: cx + nx * offset, z: cz + nz * offset };
}

// ── centerline fingerprint ────────────────────────────────────────────────────

/**
 * Cheap hash of control points to detect centerline changes between analysis
 * and application. Not cryptographic — just a change detector.
 */
export function centerlineFingerprint(points: { x: number; z: number }[]): string {
  let h = 0;
  for (const p of points) {
    h = Math.imul(h, 31) + Math.round(p.x * 1000) | 0;
    h = Math.imul(h, 31) + Math.round(p.z * 1000) | 0;
  }
  return (h >>> 0).toString(16);
}
