import { alignmentMatches } from "../replay/visualAlignment";
import { useReplayStore } from "../state/replayStore";

export function LocalAlignmentPanel() {
  const track = useReplayStore(s => s.track);
  const lap = useReplayStore(s => s.activeLap);
  const seek = useReplayStore(s => s.seek);
  const setPlaying = useReplayStore(s => s.setPlaying);
  const currentTime = useReplayStore(s => s.currentTime);
  const a = track?.replayAlignment;
  if (!track || !lap || a?.kind !== "local-windows") return null;
  const matches = alignmentMatches(lap, track);
  const enabled = matches && a.enabled !== false;
  const toggle = new URL(window.location.href);
  toggle.searchParams.set("alignment", enabled ? "local-raw" : "local");
  toggle.searchParams.set("time", currentTime.toFixed(2));
  return <aside className="local-alignment-panel" aria-label="地点別の位置補正">
    <strong>{matches ? (enabled ? "地点別補正 · 幅1700mm" : "元GPS · 幅1700mm") : "対象外のラップ · 元GPS"}</strong>
    <small>7/29 Demio・ラップ3の映像に合わせた暫定表示</small>
    <div>{[22, 55, 76, 84, 97, 122].map(t => <button key={t} onClick={() => { seek(t); setPlaying(false); }}>{t}秒</button>)}</div>
    <a href={toggle.href}>{enabled ? "同じ車幅の元GPSを見る" : "地点別補正を見る"}</a>
    <a href="/docs/fuji-local-alignment-2026-09-19.html#comparison" target="_blank" rel="noreferrer">実車との比較・補正の根拠</a>
    <small>元ログは保持。実車・道路の測量精度は未校正。</small>
  </aside>;
}
