import type { LapData, LapIndexRecord, LapsIndex, LoadedReplay, TrackData } from "./types";
import { resolveShowcase } from "./showcase";
import { parseSatVariantParam, probeSatelliteVariantsForTrack, type SatVariantId } from "./satelliteVariants";
import { parseGpsRegistration, registrationLimitsUrl, withRegistration, type GpsRegistrationFile } from "./gpsRegistration";

const DEFAULT_TRACK_ID = "barber";
const DEFAULT_RACE_ID = "barber_r1";

/** Curated imagery shown by the user-facing viewer without variant probing. */
const USER_SATELLITE_VARIANT_BY_TRACK: Readonly<Partial<Record<string, SatVariantId>>> = {
  fuji: "shizuoka_x2",
};

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
    raceLabel: "AiM · Demio (7/29)",
  },
  {
    trackId: "fuji",
    raceId: "fuji_aim_2020_07_30",
    trackName: "Fuji Speedway",
    raceLabel: "AiM · Demio (7/30)",
  },
  {
    trackId: "suzuka",
    raceId: "suzuka_preview",
    trackName: "Suzuka Circuit",
    raceLabel: "コース確認 · 合成走行",
  },
  {
    trackId: "okayama",
    raceId: "okayama_preview",
    trackName: "Okayama International Circuit",
    raceLabel: "コース確認 · 合成走行",
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
  developerMode: boolean;
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
 * `?dev=1` enables the developer-only texture and layer tooling. The `sat`
 * parameter is intentionally ignored outside that mode so the user-facing
 * viewer always loads the single curated texture for the selected track.
 */
export function resolveDataSources(search: string): ResolvedDataSources {
  const params = new URLSearchParams(search);
  const trackId = sanitizeId(params.get("track"), DEFAULT_TRACK_ID);
  const raceId = sanitizeId(params.get("race"), DEFAULT_RACE_ID);
  const developerMode = params.get("dev") === "1";
  const satVariant = developerMode
    ? parseSatVariantParam(params.get("sat"))
    : (USER_SATELLITE_VARIANT_BY_TRACK[trackId] ?? "default");

  return {
    trackId,
    raceId,
    trackUrl: `/data/tracks/${trackId}/track.json`,
    raceBaseUrl: `/data/races/${raceId}`,
    satVariant,
    developerMode,
  };
}

// Resolved once from the page URL (e.g. ?track=...&race=...&sat=sr). Defaults
// reproduce the previously hardcoded Barber round-1 behavior exactly.
const SOURCES = resolveDataSources(typeof window !== "undefined" ? window.location.search : "");

/** The track/race ids actually in use for the current page load. */
export function getCurrentSelection(): { trackId: string; raceId: string } {
  return { trackId: SOURCES.trackId, raceId: SOURCES.raceId };
}

/** Whether this page load explicitly opted into the developer controls. */
export function isDeveloperMode(): boolean {
  return SOURCES.developerMode;
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Optional asset: a missing file is a 404 on static hosting but a 200 with
 * index.html under Vite's SPA fallback, so a JSON parse failure also means
 * "absent". */
async function fetchOptionalJson(url: string): Promise<unknown> {
  try {
    const response = await fetch(url);
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** SHA-256 of the served bytes, so a lap regenerated after its registration
 * was fitted is detected. Null where WebCrypto is unavailable (plain http on a
 * non-localhost host): the check is then skipped, not failed. */
async function sha256Hex(text: string): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

async function fetchHashedJson<T>(url: string): Promise<{ data: T; sha256: string | null }> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status}`);
  }
  const text = await response.text();
  return { data: JSON.parse(text) as T, sha256: await sha256Hex(text) };
}

// Race-level GPS registration, fetched once with the initial replay and then
// stamped on every lap this page loads (main and ghost).
let gpsRegistration: GpsRegistrationFile | null = null;
let gpsLimitsSha256: string | null = null;
let gpsTrackSha256: string | null = null;

async function fetchRegistrationLimitsHash(file: GpsRegistrationFile | null): Promise<string | null> {
  const url = file && registrationLimitsUrl(file);
  if (!url) return null;
  try {
    const response = await fetch(url);
    return response.ok ? await sha256Hex(await response.text()) : null;
  } catch {
    return null;
  }
}

/** `?gps=raw` starts with the registration switched off (it stays toggleable). */
export function gpsRegistrationInitiallyEnabled(search: string): boolean {
  return new URLSearchParams(search).get("gps") !== "raw";
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
  // Availability probing is development tooling. In the user-facing viewer,
  // resolve immediately with the one texture it is allowed to display; this
  // removes the six large-file HEAD requests from the initial critical path.
  const availableVariantsPromise = SOURCES.developerMode
    ? probeSatelliteVariantsForTrack(trackDir)
    : Promise.resolve<SatVariantId[]>([SOURCES.satVariant]);
  const search = typeof window !== "undefined" ? window.location.search : "";
  const alignment = new URLSearchParams(search).get("alignment");
  const [fetchedTrack, lapsIndex, availableSatelliteVariants, registration] = await Promise.all([
    fetchHashedJson<TrackData>(SOURCES.trackUrl),
    fetchJson<LapsIndex>(`${SOURCES.raceBaseUrl}/laps.json`),
    availableVariantsPromise,
    // The dated `alignment=` studies are defined against raw GPS; keep them so.
    alignment ? Promise.resolve(null) : fetchOptionalJson(`${SOURCES.raceBaseUrl}/gps_registration.json`),
  ]);
  const track = fetchedTrack.data;
  gpsTrackSha256 = fetchedTrack.sha256;
  gpsRegistration = parseGpsRegistration(registration, SOURCES.raceId, SOURCES.trackId);
  // Stamp the resolved selection onto the track so downstream consumers
  // (TrackBuilder's satellite/terrain/features asset URLs) key off the id
  // actually used to fetch this data, not whatever track.json happens to
  // contain internally.
  track.trackId = SOURCES.trackId;
  if (typeof window !== "undefined") {
    const look = resolveShowcase(window.location.search);
    if (look === "reference" || look === "cg") track.visualProfile = look;
    // The full-lap CG trial is also available without the excerpt window.
    if (SOURCES.trackId === "fuji" && new URLSearchParams(window.location.search).get("look") === "cg") track.visualProfile = "cg";
  }
  if (track.trackId === "fuji" && track.visualProfile === "cg" && SOURCES.raceId === "fuji_aim_01") {
    if (alignment === "visual") {
      track.replayAlignment = await fetchJson<import("./visualAlignment").ReplayAlignment>(`${trackDir}/cg_study/replay-alignment.json`);
    } else if (alignment === "local" || alignment === "local-raw") {
      const profile = await fetchJson<import("./visualAlignment").LocalReplayAlignment>(`${trackDir}/cg_study/local-replay-alignment.json`);
      track.replayAlignment = { ...profile, enabled: alignment === "local" };
    }
  }
  track.satVariant = availableSatelliteVariants.includes(SOURCES.satVariant)
    ? SOURCES.satVariant
    : "default";
  const activeRecord = pickInitialLap(lapsIndex);
  const fetched = await fetchHashedJson<LapData>(`${SOURCES.raceBaseUrl}/${activeRecord.data_file}`);
  gpsLimitsSha256 = await fetchRegistrationLimitsHash(gpsRegistration);
  const lap = withRegistration(fetched.data, gpsRegistration, gpsRegistrationInitiallyEnabled(search),
    fetched.sha256, gpsLimitsSha256, gpsTrackSha256);
  return { track, lapsIndex, lap, activeRecord, availableSatelliteVariants };
}

/**
 * Loads a lap JSON file by its data_file path. `registrationEnabled` is the
 * viewer's current raw/registered choice so a newly picked lap matches it.
 */
export async function loadLapFile(dataFile: string, registrationEnabled = true): Promise<LapData> {
  const fetched = await fetchHashedJson<LapData>(`${SOURCES.raceBaseUrl}/${dataFile}`);
  return withRegistration(fetched.data, gpsRegistration, registrationEnabled, fetched.sha256, gpsLimitsSha256, gpsTrackSha256);
}

/**
 * The vehicle's best lap of the session: the record flagged upstream as
 * `is_best_vehicle`, falling back to the smallest positive lap_time_seconds.
 * Considers every timed lap in the index (not just ones with data files), so
 * the HUD "BEST" stays the true session best even while a slower lap is being
 * replayed. Null when the vehicle has no timed laps.
 */
export function getVehicleBestLap(index: LapsIndex, vehicleId: string): LapIndexRecord | null {
  const timed = index.laps.filter(
    (rec) => rec.vehicle_id === vehicleId && rec.lap_time_seconds > 0,
  );
  if (timed.length === 0) return null;
  const flagged = timed.find((rec) => rec.is_best_vehicle);
  if (flagged) return flagged;
  return timed.reduce((best, rec) => (rec.lap_time_seconds < best.lap_time_seconds ? rec : best));
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
