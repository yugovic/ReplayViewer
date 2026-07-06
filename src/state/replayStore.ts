import { create } from "zustand";
import type { LapData, LapIndexRecord, LapMeta, LapsIndex, ReplayTelemetry, TrackData } from "../replay/types";
import type { CameraMode } from "../engine/cameras";
import type { QualityPreset } from "../engine/Effects";
import { SATELLITE_VARIANTS, type SatVariantId } from "../replay/satelliteVariants";

interface ReplayState {
  // Playback
  playing: boolean;
  currentTime: number;
  duration: number;
  playbackRate: number;
  loop: boolean;

  // Camera / rendering
  cameraMode: CameraMode;
  quality: QualityPreset;

  // Main lap
  lapMeta: LapMeta | null;
  telemetry: ReplayTelemetry;

  // Data index (available after initial load)
  track: TrackData | null;
  lapsIndex: LapsIndex | null;
  activeRecord: LapIndexRecord | null;
  activeLap: LapData | null;

  // Ghost lap (optional)
  ghostRecord: LapIndexRecord | null;
  ghostLap: LapData | null;

  // Live world positions for the minimap (throttled updates)
  carPosition: { x: number; z: number } | null;
  ghostPosition: { x: number; z: number } | null;

  // UI visibility
  showLapSelector: boolean;
  showTelemetryPanel: boolean;
  showMinimap: boolean;

  // Scene layers
  showRoad3d: boolean;
  showOsmFeatures: boolean;
  showFeatures3d: boolean;
  showDetailTexture: boolean;

  // Satellite imagery variant (mutually-exclusive selection)
  satelliteVariant: SatVariantId;
  availableSatelliteVariants: SatVariantId[];

  // Actions – playback
  setDuration: (duration: number) => void;
  togglePlaying: () => void;
  setPlaying: (playing: boolean) => void;
  seek: (time: number) => void;
  advance: (deltaSeconds: number) => void;
  setPlaybackRate: (rate: number) => void;
  toggleLoop: () => void;

  // Actions – camera
  setCameraMode: (mode: CameraMode) => void;
  setQuality: (quality: QualityPreset) => void;

  // Actions – data
  setLapMeta: (meta: LapMeta) => void;
  setTelemetry: (telemetry: ReplayTelemetry) => void;
  setTrack: (track: TrackData) => void;
  setLapsIndex: (index: LapsIndex) => void;
  setActiveLap: (record: LapIndexRecord, lap: LapData) => void;
  setGhostLap: (record: LapIndexRecord | null, lap: LapData | null) => void;
  clearGhost: () => void;
  setMapPositions: (
    car: { x: number; z: number } | null,
    ghost: { x: number; z: number } | null,
  ) => void;

  // Actions – UI
  setShowLapSelector: (show: boolean) => void;
  toggleTelemetryPanel: () => void;
  toggleMinimap: () => void;

  // Actions – scene layers
  setShowRoad3d: (show: boolean) => void;
  setShowOsmFeatures: (show: boolean) => void;
  setShowFeatures3d: (show: boolean) => void;
  setShowDetailTexture: (show: boolean) => void;
  toggleRoad3d: () => void;
  toggleOsmFeatures: () => void;
  toggleFeatures3d: () => void;
  toggleDetailTexture: () => void;

  // Actions – satellite variant
  setSatelliteVariant: (variant: SatVariantId) => void;
  setAvailableSatelliteVariants: (variants: SatVariantId[]) => void;
  /** Cycle to the next available variant (wraps around); used by key "0". */
  cycleSatelliteVariant: () => void;
}

const emptyTelemetry: ReplayTelemetry = {
  speed: 0,
  aps: 0,
  brake: 0,
  steer: 0,
  gear: 0,
  accx: 0,
  accy: 0,
  dist: 0,
};

