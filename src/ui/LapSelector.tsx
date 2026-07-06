import { useState } from "react";
import { useReplayStore } from "../state/replayStore";
import {
  getAvailableLaps,
  getCurrentSelection,
  loadLapFile,
  TRACK_CATALOG,
  type TrackCatalogEntry,
} from "../replay/dataLoader";
import { lapDuration } from "../replay/interpolation";
import type { LapIndexRecord } from "../replay/types";

type SelectorTab = "main" | "ghost";

/** Format mm:ss.mmm */
function fmtLapTime(t: string | null | undefined): string {
  return t ?? "--:--.-";
}

/**
 * Switches to another track/race by rewriting the URL query and letting the
 * browser reload. A full reload re-resolves the module-level data sources and
 * rebuilds every trackId-keyed asset (satellite, terrain, features, scene),
 * which is why we navigate instead of trying to hot-swap the track in place.
 */
function navigateToTrack(entry: TrackCatalogEntry): void {
  const params = new URLSearchParams(window.location.search);
  params.set("track", entry.trackId);
  params.set("race", entry.raceId);
  // A satellite variant picked for the previous track may not exist on the new
  // one; drop it so the new track loads its own default imagery.
  params.delete("sat");
  window.location.search = params.toString();
}

function TrackPicker() {
  const current = getCurrentSelection();
  return (
    <div className="ls-track-section">
      <div className="ls-section-label">Track</div>
      <div className="ls-track-list">
        {TRACK_CATALOG.map((entry) => {
          const isActive =
            entry.trackId === current.trackId && entry.raceId === current.raceId;
          return (
            <button
              key={`${entry.trackId}-${entry.raceId}`}
              type="button"
              className={`ls-track-row${isActive ? " ls-track-row--active" : ""}`}
              aria-current={isActive ? "true" : undefined}
              disabled={isActive}
              onClick={() => navigateToTrack(entry)}
            >
              <span className="ls-track-name-row">
                <span className="ls-track-name">{entry.trackName}</span>
                {isActive && <span className="ls-track-now">NOW</span>}
              </span>
              <span className="ls-track-race">{entry.raceLabel}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function VehicleGroup({
  vehicleId,
  vehicleNumber,
  laps,
  selectedLap,
  onSelect,
}: {
  vehicleId: string;
  vehicleNumber: string;
  laps: LapIndexRecord[];
  selectedLap: LapIndexRecord | null;
  onSelect: (rec: LapIndexRecord) => void;
}) {
  return (
    <div className="ls-vehicle-group">
      <div className="ls-vehicle-header">
        <span className="ls-car-number">#{vehicleNumber}</span>
        <span className="ls-vehicle-id">{vehicleId}</span>
      </div>
      <div className="ls-lap-list">
        {laps.map((rec) => {
          const isActive = selectedLap?.vehicle_id === rec.vehicle_id && selectedLap?.lap === rec.lap;
          return (
            <button
              key={`${rec.vehicle_id}-${rec.lap}`}
              type="button"
              className={`ls-lap-row${isActive ? " ls-lap-row--active" : ""}`}
              onClick={() => onSelect(rec)}
            >
              <span className="ls-lap-num">L{rec.lap}</span>
              <span className="ls-lap-time">{fmtLapTime(rec.lap_time)}</span>
              {rec.is_overall_best && <span className="ls-badge ls-badge--overall">BEST</span>}
              {!rec.is_overall_best && rec.is_best_vehicle && (
                <span className="ls-badge ls-badge--vehicle">PB</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function LapSelector() {
  const showLapSelector = useReplayStore((state) => state.showLapSelector);
  const setShowLapSelector = useReplayStore((state) => state.setShowLapSelector);
  const lapsIndex = useReplayStore((state) => state.lapsIndex);
  const activeRecord = useReplayStore((state) => state.activeRecord);
  const ghostRecord = useReplayStore((state) => state.ghostRecord);
  const setActiveLap = useReplayStore((state) => state.setActiveLap);
  const setGhostLap = useReplayStore((state) => state.setGhostLap);
  const clearGhost = useReplayStore((state) => state.clearGhost);
  const setDuration = useReplayStore((state) => state.setDuration);
  const seek = useReplayStore((state) => state.seek);

  const [tab, setTab] = useState<SelectorTab>("main");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!showLapSelector) return null;
  if (!lapsIndex) return null;

  const availableLaps = getAvailableLaps(lapsIndex);

  // Group by vehicle
  const vehicleMap = new Map<string, { vehicleNumber: string; laps: LapIndexRecord[] }>();
  for (const rec of availableLaps) {
    if (!vehicleMap.has(rec.vehicle_id)) {
      vehicleMap.set(rec.vehicle_id, { vehicleNumber: rec.vehicle_number, laps: [] });
    }
    vehicleMap.get(rec.vehicle_id)!.laps.push(rec);
  }

  async function handleSelectMain(rec: LapIndexRecord) {
    if (!rec.data_file) return;
    setLoading(true);
    setError(null);
    try {
      const lapData = await loadLapFile(rec.data_file);
      setActiveLap(rec, lapData);
      setDuration(lapDuration(lapData));
      seek(0);
      setShowLapSelector(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleSelectGhost(rec: LapIndexRecord) {
    if (!rec.data_file) return;
    // If same as main, clear ghost
    if (activeRecord && rec.vehicle_id === activeRecord.vehicle_id && rec.lap === activeRecord.lap) {
      clearGhost();
      setShowLapSelector(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const lapData = await loadLapFile(rec.data_file);
      setGhostLap(rec, lapData);
      setShowLapSelector(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const currentSelected = tab === "main" ? activeRecord : ghostRecord;

  return (
    <div className="ls-overlay" role="dialog" aria-modal="true" aria-label="Lap selector">
      <div className="ls-modal">
        <div className="ls-header">
          <div className="ls-title">Select Track &amp; Lap</div>
          <div className="ls-tabs">
            <button
              type="button"
              className={`ls-tab${tab === "main" ? " ls-tab--active" : ""}`}
              onClick={() => setTab("main")}
            >
              Main Car
            </button>
            <button
              type="button"
              className={`ls-tab${tab === "ghost" ? " ls-tab--active" : ""}`}
              onClick={() => setTab("ghost")}
            >
              Ghost
            </button>
          </div>
          <button
            type="button"
            className="ls-close"
            onClick={() => setShowLapSelector(false)}
            aria-label="Close lap selector"
          >
            x
          </button>
        </div>

        <div className="ls-body">
          <TrackPicker />

          {tab === "ghost" && (
            <div className="ls-ghost-hint">
              {ghostRecord
                ? `Ghost: #${ghostRecord.vehicle_number} L${ghostRecord.lap} – click a lap to change, or click the current ghost to remove it`
                : "No ghost selected. Choose a lap below to compare."}
            </div>
          )}

          {Array.from(vehicleMap.entries()).map(([vehicleId, { vehicleNumber, laps }]) => (
            <VehicleGroup
              key={vehicleId}
              vehicleId={vehicleId}
              vehicleNumber={vehicleNumber}
              laps={laps}
              selectedLap={currentSelected}
              onSelect={tab === "main" ? handleSelectMain : handleSelectGhost}
            />
          ))}
        </div>

        {loading && <div className="ls-loading">Loading...</div>}
        {error && <div className="ls-error">{error}</div>}

        {tab === "ghost" && ghostRecord && (
          <div className="ls-footer">
            <button type="button" className="ls-clear-ghost" onClick={() => { clearGhost(); setShowLapSelector(false); }}>
              Clear Ghost
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
