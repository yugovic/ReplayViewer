import { useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useReplayStore } from "../state/replayStore";
import { interpolateNumberSeries } from "../replay/interpolation";
import { playbackTimeAtTrueAxis, trueAxisOffset, type TelemetryAxis } from "../replay/delta";
import type { LapData } from "../replay/types";

/**
 * Both axes are on the TRUE lap basis like the HUD lap clock and delta:
 * seconds / metres since the timing line (recorded t + δ, dist + s0), not
 * since the lap's first GPS sample.
 */
type XAxis = TelemetryAxis;

const SPEED_COLOR = "#4ade80";
const APS_COLOR = "#86efac";
const BRAKE_COLOR = "#f87171";
const GHOST_COLOR = "#00cfff";
const GHOST_APS_COLOR = "#80f0ff";
const GHOST_BRAKE_COLOR = "#ff9999";

/**
 * Project ghost telemetry values onto the main lap's true x-axis: the ghost
 * channel at the same true lap distance (dist axis, the alignment of the 3D
 * ghost and the HUD delta) or the same true lap time (time axis).
 */
function projectGhostSeries(
  xAxis: XAxis,
  xMain: number[],
  ghostLap: LapData,
  ghostChannel: number[],
): number[] {
  return xMain.map((x) =>
    interpolateNumberSeries(ghostLap.t, ghostChannel, playbackTimeAtTrueAxis(ghostLap, xAxis, x)));
}

function buildPlotData(
  lap: LapData,
  ghostLap: LapData | null,
  xAxis: XAxis,
): uPlot.AlignedData {
  // uPlot requires AlignedData: [xValues[], y1[], y2[], ...] with equal lengths.
  const offset = trueAxisOffset(lap, xAxis);
  const xMain = (xAxis === "dist" ? (lap.dist ?? []) : (lap.t ?? [])).map((v) => v + offset);

  if (ghostLap) {
    const ghostSpeed = projectGhostSeries(xAxis, xMain, ghostLap, ghostLap.speed ?? []);
    const ghostAps = projectGhostSeries(xAxis, xMain, ghostLap, ghostLap.aps ?? []);
    const ghostBrake = projectGhostSeries(xAxis, xMain, ghostLap, ghostLap.brake ?? []);

    return [
      xMain,
      lap.speed ?? [],
      lap.aps ?? [],
      lap.brake ?? [],
      ghostSpeed,
      ghostAps,
      ghostBrake,
    ];
  }

  return [xMain, lap.speed ?? [], lap.aps ?? [], lap.brake ?? []];
}

function buildOptions(
  width: number,
  height: number,
  xAxis: XAxis,
  hasGhost: boolean,
): uPlot.Options {
  const xLabel = xAxis === "dist" ? "Distance (m)" : "Lap time (s)";

  const series: uPlot.Series[] = [
    { label: xAxis === "dist" ? "Dist" : "Time" },
    { label: "Speed", stroke: SPEED_COLOR, width: 1.5, scale: "spd" },
    { label: "Throttle", stroke: APS_COLOR, width: 1.5, scale: "pct" },
    { label: "Brake", stroke: BRAKE_COLOR, width: 1.5, scale: "pct" },
  ];

  if (hasGhost) {
    series.push(
      { label: "Speed (G)", stroke: GHOST_COLOR, width: 1, scale: "spd", dash: [4, 4] },
      { label: "Thr (G)", stroke: GHOST_APS_COLOR, width: 1, scale: "pct", dash: [4, 4] },
      { label: "Brk (G)", stroke: GHOST_BRAKE_COLOR, width: 1, scale: "pct", dash: [4, 4] },
    );
  }

  return {
    width,
    height,
    cursor: {
      drag: { x: false, y: false },
    },
    legend: { show: false },
    axes: [
      {
        label: xLabel,
        stroke: "#8fa4ad",
        ticks: { stroke: "#2a3540" },
        grid: { stroke: "#1e2a32" },
      },
      {
        label: "Speed km/h",
        scale: "spd",
        stroke: "#8fa4ad",
        side: 3,
        grid: { stroke: "#1e2a32" },
      },
      {
        label: "%",
        scale: "pct",
        stroke: "#8fa4ad",
        side: 1,
        grid: { show: false },
      },
    ],
    scales: {
      // CRITICAL: x defaults to `time: true` in uPlot, which routes the axis
      // through the date/timezone split+format pipeline. Our x values are
      // metres (dist mode) or lap-relative seconds (time mode), so the scale
      // must be a plain numeric scale.
      x: { time: false },
      spd: { range: [0, 250] },
      pct: { range: [0, 120] },
    },
    series,
  };
}

