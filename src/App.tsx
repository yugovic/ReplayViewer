import { useEffect, useState } from "react";
import { ViewerCanvas } from "./engine/ViewerCanvas";
import { isDeveloperMode, loadInitialReplay } from "./replay/dataLoader";
import { lapDuration } from "./replay/interpolation";
import type { LoadedReplay } from "./replay/types";
import { useReplayStore } from "./state/replayStore";
import { Hud } from "./ui/Hud";
import { ReplayControls } from "./ui/ReplayControls";
import { LapSelector } from "./ui/LapSelector";
import { TelemetryPanel } from "./ui/TelemetryPanel";
import { ApexKpiPanel } from "./ui/ApexKpiPanel";
import { Minimap } from "./ui/Minimap";
import { CreditOverlay } from "./ui/CreditOverlay";
import { FUJI_SHOWCASE, resolveShowcase } from "./replay/showcase";
import { LocalAlignmentPanel } from "./ui/LocalAlignmentPanel";
import { ShowcasePanel } from "./ui/ShowcasePanel";

const DEVELOPER_MODE = isDeveloperMode();

function LoadingIndicator() {
  return (
    <div className="loading-indicator" role="status">
      <span className="loading-spinner" aria-hidden="true" />
      <span>Loading replay...</span>
    </div>
  );
}

export function App() {
  const alignmentMode = new URLSearchParams(window.location.search).get("alignment");
  const showcase = alignmentMode === "local" || alignmentMode === "local-raw"
    ? null : resolveShowcase(window.location.search);
  const [bundle, setBundle] = useState<LoadedReplay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const setDuration = useReplayStore((state) => state.setDuration);
  const seek = useReplayStore((state) => state.seek);
  const setTrack = useReplayStore((state) => state.setTrack);
  const setLapsIndex = useReplayStore((state) => state.setLapsIndex);
  const setActiveLap = useReplayStore((state) => state.setActiveLap);
  const setShowLapSelector = useReplayStore((state) => state.setShowLapSelector);
  const setSatelliteVariant = useReplayStore((state) => state.setSatelliteVariant);
  const setAvailableSatelliteVariants = useReplayStore((state) => state.setAvailableSatelliteVariants);
  const applyViewerModePreset = useReplayStore((state) => state.applyViewerModePreset);

  // Use the activeLap from store if available (set by lap selector), otherwise fall back to bundle
  const storeLap = useReplayStore((state) => state.activeLap);
  const handleSceneReady = () => setSceneReady(true);

  useEffect(() => {
    let cancelled = false;
    applyViewerModePreset(DEVELOPER_MODE);
    loadInitialReplay()
      .then((loaded) => {
        if (cancelled) return;
        setBundle(loaded);
        setTrack(loaded.track);
        // Per-track initial layer state (e.g. bootstrapped tracks run the car
        // directly on the aerial imagery — no procedural road ribbon).
        if (DEVELOPER_MODE && typeof loaded.track.defaultLayers?.road3d === "boolean") {
          useReplayStore.getState().setShowRoad3d(loaded.track.defaultLayers.road3d);
        }
        setLapsIndex(loaded.lapsIndex);
        setActiveLap(loaded.activeRecord, loaded.lap);
        // Optional Fuji analysis layers: kerb contacts, clipping-point KPI, registration source.
        useReplayStore.getState().setAnalysisData(loaded.analysis);
        setAvailableSatelliteVariants(loaded.availableSatelliteVariants);
        setSatelliteVariant(loaded.track.satVariant ?? "default");
        setDuration(lapDuration(loaded.lap));
        useReplayStore.getState().setPlaybackWindow(showcase ? FUJI_SHOWCASE : null);
        const localStudy = loaded.track.replayAlignment?.kind === "local-windows";
        if (localStudy) useReplayStore.getState().setPlaying(false);
        const requestedTime = Number(new URLSearchParams(window.location.search).get("time") ?? 55);
        seek(showcase ? FUJI_SHOWCASE.start : localStudy && Number.isFinite(requestedTime)
          ? Math.max(0, Math.min(lapDuration(loaded.lap), requestedTime)) : 0);
        if (showcase === "reference") {
          const s = useReplayStore.getState();
          s.setShowTrialTiles(false);
          s.setShowDetailTexture(true);
        }
        // Show lap selector on startup so user can choose/configure
        setShowLapSelector(!showcase && !localStudy);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    showcase,
    applyViewerModePreset,
    seek,
    setDuration,
    setTrack,
    setLapsIndex,
    setActiveLap,
    setShowLapSelector,
    setAvailableSatelliteVariants,
    setSatelliteVariant,
  ]);

  if (error) {
    return <main className="app-shell status-panel">Failed to load replay data: {error}</main>;
  }

  if (!bundle) {
    return (
      <main className="app-shell loading-screen">
        <LoadingIndicator />
      </main>
    );
  }

  // Use active lap from store if changed via LapSelector, otherwise use the initial bundle lap
  const currentLap = storeLap ?? bundle.lap;

  return (
    <main className="app-shell" data-showcase={showcase ?? undefined}>
      <ViewerCanvas
        track={bundle.track}
        lap={currentLap}
        developerMode={DEVELOPER_MODE}
        onReady={handleSceneReady}
      />
      <Hud developerMode={DEVELOPER_MODE} />
      <Minimap />
      <ReplayControls />
      <LapSelector />
      <TelemetryPanel lap={currentLap} />
      <ApexKpiPanel />
      <CreditOverlay />
      {showcase && bundle.track.replayAlignment?.kind !== "local-windows" && <ShowcasePanel look={showcase} />}
      <LocalAlignmentPanel />
      {!sceneReady && (
        <div className="loading-screen loading-screen--overlay">
          <LoadingIndicator />
        </div>
      )}
    </main>
  );
}
