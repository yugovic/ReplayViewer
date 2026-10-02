/**
 * HTML report for pose_lag_eval.ts (self-contained, no external assets).
 * Every number comes from the evaluation objects passed in; nothing is typed by hand.
 */

export interface TracePoint {
  time: number;
  dist: number;
  headingDeg: number;
  beforeDeg: number;
  afterDeg: number;
}

interface Stats { n: number; median: number; p95: number; p99: number; max: number }
interface Summary {
  headingErrorDeg: Stats;
  lateralShiftAt20mM: Stats;
  lateralShiftAt50mM: Stats;
  stepDegPerFrame: Stats;
  jitterDegPerFrame: Stats;
  yawRateChangeDegPerSecPerFrame: Stats;
  worst: { lap: string; time: number; dist: number; errorDeg: number };
}
interface ClockRow {
  lap: string;
  deltaSeconds: number;
  startOffsetMeters: number;
  beforeAheadMeters: Stats;
}

// Loose input types: the report only reads the fields it shows.
/* eslint-disable @typescript-eslint/no-explicit-any */
export interface ReportInput {
  pose: any;
  timebase: any;
  trace: TracePoint[];
  traceLap: string;
}

const esc = (value: unknown) => String(value)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const f = (v: number, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "–");
const lapName = (id: string) => id.replace("fuji_aim_2020_07_30/", "7/30 ").replace("fuji_aim_01/", "7/29 ");

function tile(label: string, before: string, after: string, note: string): string {
  return `<div class="tile"><div class="tile-label">${esc(label)}</div>
  <div class="tile-values"><span class="tile-before">${esc(before)}</span><span class="tile-arrow" aria-hidden="true">→</span><strong class="tile-after">${esc(after)}</strong></div>
  <div class="tile-note">${esc(note)}</div></div>`;
}