interface TelemetryPanelProps {
  lap: LapData;
}

export function TelemetryPanel({ lap }: TelemetryPanelProps) {
  const showTelemetryPanel = useReplayStore((state) => state.showTelemetryPanel);
  const toggleTelemetryPanel = useReplayStore((state) => state.toggleTelemetryPanel);
  const ghostLap = useReplayStore((state) => state.ghostLap);
  const currentTime = useReplayStore((state) => state.currentTime);
  const seek = useReplayStore((state) => state.seek);
  const telemetry = useReplayStore((state) => state.telemetry);

  const [xAxis, setXAxis] = useState<XAxis>("dist");
  const plotRef = useRef<uPlot | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Store seek callback in a ref so click handler always has fresh capture
  const seekRef = useRef<(xVal: number) => void>(() => undefined);

  // Build seek callback: true-axis value → playback time (recorded t basis; seek clamps).
  useEffect(() => {
    seekRef.current = (xVal: number) => {
      if (lap.t.length === 0) return;
      seek(playbackTimeAtTrueAxis(lap, xAxis, xVal));
    };
  }, [xAxis, lap, seek]);

  // Build/rebuild plot when data or xAxis changes
  useEffect(() => {
    if (!showTelemetryPanel || !containerRef.current) return;

    const container = containerRef.current;
    const width = Math.round(container.getBoundingClientRect().width) || 800;
    const height = 160;

    if (plotRef.current) {
      plotRef.current.destroy();
      plotRef.current = null;
    }

    const attachClickSeek = (u: uPlot) => {
      // Seek on click. Positions passed to posToVal must be relative to the
      // plotting-area overlay (u.over), NOT the canvas (which includes axes).
      const over = u.over;
      const handleClick = (e: MouseEvent) => {
        const rect = over.getBoundingClientRect();
        const left = e.clientX - rect.left;
        const xVal = u.posToVal(left, "x");
        if (Number.isFinite(xVal)) {
          seekRef.current(xVal);
        }
      };
      over.addEventListener("click", handleClick);
      (u as unknown as { _clickCleanup?: () => void })._clickCleanup = () => {
        over.removeEventListener("click", handleClick);
      };
    };

    const createPlot = (opts: uPlot.Options, data: uPlot.AlignedData): uPlot => {
      const u = new uPlot(opts, data, container);
      // uPlot defers its first layout+draw to a microtask (commit() ->
      // queueMicrotask(_commit)), so draw-time errors escape this effect and
      // leave a blank canvas while the cursor overlay keeps working. Flushing
      // synchronously via batch() surfaces those errors here, where we can
      // recover with a fallback config.
      try {
        u.batch(() => undefined);
      } catch (err) {
        u.destroy();
        throw err;
      }
      return u;
    };

    let u: uPlot | null = null;
    const data = buildPlotData(lap, ghostLap, xAxis);

    try {
      u = createPlot(buildOptions(width, height, xAxis, ghostLap !== null), data);
    } catch (err) {
      console.error("[TelemetryPanel] uPlot draw failed, using fallback options:", err);
      u = null;
      try {
        // Minimal, known-good config: numeric scales, default axes.
        const fallback: uPlot.Options = {
          width,
          height,
          legend: { show: false },
          cursor: { drag: { x: false, y: false } },
          scales: { x: { time: false }, spd: { range: [0, 250] }, pct: { range: [0, 120] } },
          series: [
            {},
            { stroke: SPEED_COLOR, width: 1.5, scale: "spd" },
            { stroke: APS_COLOR, width: 1.5, scale: "pct" },
            { stroke: BRAKE_COLOR, width: 1.5, scale: "pct" },
            ...(ghostLap
              ? [
                  { stroke: GHOST_COLOR, width: 1, scale: "spd", dash: [4, 4] },
                  { stroke: GHOST_APS_COLOR, width: 1, scale: "pct", dash: [4, 4] },
                  { stroke: GHOST_BRAKE_COLOR, width: 1, scale: "pct", dash: [4, 4] },
                ]
              : []),
          ],
        };
        u = createPlot(fallback, data);
      } catch (err2) {
        console.error("[TelemetryPanel] uPlot fallback init failed:", err2);
        u = null;
      }
    }

    if (u) {
      plotRef.current = u;
      attachClickSeek(u);
    }

    // Keep chart width in sync with the panel (also recovers from a 0-width mount).
    const resizeObserver = new ResizeObserver((entries) => {
      const plot = plotRef.current;
      if (!plot) return;
      const newWidth = Math.round(entries[0].contentRect.width);
      if (newWidth > 0 && newWidth !== plot.width) {
        plot.setSize({ width: newWidth, height });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      if (plotRef.current) {
        const cleanup = plotRef.current as unknown as { _clickCleanup?: () => void };
        cleanup._clickCleanup?.();
        plotRef.current.destroy();
        plotRef.current = null;
      }
    };
  }, [lap, ghostLap, xAxis, showTelemetryPanel]);

  // Move the playback cursor line to match replay position
  useEffect(() => {
    if (!plotRef.current) return;
    const u = plotRef.current;
    const xVal = (xAxis === "time" ? currentTime : telemetry.dist) + trueAxisOffset(lap, xAxis);
    const left = u.valToPos(xVal, "x");
    if (Number.isFinite(left) && left >= 0) {
      u.setCursor({ left, top: (u.cursor.top as number | undefined) ?? 0 });
    }
  }, [currentTime, telemetry.dist, xAxis, lap]);

  if (!showTelemetryPanel) {
    return (
      <button
        type="button"
        className="telemetry-toggle"
        onClick={toggleTelemetryPanel}
        aria-label="Show telemetry graph"
      >
        Telemetry
      </button>
    );
  }

  return (
    <div className="telemetry-panel">
      <div className="telemetry-toolbar">
        <span className="telemetry-title">Telemetry</span>
        <div className="telemetry-xaxis-btns">
          <button
            type="button"
            className={`telem-btn${xAxis === "dist" ? " telem-btn--active" : ""}`}
            onClick={() => setXAxis("dist")}
          >
            Dist
          </button>
          <button
            type="button"
            className={`telem-btn${xAxis === "time" ? " telem-btn--active" : ""}`}
            onClick={() => setXAxis("time")}
          >
            Time
          </button>
        </div>
        <div className="telemetry-legend">
          <span style={{ color: SPEED_COLOR }}>Speed</span>
          <span style={{ color: APS_COLOR }}>Thr</span>
          <span style={{ color: BRAKE_COLOR }}>Brk</span>
          {ghostLap && (
            <span style={{ color: GHOST_COLOR }}>Ghost</span>
          )}
        </div>
        <button
          type="button"
          className="telemetry-close"
          onClick={toggleTelemetryPanel}
          aria-label="Hide telemetry graph"
        >
          x
        </button>
      </div>
      <div ref={containerRef} className="telemetry-plot-container" />
    </div>
  );
}
