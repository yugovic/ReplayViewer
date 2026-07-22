import type { SatVariantId } from "./satelliteVariants";

export interface ProjectionOrigin {
  lat: number;
  lng: number;
  alt: number;
}

export interface TrackPoint {
  lat: number;
  lng: number;
  alt: number;
  dist: number;
  x: number;
  y: number;
  z: number;
  /**
   * Absolute elevation (m ASL, same datum as `alt`) of the road's LEFT and
   * RIGHT edges at this centerline point, baked from the LiDAR terrain raster
   * by pipeline/bake_road_camber.py (P2). LEFT/RIGHT are the driver's left/right
   * in the direction of increasing `dist` (left = up × forward). Their mean
   * equals `alt` by construction, so `alt`/`y` stay authoritative. Optional:
   * pre-P2 track.json (and test fixtures) omit them, in which case the viewer
   * and replay fall back to the flat centerline profile (backward compatible).
   */
  altLeft?: number;
  altRight?: number;
}

export interface TrackData {
  version: number;
  trackId: string;
  trackName: string;
  width: number;
  totalLength: number;
  origin: ProjectionOrigin;
  bounds: {
    lat: [number, number];
    lng: [number, number];
    x: [number, number];
    z: [number, number];
  };
  centerline: TrackPoint[];
  /**
   * Satellite image variant to use for this track's ground texture, resolved
   * from the developer-only `?dev=1&sat=` query by dataLoader (see
   * satelliteVariants.ts for the id -> filename mapping). The user-facing
   * viewer uses one curated per-track variant. Optional so track.json fixtures/tests that
   * don't set it keep working; TrackBuilder treats a missing value as default.
   */
  satVariant?: SatVariantId;
  /**
   * Per-track initial layer state, applied once when the track loads (the
   * user can still toggle afterwards). Bootstrapped tracks (suzuka/okayama)
   * set road3d=false so the car runs directly on the aerial imagery instead
   * of the procedural asphalt ribbon; tracks without the field keep the
   * store defaults.
   */
  defaultLayers?: { road3d?: boolean };
}

export interface LapMeta {
  version: number;
  track_id: string;
  race_id: string;
  vehicle_id: string;
  vehicle_number: string;
  lap: number;
  lap_time_seconds: number | null;
  lap_time: string | null;
  point_count: number;
  raw_gps_points: number;
  gps_outliers_removed: number;
  total_distance_m: number;
  source_file: string;
  time_column: string;
  first_sample_time: string;
  last_sample_time: string;
}

export interface LapData {
  meta: LapMeta;
  t: number[];
  lat: number[];
  lng: number[];
  speed: number[];
  aps: number[];
  brake: number[];
  steer: number[];
  gear: number[];
  accx: number[];
  accy: number[];
  dist: number[];
}

export interface LapIndexRecord {
  vehicle_id: string;
  vehicle_number: string;
  lap: number;
  lap_time_seconds: number;
  lap_time: string;
  start_time: string;
  end_time: string;
  outing: string;
  is_best_vehicle: boolean;
  is_overall_best: boolean;
  data_file?: string;
  point_count?: number;
  file_size?: number;
  total_distance_m?: number;
}

export interface LapsIndex {
  version: number;
  race_id: string;
  track_id: string;
  selected: Array<{
    vehicle_id: string;
    lap: number;
    data_file: string;
    point_count: number;
    file_size: number;
    total_distance_m: number;
  }>;
  laps: LapIndexRecord[];
}

export interface ReplayTelemetry {
  speed: number;
  aps: number;
  brake: number;
  steer: number;
  gear: number;
  accx: number;
  accy: number;
  dist: number;
}

export interface ReplaySample {
  time: number;
  x: number;
  y: number;
  z: number;
  heading: number;
  pitch: number;
  /**
   * Bank / camber roll about the direction of travel, in radians. Positive =
   * the road's LEFT edge is higher than the RIGHT (the car leans to the right).
   * Derived from the baked road cross-slope (see sampleTrackRoll); 0 for tracks
   * without camber data. Clamped to ±MAX_VEHICLE_ROLL.
   */
  roll: number;
  telemetry: ReplayTelemetry;
}

export interface LoadedReplay {
  track: TrackData;
  lapsIndex: LapsIndex;
  lap: LapData;
  activeRecord: LapIndexRecord;
  /** Developer-mode variant ids; user mode contains only its curated variant. */
  availableSatelliteVariants: SatVariantId[];
}
