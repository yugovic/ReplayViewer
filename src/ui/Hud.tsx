import { useEffect, useRef } from "react";
import { useReplayStore } from "../state/replayStore";
import { computeDeltaAtDist, buildSectorBoundaries, computeSectorTimes, getSectorIndex } from "../replay/delta";
import { LayersPanel } from "./LayersPanel";

function formatLapTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rem = seconds - minutes * 60;
  const remStr = rem.toFixed(3).padStart(6, "0");
  return `${String(minutes).padStart(2, "0")}:${remStr}`;
}

function formatSectorTime(seconds: number): string {
  return seconds.toFixed(3);
}

function GMeter({ gx, gy }: { gx: number; gy: number }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 6;
    const maxG = 3.0;

    ctx.clearRect(0, 0, w, h);

    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.18)";
    ctx.lineWidth = 1;
    ctx.stroke();

    for (const ring of [1, 2]) {
      const r = (radius * ring) / maxG;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.moveTo(cx - radius, cy);
    ctx.lineTo(cx + radius, cy);
    ctx.moveTo(cx, cy - radius);
    ctx.lineTo(cx, cy + radius);
    ctx.strokeStyle = "rgba(255,255,255,0.18)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = "9px monospace";
    ctx.textAlign = "center";
    ctx.fillText("ACC", cx + radius - 14, cy - 4);
    ctx.fillText("BRK", cx - radius + 14, cy - 4);

    const dotX = cx + (gx / maxG) * radius;
    const dotY = cy - (gy / maxG) * radius;
    const clampedDotX = Math.min(cx + radius, Math.max(cx - radius, dotX));
    const clampedDotY = Math.min(cy + radius, Math.max(cy - radius, dotY));

    const gradient = ctx.createRadialGradient(clampedDotX, clampedDotY, 0, clampedDotX, clampedDotY, 12);
    gradient.addColorStop(0, "rgba(255,80,0,0.45)");
    gradient.addColorStop(1, "rgba(255,80,0,0)");
    ctx.beginPath();
    ctx.arc(clampedDotX, clampedDotY, 12, 0, Math.PI * 2);
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(clampedDotX, clampedDotY, 5, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
  }, [gx, gy]);

  return <canvas ref={canvasRef} width={120} height={120} className="gmeter-canvas" />;
}

function ThrottleBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="input-bar-track">
      <div className="input-bar-fill throttle-fill" style={{ height: `${pct}%` }} />
    </div>
  );
}

function BrakeBar({ value, max = 120 }: { value: number; max?: number }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="input-bar-track">
      <div className="input-bar-fill brake-fill" style={{ height: `${pct}%` }} />
    </div>
  );
}

function SteeringWheel({ angle }: { angle: number }) {
  const wheelAngle = angle / 16;
  return (
    <div
      className="steering-wheel"
      style={{ transform: `rotate(${wheelAngle}deg)` }}
      title={`Steer: ${angle.toFixed(0)}\u00b0`}
    >
      <svg viewBox="0 0 40 40" width={40} height={40} aria-hidden="true">
        <circle cx="20" cy="20" r="17" fill="none" stroke="rgba(255,255,255,0.75)" strokeWidth="3" />
        <line x1="20" y1="3" x2="20" y2="11" stroke="rgba(255,255,255,0.75)" strokeWidth="3" strokeLinecap="round" />
        <circle cx="20" cy="20" r="4" fill="rgba(255,255,255,0.6)" />
        <line x1="3" y1="20" x2="11" y2="20" stroke="rgba(255,255,255,0.5)" strokeWidth="2" strokeLinecap="round" />
        <line x1="29" y1="20" x2="37" y2="20" stroke="rgba(255,255,255,0.5)" strokeWidth="2" strokeLinecap="round" />
      </svg>
    </div>
  );
}

function DeltaDisplay({ delta }: { delta: number }) {
  const isPositive = delta >= 0;
  const sign = isPositive ? "+" : "";
  const color = isPositive ? "#f87171" : "#4ade80";
  return (
    <div className="hud-row hud-delta" style={{ color }}>
      <span className="hud-label">DELTA</span>
      <strong className="hud-value" style={{ color }}>
        {sign}{delta.toFixed(3)}s
      </strong>
    </div>
  );
}

function SectorDisplay({
  sectorTimes,
  currentSector,
}: {
  sectorTimes: Array<number | undefined>;
  currentSector: number;
}) {
  return (
    <div className="hud-sectors">
      {sectorTimes.map((t, i) => (
        <div
          key={i}
          className={`hud-sector${i === currentSector ? " hud-sector--active" : t !== undefined ? " hud-sector--done" : ""}`}
        >
          <span className="hud-sector-label">S{i + 1}</span>
          <span className="hud-sector-time">{t !== undefined ? formatSectorTime(t) : "--"}</span>
        </div>
      ))}
    </div>
  );
}

