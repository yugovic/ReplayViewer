import { useReplayStore } from "../state/replayStore";
import { formatLapClock, formatTransportClock, lapStartOffsetSeconds } from "../replay/lapClock";

const PLAYBACK_RATES = [0.5, 1, 2, 4];

export function ReplayControls() {
  const playing = useReplayStore((state) => state.playing);
  const currentTime = useReplayStore((state) => state.currentTime);
  const duration = useReplayStore((state) => state.duration);
  const range = useReplayStore((state) => state.playbackWindow);
  const playbackRate = useReplayStore((state) => state.playbackRate);
  const loop = useReplayStore((state) => state.loop);
  const togglePlaying = useReplayStore((state) => state.togglePlaying);
  const seek = useReplayStore((state) => state.seek);
  const setPlaybackRate = useReplayStore((state) => state.setPlaybackRate);
  const toggleLoop = useReplayStore((state) => state.toggleLoop);
  const lapMeta = useReplayStore((state) => state.lapMeta);
  // Clocks show true lap time (t + δ); the slider itself stays in recorded t.
  const lapClockOffset = lapStartOffsetSeconds(lapMeta);

  return (
    <section className="controls" aria-label="Replay controls">
      <button
        type="button"
        className="loop-btn"
        onClick={() => seek(range?.start ?? 0)}
        aria-label="Restart from the beginning"
        title="Restart from the beginning"
      >
        ⏮
      </button>
      <button className="play-button" type="button" onClick={togglePlaying} aria-label={playing ? "Pause" : "Play"}>
        {playing ? "⏸" : "▶"}
      </button>
      <span className="clock">{formatTransportClock(currentTime + lapClockOffset)}</span>
      <input
        aria-label="Replay time"
        aria-valuetext={formatLapClock(Math.min(currentTime, duration) + lapClockOffset)}
        className="seek"
        type="range"
        min={range?.start ?? 0}
        max={range?.end ?? Math.max(duration, 0.01)}
        step={0.01}
        value={Math.min(currentTime, duration)}
        onChange={(event) => seek(Number(event.target.value))}
      />
      <span className="clock">{formatTransportClock((range?.end ?? duration) + lapClockOffset)}</span>
      <div className="rate-buttons" role="group" aria-label="Playback speed">
        {PLAYBACK_RATES.map((rate) => (
          <button
            key={rate}
            type="button"
            className={`rate-btn${playbackRate === rate ? " rate-btn--active" : ""}`}
            onClick={() => setPlaybackRate(rate)}
            aria-pressed={playbackRate === rate}
          >
            {rate}x
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`loop-btn${loop ? " loop-btn--active" : ""}`}
        onClick={toggleLoop}
        aria-pressed={loop}
        title="Loop replay"
      >
        ↺
      </button>
    </section>
  );
}
