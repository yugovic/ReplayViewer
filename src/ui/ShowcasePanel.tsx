import { CgAdjustmentPanel } from "./CgAdjustmentPanel";
import { FUJI_SHOWCASE, type ShowcaseLook } from "../replay/showcase";
import { useReplayStore } from "../state/replayStore";

export function ShowcasePanel({ look }: { look: ShowcaseLook }) {
  const track = useReplayStore(s => s.track);
  const lapMeta = useReplayStore(s => s.lapMeta);
  const aligned = !!track?.replayAlignment && lapMeta?.lap === 3;
  const alignmentHref = new URL(window.location.href);
  alignmentHref.searchParams.set("alignment", aligned ? "raw" : "visual");
  const seek = useReplayStore(s => s.seek);
  const setPlaying = useReplayStore(s => s.setPlaying);
  const names = { original: "元の画像処理版", reference: "前回の比較試作", cg: "3DCG試作版" };
  const href = (value: ShowcaseLook) => {
    const target = new URL(window.location.href); target.searchParams.set("look", value); return target.href;
  };
  return <aside className="showcase-panel" aria-label="25秒の品質プレビュー">
    <span className="showcase-eyebrow">FUJI / 25 SECOND STUDY</span>
    <strong>{FUJI_SHOWCASE.name}</strong>
    <div className="showcase-actions">
      <span>{names[look]}</span>
      {(["original", "cg", "reference"] as const).filter(value => value !== look).map(value =>
        <a key={value} href={href(value)}>{names[value]}</a>)}
      <button onClick={() => { seek(FUJI_SHOWCASE.start); setPlaying(true); }}>区間を再生</button>
    </div>
    <small>停止・視点変更ができます。航空写真：<a href="https://www.geospatial.jp/ckan/dataset/shizuoka-2019-pointcloud" target="_blank" rel="noreferrer">静岡県 VIRTUAL SHIZUOKA 2019</a> / CC BY 4.0・縮小加工。</small>
    {look === "cg" && <><a href="/docs/fuji-three-point-2026-09-18.html" target="_blank" rel="noreferrer">3地点の位置検証を開く</a><CgAdjustmentPanel /></>}
    {look === "cg" && <small>位置表示：{aligned ? "未検証の試験補正（区間限定）" : "元GPS"} · <a href={alignmentHref.href}>{aligned ? "元GPSと比較" : "未検証の試験補正を表示"}</a></small>}
    {look === "cg" && <small>縁石の平面位置を元画像からトレース。高さ・断面、背景は概略。全周の精度保証は未完了。</small>}
  </aside>;
}