export function Hud() {
  const lapMeta = useReplayStore((state) => state.lapMeta);
  const currentTime = useReplayStore((state) => state.currentTime);
  const telemetry = useReplayStore((state) => state.telemetry);
  const cameraMode = useReplayStore((state) => state.cameraMode);
  const setCameraMode = useReplayStore((state) => state.setCameraMode);
  const setShowLapSelector = useReplayStore((state) => state.setShowLapSelector);
  const activeLap = useReplayStore((state) => state.activeLap);
  const ghostLap = useReplayStore((state) => state.ghostLap);
  const ghostRecord = useReplayStore((state) => state.ghostRecord);

  const gx = telemetry.accx ?? 0;
  const gy = telemetry.accy ?? 0;
  const currentDist = telemetry.dist ?? 0;

  const CAMERA_MODES = [
    { mode: "chase" as const, label: "Chase", key: "1" },
    { mode: "cockpit" as const, label: "Cockpit", key: "2" },
    { mode: "top" as const, label: "Top", key: "3" },
    { mode: "free" as const, label: "Free", key: "4" },
    { mode: "tv" as const, label: "TV", key: "5" },
    { mode: "cinematic" as const, label: "Cinematic", key: "6" },
  ];

  // --- Delta time ---
  let delta: number | null = null;
  if (activeLap && ghostLap) {
    delta = computeDeltaAtDist(activeLap, ghostLap, currentDist);
  }

  // --- Sector times ---
  const totalDist = lapMeta?.total_distance_m ?? activeLap?.meta.total_distance_m ?? 3600;
  const sectorBoundaries = buildSectorBoundaries(totalDist, 3);
  const currentSector = getSectorIndex(currentDist, sectorBoundaries);
  const sectorTimes = activeLap
    ? computeSectorTimes(activeLap, sectorBoundaries, currentDist)
    : [undefined, undefined, undefined];

  return (
    <section className="hud" aria-label="Replay telemetry">
      {/* Top-left: lap info + current time + delta */}
      <div className="hud-panel hud-info">
        <div className="hud-row">
          <span className="hud-label">CAR</span>
          <strong className="hud-value">{lapMeta?.vehicle_number || lapMeta?.vehicle_id || "--"}</strong>
          <button
            type="button"
            className="hud-select-btn"
            onClick={() => setShowLapSelector(true)}
            title="Select lap"
          >
            [change]
          </button>
        </div>
        <div className="hud-row">
          <span className="hud-label">LAP</span>
          <strong className="hud-value">{lapMeta?.lap ?? "--"}</strong>
        </div>
        <div className="hud-row">
          <span className="hud-label">TIME</span>
          <strong className="hud-value hud-laptime">{formatLapTime(currentTime)}</strong>
        </div>
        {lapMeta?.lap_time && (
          <div className="hud-row">
            <span className="hud-label">BEST</span>
            <strong className="hud-value">{lapMeta.lap_time}</strong>
          </div>
        )}

        {/* Ghost info */}
        {ghostRecord && (
          <div className="hud-row hud-ghost-info">
            <span className="hud-label">GHOST</span>
            <strong className="hud-value" style={{ color: "#00cfff" }}>
              #{ghostRecord.vehicle_number} L{ghostRecord.lap}
            </strong>
          </div>
        )}

        {/* Delta */}
        {delta !== null && <DeltaDisplay delta={delta} />}

        {/* Sectors */}
        <SectorDisplay sectorTimes={sectorTimes} currentSector={currentSector} />
      </div>

      {/* Top-right: camera buttons */}
      <div className="hud-panel hud-cameras">
        {CAMERA_MODES.map(({ mode, label, key }) => (
          <button
            key={mode}
            type="button"
            className={`cam-btn${cameraMode === mode ? " cam-btn--active" : ""}`}
            onClick={() => setCameraMode(mode)}
            title={`Camera: ${label} [${key}]`}
          >
            <span className="cam-key">{key}</span> {label}
          </button>
        ))}
      </div>

      {/* Top-right, below cameras: scene layer toggles */}
      <LayersPanel />

      {/* Bottom-left: input cluster + speed + gear */}
      <div className="hud-panel hud-inputs">
        <div className="input-cluster">
          <div className="input-bar-group">
            <span className="input-bar-label">THR</span>
            <ThrottleBar value={telemetry.aps} />
          </div>
          <div className="input-cluster-center">
            <div className="hud-speed">{Math.round(telemetry.speed)}</div>
            <div className="hud-speed-unit">km/h</div>
            <div className="hud-gear">{telemetry.gear > 0 ? telemetry.gear : "N"}</div>
          </div>
          <div className="input-bar-group">
            <span className="input-bar-label">BRK</span>
            <BrakeBar value={telemetry.brake} />
          </div>
        </div>
        <SteeringWheel angle={telemetry.steer} />
      </div>

      {/* Bottom-right: G-meter */}
      <div className="hud-panel hud-gmeter">
        <span className="hud-label">G-FORCE</span>
        <GMeter gx={gx} gy={gy} />
        <div className="gmeter-values">
          <span>Lat {gx.toFixed(2)}G</span>
          <span>Lon {gy.toFixed(2)}G</span>
        </div>
      </div>
    </section>
  );
}
