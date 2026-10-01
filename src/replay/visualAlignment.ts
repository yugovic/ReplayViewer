import type { LapData, TrackData } from './types';
import { latLngToLocal } from './projection';
import { registrationOffset } from './gpsRegistration';
import type { VehicleGeometry } from './vehicleGeometry';

interface AlignmentScope {
  raceId: string;
  vehicleId: string;
  lap: number;
  status: 'visual-fit';
}
interface TranslationWindow {
  offsetMeters: [number, number];
  windowSeconds: [number, number, number, number];
}
interface LegacyAlignment extends AlignmentScope, TranslationWindow {
  kind?: 'constant-window';
}
export interface LocalReplayAlignment extends AlignmentScope {
  kind: 'local-windows';
  /** Raw comparison keeps the same vehicle dimensions. */
  enabled?: boolean;
  widthMeters: number;
  segments: (TranslationWindow & { id: string })[];
}
export type ReplayAlignment = LegacyAlignment | LocalReplayAlignment;

export function alignmentMatches(lap: LapData, track: TrackData): boolean {
  const a = track.replayAlignment;
  return !!a && track.visualProfile === 'cg' && track.trackId === 'fuji' &&
    a.raceId === lap.meta.race_id && a.vehicleId === lap.meta.vehicle_id && a.lap === lap.meta.lap;
}

function validWindow(w: TranslationWindow): boolean {
  const [start, fullStart, fullEnd, end] = w.windowSeconds;
  return [...w.windowSeconds, ...w.offsetMeters].every(Number.isFinite) &&
    start < fullStart && fullStart <= fullEnd && fullEnd < end;
}

/** Display-only registration: each reviewed core is a rigid translation.
 * Quintic fades have zero first and second derivatives at their endpoints;
 * disjoint windows protect the unreviewed portions of the recorded line. */
export function visualPositionOffset(lap: LapData, track: TrackData, time: number) {
  const a = track.replayAlignment;
  if (!a || !alignmentMatches(lap, track) || !Number.isFinite(time)) return { x: 0, z: 0 };
  const local = a.kind === 'local-windows';
  if (local && a.enabled === false) return { x: 0, z: 0 };
  const windows = local ? a.segments : [a];
  if (windows.some((w, i) => !validWindow(w) ||
    (i > 0 && w.windowSeconds[0] < windows[i - 1].windowSeconds[3]))) return { x: 0, z: 0 };
  for (const w of windows) {
    const [start, fullStart, fullEnd, end] = w.windowSeconds;
    if (time <= start || time >= end) continue;
    const fade = (t: number) => {
      const u = Math.max(0, Math.min(1, t));
      return local ? u * u * u * (10 + u * (-15 + 6 * u)) : u * u * (3 - 2 * u);
    };
    const weight = fade((time - start) / (fullStart - start)) * fade((end - time) / (end - fullEnd));
    return { x: w.offsetMeters[0] * weight, z: w.offsetMeters[1] * weight };
  }
  return { x: 0, z: 0 };
}

/** An old alignment study keeps its historical dimensions. Registration geometry
 * is attached only to matching vehicle/laps and is identical in raw mode. */
export function registeredVehicleGeometry(lap: LapData, track: TrackData): VehicleGeometry | null {
  return alignmentMatches(lap, track) ? null : lap.registration?.vehicleGeometry ?? null;
}

export function registeredVehicleWidth(lap: LapData, track: TrackData): number | null {
  const a = track.replayAlignment;
  if (alignmentMatches(lap, track) && a?.kind === 'local-windows' &&
    Number.isFinite(a.widthMeters) && a.widthMeters > 0) return a.widthMeters;
  // Same width with the registration off, so raw/registered compare like for like.
  return lap.registration?.vehicleWidthMeters ?? null;
}

/** Same ±6-sample chord as the pose window (= the fit's ±0.6 s at 10 Hz). */
const ANTENNA_HEADING_WINDOW = 6;

/** Antenna → vehicle centre. A body-frame vector, so unlike the world-space
 * registration it turns with the car; the heading comes from the raw line. */
function antennaToCentre(lap: LapData, track: TrackData, index: number) {
  const a = lap.registration?.enabled ? lap.registration.antennaMeters : null;
  if (!a) return { x: 0, z: 0 };
  const s = Math.max(0, index - ANTENNA_HEADING_WINDOW);
  const e = Math.min(lap.t.length - 1, index + ANTENNA_HEADING_WINDOW);
  const p0 = latLngToLocal(lap.lat[s] ?? 0, lap.lng[s] ?? 0, track.origin);
  const p1 = latLngToLocal(lap.lat[e] ?? 0, lap.lng[e] ?? 0, track.origin);
  const dx = p1.x - p0.x, dz = p1.z - p0.z, length = Math.hypot(dx, dz);
  if (length < 0.001) return { x: 0, z: 0 }; // stationary: no heading to turn with
  const fx = dx / length, fz = dz / length; // forward; the driver's right is (-fz, fx)
  return { x: -(a.right * -fz + a.forward * fx), z: -(a.right * fx + a.forward * fz) };
}

/** The single place a recorded sample becomes a displayed position: the
 * whole-lap track-limit registration, then the legacy per-window study. The
 * loader never sets both (the `alignment=` studies are defined against raw). */
export function displayedLapPoint(lap: LapData, track: TrackData, index: number) {
  const p = latLngToLocal(lap.lat[index] ?? 0, lap.lng[index] ?? 0, track.origin);
  const registration = registrationOffset(lap);
  const antenna = antennaToCentre(lap, track, index);
  const offset = visualPositionOffset(lap, track, lap.t[index]);
  return { x: p.x + antenna.x + registration.x + offset.x, z: p.z + antenna.z + registration.z + offset.z };
}
