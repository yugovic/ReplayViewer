import { useMemo, type MouseEvent } from "react";
import { useReplayStore } from "../state/replayStore";
import { timeAtDist } from "../replay/delta";
import type { TrackData } from "../replay/types";
import { sampleReplay } from "../replay/interpolation";
import { activeKerbContact, kerbContactText, kerbEventsForLap, strengthLabel, type ContactStrengthBin } from "../replay/kerbContacts";

const MAP_WIDTH = 190;
const PADDING = 8;
const SECTOR_COLORS = ["#f87171", "#4ade80", "#60a5fa"];
/** IMU kerb-contact strength (弱/中/強); same palette as the HUD pill. */
const CONTACT_COLORS: Record<ContactStrengthBin, string> = { low: "#fde68a", mid: "#fb923c", high: "#ef4444" };

interface MapGeometry {
  height: number;
  toX: (worldX: number) => number;
  toY: (worldZ: number) => number;
  sectorPaths: string[];
}

function buildGeometry(track: TrackData): MapGeometry {
  const [minX, maxX] = track.bounds.x;
  const [minZ, maxZ] = track.bounds.z;
  const spanX = Math.max(1, maxX - minX);
  const spanZ = Math.max(1, maxZ - minZ);
  const scale = (MAP_WIDTH - PADDING * 2) / spanX;
  const height = spanZ * scale + PADDING * 2;

  const toX = (worldX: number) => PADDING + (worldX - minX) * scale;
  // World north is −Z (see src/replay/projection.ts). Increasing world Z
  // means heading south, and SVG y already grows downward (screen-south),
  // so north-south needs no extra flip here: toY grows directly with z.
  // (A "height - ... - " flip here would mirror the map vertically while
  // leaving east/west alone — a reflection that reverses every corner's
  // apparent left/right, exactly the bug this file fixes.)
  const toY = (worldZ: number) => PADDING + (worldZ - minZ) * scale;

  // Split centerline into 3 sectors by distance, overlapping one point at
  // boundaries so the strokes connect.
  const total = track.totalLength;
  const boundaries = [total / 3, (total * 2) / 3, Infinity];
  const sectors: string[][] = [[], [], []];
  const fmt = (p: { x: number; z: number }) => `${toX(p.x).toFixed(1)},${toY(p.z).toFixed(1)}`;
  let sector = 0;
  let prev: { x: number; z: number } | null = null;
  for (const p of track.centerline) {
    while (sector < 2 && p.dist > boundaries[sector]) {
      sector += 1;
      // Repeat the previous point so consecutive sector strokes connect
      if (prev) sectors[sector].push(fmt(prev));
    }
    sectors[sector].push(fmt(p));
    prev = p;
  }
  // Close the loop: connect last sector back to the first point
  const first = track.centerline[0];
  if (first) {
    sectors[2].push(fmt(first));
  }

  return {
    height,
    toX,
    toY,
    sectorPaths: sectors.map((pts) => (pts.length > 1 ? `M ${pts.join(" L ")}` : "")),
  };
}

