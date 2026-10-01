import { useEffect, useRef } from "react";
import { ReplayScene } from "./ReplayScene";
import { useReplayStore } from "../state/replayStore";
import { ghostTimeForMainTime } from "../replay/delta";
import { sampleReplay } from "../replay/interpolation";
import type { CameraMode } from "./cameras";
import type { LapData, TrackData } from "../replay/types";

interface ViewerCanvasProps {
  track: TrackData;
  lap: LapData;
  developerMode?: boolean;
  onReady?: () => void;
}

const KEY_CAMERA_MAP: Record<string, CameraMode> = {
  "1": "chase",
  "2": "cockpit",
  "3": "top",
  "4": "free",
  "5": "tv",
  "6": "cinematic",
};

export function ViewerCanvas({
  track,
  lap,
  developerMode = false,
  onReady,
}: ViewerCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<ReplayScene | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  // The rAF tick below closes over refs (not props) so the scene-building
  // effect can key on [track] alone: picking another lap swaps replay data via
  // setLap() instead of tearing down the WebGL context and re-draping the
  // whole track (measured ~8 s per lap click before the split).
  const lapRef = useRef(lap);
  lapRef.current = lap;

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    const replayScene = new ReplayScene(containerRef.current);
    sceneRef.current = replayScene;
    if (import.meta.env.DEV) {
      const w = window as unknown as { __replayScene?: ReplayScene; __replayStore?: typeof useReplayStore };
      w.__replayScene = replayScene;
      w.__replayStore = useReplayStore;
    }
    replayScene.load(track, lapRef.current);
    // Restore ghost / layer visibility already selected in the store
    replayScene.setGhostLap(useReplayStore.getState().ghostLap);
    replayScene.setRoadVisible(useReplayStore.getState().showRoad3d);
    replayScene.setFeaturesVisible(useReplayStore.getState().showOsmFeatures);
    replayScene.setFeatures3dVisible(useReplayStore.getState().showFeatures3d);
    replayScene.setDetailTexture(useReplayStore.getState().showDetailTexture);
    replayScene.setTrackLines(useReplayStore.getState().showTrackLines);
    replayScene.setSatelliteVariant(useReplayStore.getState().satelliteVariant);
    replayScene.setAcOverlayVisible(useReplayStore.getState().showAcOverlay);
    replayScene.setTrialTilesVisible(useReplayStore.getState().showTrialTiles);
    replayScene.setDriveOnAc(useReplayStore.getState().driveOnAc);
    let frame = 0;
    let last = performance.now();
    let lastTelemetryUpdate = 0;
    let readyReported = false;

    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const store = useReplayStore.getState();
      if (store.playing) {
        store.advance(dt);
      }
      const nextStore = useReplayStore.getState();

      // Compute distance-aligned ghost time
      let ghostTime: number | null = null;
      if (nextStore.ghostLap) {
        ghostTime = ghostTimeForMainTime(lapRef.current, nextStore.ghostLap, nextStore.currentTime);
      }

      const sample = replayScene.update(nextStore.currentTime, ghostTime, nextStore.cameraMode, dt);
      if (!readyReported) {
        readyReported = true;
        onReadyRef.current?.();
      }
      if (sample && now - lastTelemetryUpdate > 50) {
        useReplayStore.getState().setTelemetry(sample.telemetry);
        // Minimap positions (throttled with telemetry)
        let ghostPos: { x: number; z: number } | null = null;
        if (nextStore.ghostLap && ghostTime !== null) {
          const ghostSample = sampleReplay(nextStore.ghostLap, track, ghostTime);
          ghostPos = { x: ghostSample.x, z: ghostSample.z };
        }
        useReplayStore.getState().setMapPositions({ x: sample.x, z: sample.z }, ghostPos);
        lastTelemetryUpdate = now;
      }
      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);

    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
        return;
      }
      const cameraMode = KEY_CAMERA_MAP[e.key];
      if (cameraMode) {
        useReplayStore.getState().setCameraMode(cameraMode);
        return;
      }
      if (e.key === "g" || e.key === "G") {
        // Raw GPS <-> track-limit registration: an evidence check for every
        // user, not developer tooling. No-op for laps without a registration.
        const s = useReplayStore.getState();
        s.setGpsRegistrationEnabled(!s.gpsRegistrationEnabled);
        return;
      }
      // Scene-layer and texture shortcuts are developer tooling. User mode
      // keeps only the replay controls (including Space) and camera keys.
      if (!developerMode && e.code !== "Space") {
        return;
      }
      if (e.key === "7") {
        useReplayStore.getState().toggleRoad3d();
        return;
      }
      if (e.key === "8") {
        useReplayStore.getState().toggleOsmFeatures();
        return;
      }
      if (e.key === "9") {
        useReplayStore.getState().toggleFeatures3d();
        return;
      }
      if (e.key === "d" || e.key === "D") {
        // Detail Texture moved off "9" (now the 3D-features toggle) to keep all
        // four layer toggles + the satellite cycle available on the keyboard.
        useReplayStore.getState().toggleDetailTexture();
        return;
      }
      if (e.key === "l" || e.key === "L") {
        // Vector track markings (white edge lines + curbs, Task 2).
        useReplayStore.getState().toggleTrackLines();
        return;
      }
      if (e.key === "i" || e.key === "I") {
        // Trial tiles (imagegen_trials): in-place A/B vs the standard SR look.
        useReplayStore.getState().toggleTrialTiles();
        return;
      }
      if (e.key === "0") {
        useReplayStore.getState().cycleSatelliteVariant();
        return;
      }
      if (e.key === "a" || e.key === "A") {
        useReplayStore.getState().toggleAcOverlay();
        return;
      }
      if (e.key === "m" || e.key === "M") {
        useReplayStore.getState().toggleDriveOnAc();
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        useReplayStore.getState().togglePlaying();
      }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      cancelAnimationFrame(frame);
      replayScene.dispose();
      sceneRef.current = null;
    };
    // lap intentionally omitted: lap switches go through the setLap effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [developerMode, track]);

  // Lap switch: swap replay data only — no track/WebGL rebuild.
  useEffect(() => {
    sceneRef.current?.setLap(lap);
  }, [lap]);

  // Sync ghost lap changes into the Three.js scene
  const ghostLap = useReplayStore((state) => state.ghostLap);
  useEffect(() => {
    sceneRef.current?.setGhostLap(ghostLap);
  }, [ghostLap]);

  // Sync scene-layer toggles into the Three.js scene
  const showRoad3d = useReplayStore((state) => state.showRoad3d);
  useEffect(() => {
    sceneRef.current?.setRoadVisible(showRoad3d);
  }, [showRoad3d]);

  const showOsmFeatures = useReplayStore((state) => state.showOsmFeatures);
  useEffect(() => {
    sceneRef.current?.setFeaturesVisible(showOsmFeatures);
  }, [showOsmFeatures]);

  const showFeatures3d = useReplayStore((state) => state.showFeatures3d);
  useEffect(() => {
    sceneRef.current?.setFeatures3dVisible(showFeatures3d);
  }, [showFeatures3d]);

  const showDetailTexture = useReplayStore((state) => state.showDetailTexture);
  useEffect(() => {
    sceneRef.current?.setDetailTexture(showDetailTexture);
  }, [showDetailTexture]);

  const showTrackLines = useReplayStore((state) => state.showTrackLines);
  useEffect(() => {
    sceneRef.current?.setTrackLines(showTrackLines);
  }, [showTrackLines]);

  const satelliteVariant = useReplayStore((state) => state.satelliteVariant);
  useEffect(() => {
    sceneRef.current?.setSatelliteVariant(satelliteVariant);
  }, [satelliteVariant]);

  const showAcOverlay = useReplayStore((state) => state.showAcOverlay);
  useEffect(() => {
    sceneRef.current?.setAcOverlayVisible(showAcOverlay);
  }, [showAcOverlay]);

  const driveOnAc = useReplayStore((state) => state.driveOnAc);
  useEffect(() => {
    sceneRef.current?.setDriveOnAc(driveOnAc);
  }, [driveOnAc]);

  const showTrialTiles = useReplayStore((state) => state.showTrialTiles);
  useEffect(() => {
    sceneRef.current?.setTrialTilesVisible(showTrialTiles);
  }, [showTrialTiles]);

  return <div className="viewer-canvas" ref={containerRef} />;
}