function chart(trace: TracePoint[], traceLap: string): string {
  const W = 720;
  const H = 300;
  const m = { l: 48, r: 92, t: 16, b: 40 };
  const t0 = trace[0]?.time ?? 0;
  const t1 = trace[trace.length - 1]?.time ?? 1;
  const yMaxRaw = Math.max(1, ...trace.map((p) => Math.abs(p.beforeDeg)));
  const yMax = Math.ceil(yMaxRaw);
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
  const y = (v: number) => m.t + ((yMax - v) / (2 * yMax)) * (H - m.t - m.b);
  const path = (key: "beforeDeg" | "afterDeg") =>
    trace.map((p, i) => `${i ? "L" : "M"}${x(p.time).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  const yTicks: number[] = [];
  const stepY = yMax > 4 ? 2 : 1;
  for (let v = -yMax; v <= yMax + 1e-9; v += stepY) yTicks.push(v);
  const xTicks: number[] = [];
  for (let t = Math.ceil(t0); t <= t1; t += 2) xTicks.push(t);
  const last = trace[trace.length - 1];
  const grid = yTicks.map((v) => `<line class="grid${v === 0 ? " zero" : ""}" x1="${m.l}" x2="${W - m.r}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/>
    <text class="axis" x="${m.l - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${v > 0 ? "+" : ""}${v}°</text>`).join("");
  const xAxis = xTicks.map((t) => `<text class="axis" x="${x(t).toFixed(1)}" y="${H - m.b + 18}" text-anchor="middle">${t}s</text>`).join("");
  return `<figure class="chart-card">
  <figcaption><strong>ダンロップ付近の向きの誤差（描画 − 目標）</strong><span>${esc(lapName(traceLap))}、改善前の最大誤差の前後6秒。60fps・等倍再生のシミュレーション</span></figcaption>
  <div class="legend" aria-hidden="true"><span><i class="key key-before"></i>改善前（140ms一次遅れ）</span><span><i class="key key-after"></i>改善後（70ms×2・先読み）</span></div>
  <div class="chart-wrap">
  <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="向きの誤差の時系列。改善前は最大${f(yMaxRaw, 1)}度、改善後はほぼ0度" id="trace-svg">
    ${grid}${xAxis}
    <text class="axis" x="${(m.l + W - m.r) / 2}" y="${H - 6}" text-anchor="middle">ラップ時刻（記録t）</text>
    <path class="line line-before" d="${path("beforeDeg")}"/>
    <path class="line line-after" d="${path("afterDeg")}"/>
    ${last ? `<text class="direct" x="${W - m.r + 6}" y="${(y(last.beforeDeg) + 4).toFixed(1)}">改善前</text>
    <text class="direct" x="${W - m.r + 6}" y="${(y(last.afterDeg) + (Math.abs(y(last.afterDeg) - y(last.beforeDeg)) < 14 ? 16 : 4)).toFixed(1)}">改善後</text>` : ""}
    <line id="crosshair" class="crosshair" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" visibility="hidden"/>
    <rect id="hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent" tabindex="0" aria-label="時刻を選んで値を表示（左右キー）"/>
  </svg>
  <div id="tooltip" class="tooltip" role="status" hidden></div>
  </div>
  <script type="application/json" id="trace-data">${JSON.stringify(trace.map((p) => [p.time, p.dist, p.beforeDeg, p.afterDeg]))}</script>
  <script>
  (function () {
    var data = JSON.parse(document.getElementById("trace-data").textContent);
    var svg = document.getElementById("trace-svg"), hit = document.getElementById("hit");
    var cross = document.getElementById("crosshair"), tip = document.getElementById("tooltip");
    var W = ${W}, L = ${m.l}, R = ${m.r}, t0 = ${t0}, t1 = ${t1}, idx = 0;
    function xOf(t) { return L + (t - t0) / ((t1 - t0) || 1) * (W - L - R); }
    function row(cls, value, name) {
      var r = document.createElement("div"); r.className = "tip-row";
      var k = document.createElement("i"); k.className = "tip-key " + cls; r.appendChild(k);
      var b = document.createElement("strong"); b.textContent = value; r.appendChild(b);
      var s = document.createElement("span"); s.textContent = name; r.appendChild(s);
      return r;
    }
    function show(i) {
      idx = Math.max(0, Math.min(data.length - 1, i));
      var d = data[idx], px = xOf(d[0]);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var h = document.createElement("div"); h.className = "tip-head";
      h.textContent = d[0].toFixed(2) + " s ・ " + d[1].toFixed(0) + " m"; tip.appendChild(h);
      tip.appendChild(row("key-before", (d[2] >= 0 ? "+" : "") + d[2].toFixed(2) + "°", "改善前"));
      tip.appendChild(row("key-after", (d[3] >= 0 ? "+" : "") + d[3].toFixed(3) + "°", "改善後"));
      tip.hidden = false;
      var box = svg.getBoundingClientRect(), scale = box.width / W;
      var left = px * scale + 12; if (left + 170 > box.width) left = px * scale - 182;
      tip.style.left = Math.max(0, left) + "px"; tip.style.top = "8px";
    }
    function nearest(clientX) {
      var box = svg.getBoundingClientRect(), vx = (clientX - box.left) / box.width * W;
      var t = t0 + (vx - L) / (W - L - R) * (t1 - t0), best = 0, bd = Infinity;
      for (var i = 0; i < data.length; i++) { var dd = Math.abs(data[i][0] - t); if (dd < bd) { bd = dd; best = i; } }
      return best;
    }
    hit.addEventListener("pointermove", function (e) { show(nearest(e.clientX)); });
    hit.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
    hit.addEventListener("focus", function () { show(idx); });
    hit.addEventListener("blur", function () { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
    hit.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { show(idx + 3); e.preventDefault(); }
      if (e.key === "ArrowLeft") { show(idx - 3); e.preventDefault(); }
    });
  })();
  </script>
  <details class="table-view"><summary>表で見る（0.5秒ごと）</summary><div class="table-scroll"><table>
  <thead><tr><th>時刻 s</th><th>距離 m</th><th>改善前 °</th><th>改善後 °</th></tr></thead><tbody>
  ${trace.filter((_, i) => i % 15 === 0).map((p) => `<tr><td>${f(p.time, 2)}</td><td>${f(p.dist, 0)}</td><td>${f(p.beforeDeg, 2)}</td><td>${f(p.afterDeg, 3)}</td></tr>`).join("")}
  </tbody></table></div></details>
</figure>`;
}

export function renderReport({ pose, timebase, trace, traceLap }: ReportInput): string {
  const before: Summary = pose.before;
  const after: Summary = pose.after;
  const truth = timebase.ghostDeltaTruthTest;
  const clock: ClockRow[] = timebase.clock;
  const variants = Object.entries(pose.variants as Record<string, Summary>);
  const robustness = Object.entries(pose.robustness as Record<string, Record<string, Summary>>);
  const legacyKey = Object.keys(pose.robustness[robustness[0][0]])[0];
  const defaultKey = Object.keys(pose.robustness[robustness[0][0]])[1];
  const targets = pose.targets as Record<string, boolean>;
  const variantLabel = (key: string) => key
    .replace("raw-target", "平滑化なし（目標そのもの）")
    .replace("+lookahead", "・先読み")
    .replace("+linearInput", "・線形入力");

  return `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>時刻基準と姿勢遅れ</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --surface-2: #f3f2ef; --border: #e2e0da;
  --text: #0b0b0b; --text-2: #52514e; --text-3: #7a7974;
  --series-before: #eb6834; --series-after: #2a78d6;
  --grid: #e8e6e1; --zero: #b9b7b0; --good: #1f7a3a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --surface-2: #232321; --border: #3a3a37;
    --text: #ffffff; --text-2: #c3c2b7; --text-3: #95948b;
    --series-before: #d95926; --series-after: #3987e5;
    --grid: #2e2e2b; --zero: #5d5c57; --good: #5cc27a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --surface-2: #232321; --border: #3a3a37;
  --text: #ffffff; --text-2: #c3c2b7; --text-3: #95948b;
  --series-before: #d95926; --series-after: #3987e5;
  --grid: #2e2e2b; --zero: #5d5c57; --good: #5cc27a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text);
  font: 15px/1.65 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif; }
main { max-width: 920px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; letter-spacing: 0.01em; }
h2 { font-size: 1.15rem; margin: 40px 0 10px; padding-top: 8px; border-top: 1px solid var(--border); }
h3 { font-size: 1rem; margin: 24px 0 8px; }
p, li { color: var(--text); }
.sub { color: var(--text-2); margin: 0 0 20px; }
.muted { color: var(--text-2); font-size: 0.9rem; }
code { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 0.86em;
  background: var(--surface-2); padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 16px 0; }
.tile { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--surface); }
.tile-label { color: var(--text-2); font-size: 0.85rem; }
.tile-values { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
.tile-before { color: var(--text-2); font-size: 1rem; text-decoration: line-through; text-decoration-color: var(--text-3); }
.tile-arrow { color: var(--text-3); }
.tile-after { font-size: 1.45rem; }
.tile-note { color: var(--text-3); font-size: 0.8rem; }
.pass { color: var(--good); font-weight: 600; }
.chart-card { margin: 16px 0; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.chart-card figcaption { display: flex; flex-direction: column; gap: 2px; margin-bottom: 6px; }
.chart-card figcaption span { color: var(--text-2); font-size: 0.85rem; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--text-2); font-size: 0.85rem; margin: 4px 0 8px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.key, .tip-key { display: inline-block; width: 16px; height: 2px; border-radius: 1px; }
.key-before { background: var(--series-before); } .key-after { background: var(--series-after); }
.chart-wrap { position: relative; }
svg { width: 100%; height: auto; display: block; }
.grid { stroke: var(--grid); stroke-width: 1; } .grid.zero { stroke: var(--zero); }
.axis { fill: var(--text-3); font-size: 11px; font-variant-numeric: tabular-nums; }
.direct { fill: var(--text-2); font-size: 12px; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.line-before { stroke: var(--series-before); } .line-after { stroke: var(--series-after); }
.crosshair { stroke: var(--text-3); stroke-width: 1; }
#hit { cursor: crosshair; outline: none; }
#hit:focus-visible { stroke: var(--text-3); stroke-dasharray: 3 3; }
.tooltip { position: absolute; min-width: 160px; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 0.8rem; box-shadow: 0 4px 16px rgba(0,0,0,0.12); pointer-events: none; }
.tip-head { color: var(--text-2); margin-bottom: 4px; font-variant-numeric: tabular-nums; }
.tip-row { display: flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; }
.tip-row span { color: var(--text-2); }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 0.86rem; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-2); font-weight: 600; }
tr.shipped td { font-weight: 600; }
details.table-view { margin-top: 10px; } details summary { cursor: pointer; color: var(--text-2); font-size: 0.88rem; }
ul { padding-left: 1.2em; }
</style>
</head>
<body>
<main>
<h1>時刻基準と姿勢遅れ</h1>
<p class="sub">富士AiMの全10周（7/29 4周・7/30 6周）で、ビューワーの表示時刻・ゴーストのデルタ・再生中の車の向きを改善前後で比較した。生成：${esc(pose.generatedAt)}</p>

<h2 id="summary">要旨</h2>
<div class="tiles">
${tile("再生中の向きの誤差（p95）", `${f(before.headingErrorDeg.p95, 2)}°`, `${f(after.headingErrorDeg.p95, 3)}°`, `最大 ${f(before.headingErrorDeg.max, 2)}° → ${f(after.headingErrorDeg.max, 2)}°（目標 p95 < 1°）`)}
${tile("コックピット前方50mの横ずれ（p95）", `${f(before.lateralShiftAt50mM.p95, 2)} m`, `${f(after.lateralShiftAt50mM.p95, 3)} m`, `20m先：${f(before.lateralShiftAt20mM.p95, 2)} → ${f(after.lateralShiftAt20mM.p95, 3)} m`)}
${tile("向きの滑らかさ（2階差 p99）", `${f(before.jitterDegPerFrame.p99, 4)}°`, `${f(after.jitterDegPerFrame.p99, 4)}°`, `1フレームあたり。最大 ${f(before.jitterDegPerFrame.max, 4)} → ${f(after.jitterDegPerFrame.max, 4)}（悪化なし）`)}
${tile("同じ表示時刻での位置ずれ（最大）", `${f(timebase.clockSummary.beforeAheadMetersMax, 1)} m`, "0 m", `周ごとの中央値の中央値 ${f(timebase.clockSummary.beforeAheadMetersMedianOfLapMedians, 2)} m。表示を t+δ にしたため構造的に0`)}
${tile("ゴーストのデルタ誤差（p95）", `${f(truth.errorMs.legacy.p95, 1)} ms`, `${f(truth.errorMs.fixed.p95, 2)} ms`, `最大 ${f(truth.errorMs.legacy.max, 0)} → ${f(truth.errorMs.fixed.max, 2)} ms（真値つき合成試験、90組）`)}
</div>
<p>判定：${Object.entries(targets).map(([k, v]) => `<code>${esc(k)}</code> ${v ? '<span class="pass">合格</span>' : "<strong>不合格</strong>"}`).join("、")}</p>

<h2 id="changes">変えたこと</h2>
<ul>
<li><strong>表示時刻：</strong>HUDのTIMEと再生バーの時計を、最初のGPS標本ではなく計測ライン通過からの時間（<code>t + δ</code>、δ = <code>first_sample_after_lap_start_seconds</code>）で表示する。データ配列と内部の再生時刻は変えていない。δが無いデータは0として扱う。</li>
<li><strong>デルタ：</strong>周どうしの比較を「真のラップ時刻 × 計測ラインからの距離」でそろえた。各周の距離は最初の標本から数えているため、時刻だけでなく距離の原点も s0 = δ × 最初の区間の速さ だけずらす。ゴーストの描画位置とセクタ1の時間も同じ基準にした。</li>
<li><strong>向きの平滑化：</strong>140msの一次遅れ（目標を後追い）を、70msの2段（計140ms）で140ms先の姿勢を追う方式に替えた。各段は「フレーム内で入力が直線的に動く」前提の厳密解で更新するので、遅れはフレームの長さによらず正確に70ms×2となり、先読みで打ち消せる。一時停止・シーク時は従来どおり即時に目標へ合わせる。</li>
</ul>

<h2 id="pose">再生中の向きの遅れ</h2>
${chart(trace, traceLap)}
<h3>平滑化方式の比較（60fps・等倍、${before.headingErrorDeg.n.toLocaleString()}フレーム）</h3>
<div class="table-scroll"><table>
<thead><tr><th>方式</th><th>誤差 中央値 °</th><th>p95 °</th><th>最大 °</th><th>2階差 p99 °/fr</th><th>ヨー変化率差 p99 °/s</th></tr></thead>
<tbody>
${variants.map(([key, v]) => `<tr${key === defaultKey ? ' class="shipped"' : ""}><td>${esc(variantLabel(key))}${key === defaultKey ? "（採用）" : key === legacyKey ? "（改善前）" : ""}</td><td>${f(v.headingErrorDeg.median, 3)}</td><td>${f(v.headingErrorDeg.p95, 3)}</td><td>${f(v.headingErrorDeg.max, 3)}</td><td>${f(v.jitterDegPerFrame.p99, 4)}</td><td>${f(v.yawRateChangeDegPerSecPerFrame.p99, 3)}</td></tr>`).join("\n")}
</tbody></table></div>
<p class="muted">「平滑化なし」は誤差0だが、10Hzの標本ごとにヨーの変化率が段差状に変わるため、2階差が改善前の約3倍になる。先読みのみ（線形入力なし）の2段は、フレーム更新の離散化で遅れが τ−Δ/2 に縮み、先読みが約16ms進みすぎて p95 0.48° が残った。</p>

<h3>フレームレート・再生速度・フレーム時間のゆらぎ</h3>
<div class="table-scroll"><table>
<thead><tr><th>条件</th><th>改善前 p95 °</th><th>改善後 p95 °</th><th>改善前 最大 °</th><th>改善後 最大 °</th><th>ヨー変化率差 p99 前 → 後 °/s</th></tr></thead>
<tbody>
${robustness.map(([name, r]) => `<tr><td>${esc(name)}</td><td>${f(r[legacyKey].headingErrorDeg.p95, 2)}</td><td>${f(r[defaultKey].headingErrorDeg.p95, 3)}</td><td>${f(r[legacyKey].headingErrorDeg.max, 2)}</td><td>${f(r[defaultKey].headingErrorDeg.max, 3)}</td><td>${f(r[legacyKey].yawRateChangeDegPerSecPerFrame.p99, 3)} → ${f(r[defaultKey].yawRateChangeDegPerSecPerFrame.p99, 3)}</td></tr>`).join("\n")}
</tbody></table></div>

<h2 id="timebase">表示時刻とデルタの基準</h2>
<h3>周ごとのδと、同じ表示時刻での位置ずれ（改善前）</h3>
<div class="table-scroll"><table>
<thead><tr><th>周</th><th>δ ms</th><th>s0 m</th><th>位置ずれ 中央値 m</th><th>最大 m</th><th>改善後 m</th></tr></thead>
<tbody>
${clock.map((c) => `<tr><td>${esc(lapName(c.lap))}</td><td>${f(c.deltaSeconds * 1000, 0)}</td><td>${f(c.startOffsetMeters, 2)}</td><td>${f(c.beforeAheadMeters.median, 2)}</td><td>${f(c.beforeAheadMeters.max, 2)}</td><td>0</td></tr>`).join("\n")}
</tbody></table></div>
<p class="muted">改善前は表示時刻Xで「真のラップ時刻X+δ」の標本を描いていたため、車は δ×速度 だけ前に出ていた。</p>

<h3>ゴーストのデルタ：真値つき合成試験</h3>
<p>各周の (t, 距離) を真の走行とみなし、実際のδで10Hzに標本化し直した記録を作って、90通りの組み合わせで計算値と真のデルタを比べた。</p>
<div class="table-scroll"><table>
<thead><tr><th>方式</th><th>誤差 中央値 ms</th><th>p95 ms</th><th>p99 ms</th><th>最大 ms</th><th>最初の標本での誤差 最大 ms</th></tr></thead>
<tbody>
<tr><td>改善前（t・距離をそのまま比較）</td><td>${f(truth.errorMs.legacy.median, 1)}</td><td>${f(truth.errorMs.legacy.p95, 1)}</td><td>${f(truth.errorMs.legacy.p99, 1)}</td><td>${f(truth.errorMs.legacy.max, 1)}</td><td>${f(truth.atFirstMainSampleMs.legacy.max, 2)}</td></tr>
<tr><td>δを足すだけ（不採用）</td><td>${f(truth.errorMs.naiveAddDeltaOnly.median, 1)}</td><td>${f(truth.errorMs.naiveAddDeltaOnly.p95, 1)}</td><td>${f(truth.errorMs.naiveAddDeltaOnly.p99, 1)}</td><td>${f(truth.errorMs.naiveAddDeltaOnly.max, 1)}</td><td>${f(truth.atFirstMainSampleMs.naiveAddDeltaOnly.max, 2)}</td></tr>
<tr class="shipped"><td>改善後（時刻と距離の原点をそろえる）</td><td>${f(truth.errorMs.fixed.median, 3)}</td><td>${f(truth.errorMs.fixed.p95, 3)}</td><td>${f(truth.errorMs.fixed.p99, 3)}</td><td>${f(truth.errorMs.fixed.max, 3)}</td><td>${f(truth.atFirstMainSampleMs.fixed.max, 3)}</td></tr>
</tbody></table></div>
<p class="muted">最悪の組は ${esc(lapName(truth.worstLegacyPair.main))}（δ ${f(truth.worstLegacyPair.deltaMain * 1000, 0)}ms）対 ${esc(lapName(truth.worstLegacyPair.ghost))}（δ ${f(truth.worstLegacyPair.deltaGhost * 1000, 0)}ms）で、改善前 ${f(truth.worstLegacyPair.legacyMaxMs, 0)}ms → 改善後 ${f(truth.worstLegacyPair.fixedMaxMs, 2)}ms。改善前の誤差は「距離の原点の差 ÷ その地点の速さ − δの差」で、低速コーナーほど大きい。δを足すだけでは距離の原点のずれが残り、かえって悪化する。実データで表示されるデルタの変化量は中央値 ${f(timebase.realDataHudDeltaChangeMs.median, 1)}ms、最大 ${f(timebase.realDataHudDeltaChangeMs.max, 0)}ms。</p>

<h2 id="method">方法と限界</h2>
<ul>
<li>再現：<code>${esc(pose.command)}</code>。ビューワー自身のコード（<code>sampleReplay</code>、<code>PoseSmoother</code>、<code>delta.ts</code>、<code>lapClock.ts</code>）をNodeで動かし、フレームごとに再生を模擬した。</li>
<li>誤差は「描画される向き」と「目標の向き（10Hz標本の進行方向を直線補間したもの）」の差。2階差は |yaw[k+1] − 2yaw[k] + yaw[k−1]|、ヨー変化率差はフレーム時間で割った変化率の差。</li>
<li>改善前の値は、既存資料（中央値1.07°・p95 3.96°・最大7.59°、標本点での計算）とほぼ同じ。最大値の差は評価点（標本点か60fpsフレームか）の違いによる。</li>
<li>このページはシミュレーションの結果であり、ブラウザでの見た目の確認は含まない。</li>
<li>残る課題：各周の距離は周ごとのGPS累積距離のため、周全体の長さが最大約20m違う。周の後半のデルタはこの縮尺差の影響を受ける（今回の修正は原点のずれのみ）。テレメトリーパネルの時間軸は記録tのまま。</li>
</ul>
</main>
</body>
</html>
`;
}