export function Minimap() {
  const track = useReplayStore((state) => state.track);
  const carPosition = useReplayStore((state) => state.carPosition);
  const ghostPosition = useReplayStore((state) => state.ghostPosition);
  const activeLap = useReplayStore((state) => state.activeLap);
  const showMinimap = useReplayStore((state) => state.showMinimap);
  const toggleMinimap = useReplayStore((state) => state.toggleMinimap);
  const seek = useReplayStore((state) => state.seek);
  const kerbContacts = useReplayStore((state) => state.kerbContacts);

  const geometry = useMemo(() => (track ? buildGeometry(track) : null), [track]);
  // Kerb-contact events of the active lap, placed at the car position at the
  // burst's RMS peak (follows the raw/registered choice via the lap object).
  const contactMarkers = useMemo(() => {
    if (!track || !activeLap) return [];
    return kerbEventsForLap(kerbContacts, activeLap.meta.race_id, activeLap.meta.lap).map((event) => {
      const sample = sampleReplay(activeLap, track, event.tPeak ?? (event.t0 + event.t1) / 2);
      return { event, x: sample.x, z: sample.z };
    });
  }, [kerbContacts, activeLap, track]);
  const contactEvents = useMemo(() => contactMarkers.map((m) => m.event), [contactMarkers]);
  // Selector returns a stable event object, so the map re-renders only when the lit event changes.
  const litContact = useReplayStore((state) => activeKerbContact(contactEvents, state.currentTime));

  if (!track || !geometry) return null;

  const handleClick = (event: MouseEvent<SVGSVGElement>) => {
    if (!activeLap) return;
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * MAP_WIDTH;
    const py = ((event.clientY - rect.top) / rect.height) * geometry.height;

    // Nearest centerline point in map space → seek to its distance
    let bestDist = Infinity;
    let bestPoint = track.centerline[0];
    for (const p of track.centerline) {
      const dx = geometry.toX(p.x) - px;
      const dy = geometry.toY(p.z) - py;
      const d = dx * dx + dy * dy;
      if (d < bestDist) {
        bestDist = d;
        bestPoint = p;
      }
    }
    if (bestPoint && bestDist < 20 * 20) {
      seek(timeAtDist(activeLap, bestPoint.dist));
    }
  };

  return (
    <div className={`minimap-panel hud-panel${showMinimap ? "" : " minimap-panel--collapsed"}`}>
      <div className="minimap-header">
        <span className="hud-label">TRACK MAP</span>
        <button
          type="button"
          className="minimap-toggle"
          onClick={toggleMinimap}
          title={showMinimap ? "Collapse minimap" : "Expand minimap"}
        >
          {showMinimap ? "\u2013" : "+"}
        </button>
      </div>
      {showMinimap && (
        <svg
          className="minimap-svg"
          viewBox={`0 0 ${MAP_WIDTH} ${geometry.height}`}
          width={MAP_WIDTH}
          height={geometry.height}
          onClick={handleClick}
          role="img"
          aria-label="Track minimap"
        >
          {/* Sector-coloured centerline */}
          {geometry.sectorPaths.map((d, i) =>
            d ? (
              <path
                key={i}
                d={d}
                fill="none"
                stroke={SECTOR_COLORS[i]}
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={0.85}
              />
            ) : null,
          )}
          {/* IMU kerb-contact events of the active lap (strength only, no side) */}
          {contactMarkers.map(({ event, x, z }) => {
            const cx = geometry.toX(x);
            const cy = geometry.toY(z);
            const r = event === litContact ? 4.2 : 2.6;
            return (
              <rect
                key={`${event.t0}-${event.kerb}`}
                className="minimap-contact"
                x={cx - r}
                y={cy - r}
                width={r * 2}
                height={r * 2}
                transform={`rotate(45 ${cx} ${cy})`}
                fill={CONTACT_COLORS[event.bin]}
                stroke="#1a0d05"
                strokeWidth={0.8}
                opacity={event === litContact ? 1 : 0.85}
              >
                <title>{`縁石振動 ${kerbContactText(event)}（${event.t0.toFixed(1)} s）`}</title>
              </rect>
            );
          })}
          {/* Ghost car (cyan) */}
          {ghostPosition && (
            <circle
              cx={geometry.toX(ghostPosition.x)}
              cy={geometry.toY(ghostPosition.z)}
              r={4}
              fill="#00cfff"
              stroke="#04222c"
              strokeWidth={1}
              opacity={0.9}
            />
          )}
          {/* Main car (yellow) */}
          {carPosition && (
            <circle
              cx={geometry.toX(carPosition.x)}
              cy={geometry.toY(carPosition.z)}
              r={4.5}
              fill="#e8f16c"
              stroke="#2b2e0d"
              strokeWidth={1}
            />
          )}
        </svg>
      )}
    </div>
  );
}