export const useReplayStore = create<ReplayState>((set, get) => ({
  // Playback
  playing: true,
  currentTime: 0,
  duration: 0,
  playbackRate: 1,
  loop: true,

  // Camera / rendering
  cameraMode: "chase",
  quality: "high",

  // Main lap
  lapMeta: null,
  telemetry: emptyTelemetry,

  // Data
  track: null,
  lapsIndex: null,
  activeRecord: null,
  activeLap: null,

  // Ghost
  ghostRecord: null,
  ghostLap: null,

  // Map positions
  carPosition: null,
  ghostPosition: null,

  // UI
  showLapSelector: false,
  showTelemetryPanel: false,
  showMinimap: true,

  // Scene layers
  showRoad3d: true,
  showOsmFeatures: true,
  showFeatures3d: true,
  showDetailTexture: true,

  // Satellite imagery variant
  satelliteVariant: "default",
  availableSatelliteVariants: ["default"],

  // ── Playback ──────────────────────────────────────────────────────────────

  setDuration: (duration) => set({ duration }),

  togglePlaying: () => set((state) => ({ playing: !state.playing })),

  setPlaying: (playing) => set({ playing }),

  seek: (time) => {
    const duration = get().duration;
    set({ currentTime: Math.min(duration, Math.max(0, time)) });
  },

  advance: (deltaSeconds) => {
    const { currentTime, duration, playbackRate, loop } = get();
    if (duration <= 0) return;
    const nextTime = currentTime + deltaSeconds * playbackRate;
    if (nextTime > duration) {
      if (loop) {
        set({ currentTime: 0 });
      } else {
        set({ currentTime: duration, playing: false });
      }
    } else {
      set({ currentTime: nextTime });
    }
  },

  setPlaybackRate: (rate) => set({ playbackRate: rate }),

  toggleLoop: () => set((state) => ({ loop: !state.loop })),

  // ── Camera / rendering ────────────────────────────────────────────────────

  setCameraMode: (cameraMode) => set({ cameraMode }),

  setQuality: (quality) => set({ quality }),

  // ── Data ──────────────────────────────────────────────────────────────────

  setLapMeta: (lapMeta) => set({ lapMeta }),

  setTelemetry: (telemetry) => set({ telemetry }),

  setTrack: (track) => set({ track }),

  setLapsIndex: (lapsIndex) => set({ lapsIndex }),

  setActiveLap: (record, lap) =>
    set({
      activeRecord: record,
      activeLap: lap,
      lapMeta: lap.meta,
    }),

  setGhostLap: (record, lap) => set({ ghostRecord: record, ghostLap: lap }),

  clearGhost: () => set({ ghostRecord: null, ghostLap: null }),

  setMapPositions: (car, ghost) => set({ carPosition: car, ghostPosition: ghost }),

  // ── UI ────────────────────────────────────────────────────────────────────

  setShowLapSelector: (show) => set({ showLapSelector: show }),

  toggleTelemetryPanel: () =>
    set((state) => ({ showTelemetryPanel: !state.showTelemetryPanel })),

  toggleMinimap: () => set((state) => ({ showMinimap: !state.showMinimap })),

  // ── Scene layers ──────────────────────────────────────────────────────────

  setShowRoad3d: (show) => set({ showRoad3d: show }),

  setShowOsmFeatures: (show) => set({ showOsmFeatures: show }),

  setShowFeatures3d: (show) => set({ showFeatures3d: show }),

  setShowDetailTexture: (show) => set({ showDetailTexture: show }),

  toggleRoad3d: () => set((state) => ({ showRoad3d: !state.showRoad3d })),

  toggleOsmFeatures: () => set((state) => ({ showOsmFeatures: !state.showOsmFeatures })),

  toggleFeatures3d: () => set((state) => ({ showFeatures3d: !state.showFeatures3d })),

  toggleDetailTexture: () => set((state) => ({ showDetailTexture: !state.showDetailTexture })),

  // ── Satellite variant ────────────────────────────────────────────────────

  setSatelliteVariant: (satelliteVariant) => set({ satelliteVariant }),

  setAvailableSatelliteVariants: (availableSatelliteVariants) => set({ availableSatelliteVariants }),

  cycleSatelliteVariant: () =>
    set((state) => {
      const order = SATELLITE_VARIANTS.map((v) => v.id).filter((id) =>
        state.availableSatelliteVariants.includes(id),
      );
      if (order.length === 0) return {};
      const currentIndex = order.indexOf(state.satelliteVariant);
      const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % order.length;
      return { satelliteVariant: order[nextIndex] };
    }),
}));
