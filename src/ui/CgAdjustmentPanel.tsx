import { useState } from 'react';
import { getCgEditor, type CgEditor, type CurbAdjustment } from '../engine/track/FujiCgEditor';

export function CgAdjustmentPanel() {
  const [editor, setEditor] = useState<CgEditor | null>(null);
  const [index, setIndex] = useState(17);
  const [changes, setChanges] = useState<Record<number, CurbAdjustment>>({});
  const [message, setMessage] = useState('');
  const selected = changes[index] ?? { index, offsetMeters: 0, widthScale: 1 };
  const update = (patch: Partial<CurbAdjustment>) => {
    const next = { ...selected, ...patch };
    try { editor?.apply(next); setChanges(previous => ({ ...previous, [index]: next })); setMessage('試調整中 · 更新すると元に戻ります'); }
    catch (error) { setMessage(String(error)); }
  };
  const download = () => {
    if (!editor) return;
    const url = URL.createObjectURL(new Blob([editor.export()], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'fuji-curb-adjustments.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <div className="cg-adjustment">
    <button aria-expanded={!!editor} onClick={() => {
      if (editor) { setEditor(null); return; }
      const current = getCgEditor(); setEditor(current);
      if (!current) setMessage('3Dの読み込み後にもう一度開いてください。');
    }}>{editor ? '調整パネルを閉じる' : '縁石の位置・幅を調整'}</button>
    {editor && <fieldset>
      <legend>縁石を試調整</legend>
      <label>区間<select value={index} onChange={event => setIndex(Number(event.target.value))}>
        {editor.spans.map(s => <option key={s.index} value={s.index}>{s.index + 1} · {s.side === 'left' ? '左' : '右'}側 {s.start}–{s.end}m</option>)}
      </select></label>
      <label>外側へ移動 <output>{selected.offsetMeters.toFixed(2)} m</output><input aria-label="縁石の横位置" type="range" min="-0.5" max="0.5" step="0.01" value={selected.offsetMeters} onChange={event => update({ offsetMeters: Number(event.target.value) })}/></label>
      <label>幅 <output>{Math.round(selected.widthScale*100)}%</output><input aria-label="縁石の幅" type="range" min="0.7" max="1.3" step="0.01" value={selected.widthScale} onChange={event => update({ widthScale: Number(event.target.value) })}/></label>
      <div className="showcase-actions"><button onClick={() => { editor.reset(); setChanges({}); setMessage('元の形状へ戻しました'); }}>全区間をリセット</button><button onClick={download}>調整JSONを保存</button></div>
      <small>実画像との照合用です。試調整値は未検証で、保存したJSONを制作工程へ引き継げます。</small>
    </fieldset>}
    <small role="status">{message}</small>
  </div>;
}
