import type { LapData } from "./types";
import { parseVehicleGeometry, type VehicleGeometry } from "./vehicleGeometry";

/**
 * Display-only world translation fitted to track limits. A session-dependent
 * GNSS bias is a plausible major contributor; this fit does not independently
 * establish the true position or the absolute accuracy of the map.
 */
export interface GpsRegistrationFile {
  version: number;
  kind: "lap-translation";
  raceId: string;
  trackId: string;
  method?: {
    /** Legacy common tyre span; newer files carry each axle separately. */
    vehicleWidthMeters?: number;
    vehicleGeometry?: VehicleGeometry;
    surface?: string;
    /** MEASURED antenna position from the vehicle centre; absent/zero = unknown. */
    antennaOffsetMeters?: { right: number; forward: number };
  };
  limits?: { source: string; sha256: string };
  referenceTrack?: { source: string; sha256: string };
  sessionOffsetMeters?: [number, number];
  laps: {
    vehicleId: string;
    lap: number;
    /** Viewer local XZ metres: +x east, +z south. */
    offsetMeters: [number, number];
    jackknifeSigmaMeters?: [number, number];
    dataSha256?: string;
  }[];
}

/** Per-lap registration stamped on the lap object by the loader. */
export interface LapRegistration {
  offsetMeters: [number, number];
  sigmaMeters: [number, number] | null;
  /** Legacy width; retained for older alignment studies. */
  vehicleWidthMeters: number | null;
  /** Independent body and axle dimensions, shared by raw/registered display. */
  vehicleGeometry?: VehicleGeometry | null;
  /** Body-frame antenna position the fit assumed (rotates with the heading). */
  antennaMeters: { right: number; forward: number } | null;
  /** false if any recorded input hash mismatches; null if any required check
   * is unavailable (including WebCrypto); true only when all recorded inputs match. */
  verified: boolean | null;
  inputVerification?: { lap: boolean | null; limits: boolean | null; referenceTrack: boolean | null };
  enabled: boolean;
}

export const MAX_REGISTRATION_OFFSET_METERS = 8;
export const MAX_ANTENNA_OFFSET_METERS = 3;

const pair = (v: unknown): v is [number, number] =>
  Array.isArray(v) && v.length === 2 && v.every((n) => typeof n === "number" && Number.isFinite(n));
const sha = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);

/** Only same-track local assets can be requested, never a remote/traversal URL. */
export function registrationLimitsUrl(file: Pick<GpsRegistrationFile, "trackId" | "limits">): string | null {
  const source = file.limits?.source;
  const prefix = `public/data/tracks/${file.trackId}/`;
  if (typeof source !== "string" || !/^[a-z0-9_-]+$/i.test(file.trackId) ||
    !source.startsWith(prefix)) return null;
  const tail = source.slice(prefix.length);
  if (!tail.endsWith(".json") || !tail.split("/").every((part) =>
    /^[a-z0-9_.-]+$/i.test(part) && part !== "." && part !== "..")) return null;
  return `/${source.slice("public/".length)}`;
}

export function parseGpsRegistration(value: unknown, raceId: string, trackId: string): GpsRegistrationFile | null {
  const file = value as Partial<GpsRegistrationFile> | null;
  if (!file || file.kind !== "lap-translation" || file.raceId !== raceId || file.trackId !== trackId ||
    !Array.isArray(file.laps)) return null;
  const antenna = file.method?.antennaOffsetMeters;
  if (antenna !== undefined && !(pair([antenna?.right, antenna?.forward]) &&
    Math.hypot(antenna.right, antenna.forward) <= MAX_ANTENNA_OFFSET_METERS)) return null;
  if (file.method?.vehicleGeometry !== undefined && !parseVehicleGeometry(file.method.vehicleGeometry)) return null;
  if (file.limits !== undefined && (!sha(file.limits?.sha256) ||
    !registrationLimitsUrl(file as GpsRegistrationFile))) return null;
  if (file.referenceTrack !== undefined && (!sha(file.referenceTrack?.sha256) ||
    file.referenceTrack.source !== `public/data/tracks/${trackId}/track.json`)) return null;
  const valid = file.laps.every((l) => typeof l?.vehicleId === "string" && Number.isInteger(l.lap) &&
    pair(l.offsetMeters) && Math.hypot(...l.offsetMeters) <= MAX_REGISTRATION_OFFSET_METERS &&
    (l.jackknifeSigmaMeters === undefined || pair(l.jackknifeSigmaMeters)) &&
    (l.dataSha256 === undefined || sha(l.dataSha256)));
  return valid ? (file as GpsRegistrationFile) : null;
}

