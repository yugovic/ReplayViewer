import type { LapData, LapIndexRecord, LapsIndex, LoadedReplay, TrackData } from "./types";
import { parseSatVariantParam, probeSatelliteVariantsForTrack, type SatVariantId } from "./satelliteVariants";

const DEFAULT_TRACK_ID = "barber";
const DEFAULT_RACE_ID = "barber_r1";

/** Only these characters are allowed in a track/race id (they end up in fetch URLs). */
const ID_PATTERN = /^[a-z0-9_-]+$/i;

/** A selectable track/race data set that ships under `public/data`. */
export interface TrackCatalogEntry {
  trackId: string;
  raceId: string;
  /** Human-readable track name shown in the first-screen picker. */
  trackName: string;
  /** Short session/round label shown under the track name. */
  raceLabel: string;
}

/**
 * Static catalog of the track/race data sets bundled in `public/data`. The
 * first-screen picker lists these; choosing a different entry navigates to
 * `?track=<trackId>&race=<raceId>`, which reloads the page so `SOURCES` (and
 * every trackId-keyed asset URL in TrackBuilder) is re-resolved from scratch —
 * far simpler and safer than swapping the whole scene in place. Keep this in
 * sync with the directories under `public/data/tracks/*` and
 * `public/data/races/*`; the first entry should mirror the DEFAULT_* ids above.
 */
export const TRACK_CATALOG: TrackCatalogEntry[] = [
  {
    trackId: "barber",
    raceId: "barber_r1",
    trackName: "Barber Motorsports Park",
    raceLabel: "Race 1 · GR86",
  },
  {
    trackId: "fuji",
    raceId: "fuji_aim_01",
    trackName: "Fuji Speedway",
    raceLabel: "AiM · Demio",
  },
];

/** @deprecated kept as an alias for SatVariantId for any external imports of the old name. */
export type SatVariant = SatVariantId;

export interface ResolvedDataSources {
  trackId: string;
  raceId: string;
  trackUrl: string;
  raceBaseUrl: string;
  satVariant: SatVariantId;
}

function sanitizeId(value: string | null, fallback: string): string {
  if (value && ID_PATTERN.test(value)) {
    return value;
  }
  return fallback;
}

/**
 * Resolves track/race/satellite-variant selection from a URL query string
 * (e.g. `location.search`). Pure and DOM-free so it is unit-testable.
 * Unknown or malformed values fall back to the defaults that reproduce
 * today's hardcoded Barber round-1 behavior.
 *
 * `sat` accepts `sr | bing | bing_sr` as the requested INITIAL variant; any
 * other value (including no param) resolves to "default" (satellite.jpg).
 * This only resolves the *requested* variant from the URL — whether that
 * file actually exists is checked later by loadInitialReplay(), which falls
 * back to "default" silently if it's unavailable.
 */
export function resolveDataSources(search: string): ResolvedDataSources {
  const params = new URLSearchParams(search);
  const trackId = sanitizeId(params.get("track"), DEFAULT_TRACK_ID);
  const raceId = sanitizeId(params.get("race"), DEFAULT_RACE_ID);
  const satVariant = parseSatVariantParam(params.get("sat"));

  return {
    trackId,
    raceId,
    trackUrl: `/data/tracks/${trackId}/track.json`,
    raceBaseUrl: `/data/races/${raceId}`,
    satVariant,
  };
}

// Resolved once from the page URL (e.g. ?track=...&race=...&sat=sr). Defaults
// reproduce the previously hardcoded Barber round-1 behavior exactly.
const SOURCES = resolveDataSources(typeof window !== "undefined" ? window.location.search : "");

/** The track/race ids actually in use for the current page load. */
export function getCurrentSelection(): { trackId: string; raceId: string } {
  return { trackId: SOURCES.trackId, raceId: SOURCES.raceId };
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status}`);
  }
  return (await response.json()) as T;
}

function pickInitialLap(index: LapsIndex): LapIndexRecord {
  const generated = index.laps.filter((lap) => lap.data_file);
  if (generated.length === 0) {
    throw new Error("laps.json has no generated lap data files");
  }
  return [...generated].sort((a, b) => a.lap_time_seconds - b.lap_time_seconds)[0];
}

export async function loadInitialReplay(): Promise<LoadedReplay> {
  const trackDir = `/data/tracks/${SOURCES.trackId}/`;
  const [track, lapsIndex, availableSatelliteVariants] = await Promise.all([
    fetchJson<TrackData>(SOURCES.trackUrl),
    fetchJson<LapsIndex>(`${SOURCES.raceBaseUrl}/laps.json`),
    // Probed up front (in parallel with the fetches above) so the initial
    // variant choice below never asks TrackBuilder to load a file that
    // doesn't exist — that's what keeps ?sat=bing_sr silent when the file is
    // absent, instead of surfacing a console warning from a failed texture load.
    probeSatelliteVariantsForTrack(trackDir),
  ]);
  // Stamp the resolved selection onto the track so downstream consumers
  // (TrackBuilder's satellite/terrain/features asset URLs) key off the id
  // actually used to fetch this data, not whatever track.json happens to
  // contain internally.
  track.trackId = SOURCES.trackId;
  track.satVariant = availableSatelliteVariants.includes(SOURCES.satVariant)
    ? SOURCES.satVariant
    : "default";
  const activeRecord = pickInitialLap(lapsIndex);
  const lap = await fetchJson<LapData>(`${SOURCES.raceBaseUrl}/${activeRecord.data_file}`);
  return { track, lapsIndex, lap, activeRecord, availableSatelliteVariants };
}

/**
 * Loads a lap JSON file by its data_file path.
 */
export async function loadLapFile(dataFile: string): Promise<LapData> {
  return fetchJson<LapData>(`${SOURCES.raceBaseUrl}/${dataFile}`);
}

/**
 * Returns laps from the index that have data files available.
 * Prefer the `selected` array (pre-generated), fall back to laps[] with data_file set.
 */
export function getAvailableLaps(index: LapsIndex): LapIndexRecord[] {
  // Build a set of (vehicle_id, lap) pairs from the selected array
  const selectedKeys = new Set(
    index.selected.map((s) => `${s.vehicle_id}::${s.lap}`),
  );

  return index.laps.filter((rec) => {
    const key = `${rec.vehicle_id}::${rec.lap}`;
    return selectedKeys.has(key) && rec.data_file;
  });
}
