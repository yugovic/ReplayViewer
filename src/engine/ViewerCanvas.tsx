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
}

const KEY_CAMERA_MAP: Record<string, CameraMode> = {
  "1": "chase",
  "2": "cockpit",
  "3": "top",
  "4": "free",
  "5": "tv",
  "6": "cinematic",
};

export function ViewerCanvas({ track, lap }: ViewerCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<ReplayScene | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    const replayScene = new ReplayScene(containerRef.current);
    sceneRef.current = replayScene;
    replayScene.load(track, lap);
    // Restore ghost / layer visibility already selected in the store
    replayScene.setGhostLap(useReplayStore.getState().ghostLap);
    replayScene.setRoadVisible(useReplayStore.getState().showRoad3d);
    replayScene.setFeaturesVisible(useReplayStore.getState().showOsmFeatures);
    replayScene.setFeatures3dVisible(useReplayStore.getState().showFeatures3d);
    replayScene.setDetailTexture(useReplayStore.getState().showDetailTexture);
    replayScene.setSatelliteVariant(useReplayStore.getState().satelliteVariant);
    let frame = 0;
    let last = performance.now();
    let lastTelemetryUpdate = 0;

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
        ghostTime = ghostTimeForMainTime(lap, nextStore.ghostLap, nextStore.currentTime);
      }

      const sample = replayScene.update(nextStore.currentTime, ghostTime, nextStore.cameraMode, dt);
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
      if (e.key === "0") {
        useReplayStore.getState().cycleSatelliteVariant();
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
  }, [track, lap]);

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

  const satelliteVariant = useReplayStore((state) => state.satelliteVariant);
  useEffect(() => {
    sceneRef.current?.setSatelliteVariant(satelliteVariant);
  }, [satelliteVariant]);

  return <div className="viewer-canvas" ref={containerRef} />;
}