/** Hashes cover the served lap and traced road input. Any mismatch disables the
 * translation; unavailable checks stay explicitly unknown. Files predating
 * road provenance retain their lap-only verification contract. */
export function withRegistration(lap: LapData, file: GpsRegistrationFile | null, enabled: boolean,
  dataSha256: string | null = null, limitsSha256: string | null = null,
  trackSha256: string | null = null): LapData {
  const entry = file && file.raceId === lap.meta.race_id
    ? file.laps.find((l) => l.vehicleId === lap.meta.vehicle_id && l.lap === lap.meta.lap) : undefined;
  if (!entry) return lap;
  const width = file?.method?.vehicleWidthMeters;
  const geometry = parseVehicleGeometry(file?.method?.vehicleGeometry);
  const antenna = file?.method?.antennaOffsetMeters;
  const lapVerified = entry.dataSha256 && dataSha256 ? entry.dataSha256 === dataSha256 : null;
  const limitsVerified = file?.limits?.sha256 && limitsSha256 ? file.limits.sha256 === limitsSha256 : null;
  const trackVerified = file?.referenceTrack?.sha256 && trackSha256
    ? file.referenceTrack.sha256 === trackSha256 : null;
  const checks = [lapVerified, ...(file?.limits ? [limitsVerified] : []),
    ...(file?.referenceTrack ? [trackVerified] : [])];
  const verified = checks.includes(false) ? false : checks.every((c) => c === true) ? true : null;
  if (verified === false) {
    console.warn(`gps_registration.json: input hash mismatch for lap ${lap.meta.lap} of ${lap.meta.race_id} ` +
      `(lap=${lapVerified}, limits=${limitsVerified}, referenceTrack=${trackVerified}); showing raw GPS. Re-run pipeline/register_gps_to_track.py.`);
  }
  return {
    ...lap,
    registration: {
      offsetMeters: [entry.offsetMeters[0], entry.offsetMeters[1]],
      sigmaMeters: entry.jackknifeSigmaMeters ? [...entry.jackknifeSigmaMeters] : null,
      vehicleWidthMeters: geometry?.bodyWidthMeters ??
        (typeof width === "number" && Number.isFinite(width) && width > 0 ? width : null),
      vehicleGeometry: geometry,
      antennaMeters: antenna && (antenna.right !== 0 || antenna.forward !== 0)
        ? { right: antenna.right, forward: antenna.forward } : null,
      verified,
      inputVerification: { lap: lapVerified, limits: limitsVerified, referenceTrack: trackVerified },
      enabled: verified !== false && enabled,
    },
  };
}

export function setRegistrationEnabled(lap: LapData, enabled: boolean): LapData {
  const r = lap.registration;
  return !r || r.enabled === enabled || (enabled && r.verified === false)
    ? lap : { ...lap, registration: { ...r, enabled } };
}

export function registrationOffset(lap: LapData): { x: number; z: number } {
  const r = lap.registration;
  return r?.enabled ? { x: r.offsetMeters[0], z: r.offsetMeters[1] } : { x: 0, z: 0 };
}

export function describeOffset(offset: [number, number]): string {
  const part = (v: number, positive: string, negative: string) =>
    `${v >= 0 ? positive : negative}${Math.abs(v).toFixed(2)}m`;
  return `${part(offset[0], "東", "西")}・${part(offset[1], "南", "北")}`;
}
