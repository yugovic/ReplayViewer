/**
 * True lap clock.
 *
 * `lap.t` counts seconds from the FIRST GPS sample of the lap (t[0] = 0 by the
 * pipeline contract), and `lap.dist` counts metres from that same sample. The
 * timing line was crossed δ = `meta.first_sample_after_lap_start_seconds`
 * earlier (7–97 ms for the Fuji AiM laps), so the true lap time of a sample is
 * t + δ and its distance from the line is dist + s0, where s0 ≈ δ · v0 is the
 * distance covered before the first sample.
 *
 * The data arrays and the playback clock (store.currentTime) stay in the
 * recorded t basis; these helpers convert only for display and for lap-to-lap
 * alignment. A missing or implausible δ is treated as 0 (older data, previews).
 */
import type { LapData, LapMeta } from "./types";

/** Optional field written by pipeline/build_race.py (not in older lap files). */
interface LapStartMeta {
  first_sample_after_lap_start_seconds?: unknown;
}

/**
 * δ is by definition within one sample interval of the lap start; anything
 * negative or longer than this is treated as corrupt and ignored.
 */
export const MAX_LAP_START_OFFSET_SECONDS = 1;

/** δ: seconds from the timing-line crossing to the first sample (≥ 0). */
export function lapStartOffsetSeconds(meta: LapMeta | null | undefined): number {
  const value = (meta as (LapMeta & LapStartMeta) | null | undefined)?.first_sample_after_lap_start_seconds;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= MAX_LAP_START_OFFSET_SECONDS
    ? value
    : 0;
}

/**
 * s0: metres covered between the timing line and the first sample, estimated
 * as δ × the speed of the first recorded segment (same GPS distance scale as
 * `lap.dist`). Falls back to the speed channel (km/h) for one-sample laps.
 */
export function lapStartOffsetMeters(lap: Pick<LapData, "meta" | "t" | "dist" | "speed">): number {
  const delta = lapStartOffsetSeconds(lap.meta);
  if (!(delta > 0)) return 0;
  let speedMps = Number.NaN;
  const { t, dist } = lap;
  if (t.length >= 2 && dist.length >= 2) {
    const dt = t[1] - t[0];
    if (dt > 0) speedMps = (dist[1] - dist[0]) / dt;
  }
  if (!(Number.isFinite(speedMps) && speedMps > 0)) {
    const v0 = lap.speed?.[0];
    speedMps = typeof v0 === "number" ? v0 / 3.6 : Number.NaN;
  }
  return Number.isFinite(speedMps) && speedMps > 0 ? speedMps * delta : 0;
}

/** Recorded playback time (t basis) → true lap time (seconds since the line). */
export function trueLapTime(time: number, meta: LapMeta | null | undefined): number {
  return time + lapStartOffsetSeconds(meta);
}

/** HUD lap clock: MM:SS.mmm (rounded to the millisecond before splitting). */
export function formatLapClock(seconds: number): string {
  const totalMs = Math.max(0, Math.round((Number.isFinite(seconds) ? seconds : 0) * 1000));
  const minutes = Math.floor(totalMs / 60000);
  const remMs = totalMs - minutes * 60000;
  const remStr = (remMs / 1000).toFixed(3).padStart(6, "0");
  return `${String(minutes).padStart(2, "0")}:${remStr}`;
}

/** Transport clock: MM:SS (whole seconds, truncated). */
export function formatTransportClock(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const minutes = Math.floor(safe / 60);
  const rem = Math.floor(safe - minutes * 60);
  return `${String(minutes).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}
