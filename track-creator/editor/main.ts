/**
 * track-creator editor — 幅・縁石をブラウザ上で編集する。
 *   npm run edit   →  http://localhost:5174/editor/?track=fuji
 *
 * 実データ(走行ライン refs.json)と地理院航空写真をオーバーレイして、
 * 客観的な情報を見ながらコース幅・縁石をドラッグで調整 → 保存 → GLBビルド。
 */

import * as THREE from "three";
import { EditorScene } from "./scene.ts";
import { TrackModel } from "./model.ts";
import { EditTools } from "./tools.ts";
import type { Mode } from "./tools.ts";
import { GsiPhotoOverlay, AnalysisImageOverlay, buildRefLines } from "./overlay.ts";
import { AnalysisController } from "./analysisController.ts";
import type { AnalysisState } from "./analysisController.ts";

const $ = (sel: string) => document.querySelector(sel) as HTMLElement;

const params = new URLSearchParams(location.search);
const trackName = params.get("track") ?? "demo";

// ── panel skeleton ────────────────────────────────────────────────────────────

$("#panel").innerHTML = `
  <h1>🏁 <span id="title">${trackName}</span> <span class="dirty" id="dirty"></span></h1>

  <section>
    <h2>トラック / 表示</h2>
    <div class="row">
      <select id="trackSel"></select>
      <span class="grow"></span>
      <button id="viewTop" class="active">上面</button>
      <button id="viewOrbit">3D</button>
    </div>
    <div class="row" style="margin-top:6px">
      <span class="muted" id="stats"></span>
    </div>
  </section>

  <section>
    <h2>編集モード</h2>
    <div class="row">
      <button id="modePan" class="active">パン</button>
      <button id="modeWidth">コース幅</button>
      <button id="modeCurb">縁石</button>
      <button id="modeDraw">なぞり描き</button>
      <span class="grow"></span>
      <button id="undoBtn" class="small" title="Ctrl+Z">↶ Undo</button>
    </div>
  </section>

  <section>
    <h2>コース幅(エッジ編集)</h2>
    <div class="row" style="margin-bottom:6px">
      <span class="muted">基本幅</span>
      <input type="number" id="baseWidth" step="0.5" min="4" max="40" /> <span class="muted">m</span>
      <span class="grow"></span>
      <span class="muted">点間隔</span>
      <select id="edgeStep">
        <option value="1">1 m</option>
        <option value="2" selected>2 m</option>
        <option value="5">5 m</option>
        <option value="10">10 m</option>
      </select>
    </div>
    <div class="row" style="margin-bottom:6px">
      <span class="muted">ブラシ半径</span>
      <input type="range" id="brush" min="0" max="60" value="20" class="grow" />
      <span class="muted" id="brushVal">20 m</span>
    </div>
    <div class="row">
      <label class="chk"><input type="checkbox" id="symmetric" /> 左右対称に編集</label>
      <span class="grow"></span>
      <button id="edgeReset" class="small danger">エッジをリセット</button>
    </div>
    <div style="margin-top:8px" title="路面を2クリック(始点→終点)すると片側のエッジ区間が黄色く選択されます。Escで解除">
      <span class="muted">区間操作(路面を2クリックで選択)</span>
      <div class="row" style="margin-top:4px">
        <button id="edgeStraighten" class="small" disabled>直線化</button>
        <button id="edgeSmooth" class="small" disabled>平滑化</button>
        <button id="edgeBaseWidth" class="small" disabled>基本幅に</button>
        <button id="edgeRangeClear" class="small" disabled>解除</button>
      </div>
      <div class="muted" style="margin-top:4px;font-size:11px">
        点の削除 = エッジ点の上で右クリック(または Alt+クリック) / Del=選択区間の点を削除 / ドラッグ=点を移動
      </div>
    </div>
    <div class="muted" id="edgeInfo" style="margin-top:6px"></div>
  </section>

  <section>
    <h2>縁石 <span class="muted">(路面2クリックで追加)</span></h2>
    <div class="list" id="curbList"></div>
  </section>

  <section id="analysisSec">
    <h2>画像から推定</h2>
    <div class="row" style="margin-bottom:6px">
      <button id="anaLoad">画像を読み込む</button>
      <select id="anaSource" title="解析に使う画像。高解像度(Bing z19, 0.24m/px)があるトラックではそちらを推奨">
        <option value="satellite">標準 (Esri)</option>
        <option value="satellite_bing">高解像度 (Bing)</option>
      </select>
      <span class="muted" id="anaRes"></span>
    </div>
    <div class="row" style="margin-bottom:6px">
      <span class="muted">最小幅</span>
      <input type="number" id="anaMinOff" value="3" step="0.5" min="1" max="10" style="width:48px" />
      <span class="muted">最大幅</span>
      <input type="number" id="anaMaxOff" value="20" step="1" min="10" max="30" style="width:48px" />
      <span class="muted">m</span>
    </div>
    <label class="chk" style="margin-bottom:6px" title="白線(ペイントされたコース端の線)が見える場所ではアスファルト境界より白線を優先する。ピットレーンや舗装ランオフを走行面から除外できる">
      <input type="checkbox" id="anaStripe" /> 白線優先(走行面の線で幅を決める)
    </label>
    <div style="margin-bottom:6px" title="ピットレーンや観客席が近い区間で、片側の探索幅に上限をかける。1行 = 開始m,終了m,左|右,最大m。開始>終了はスタートラインをまたぐ範囲">
      <span class="muted">制限区間(1行: 開始,終了,左|右,最大m)</span>
      <textarea id="anaCorridors" rows="2" style="width:100%;resize:vertical;font-family:monospace"
        placeholder="例: 4224,598,右,9&#10;    4224,598,左,12"></textarea>
    </div>
    <div class="row" style="margin-bottom:6px">
      <span class="muted">信頼度しきい値</span>
      <input type="range" id="anaThresh" min="30" max="90" value="60" class="grow" />
      <span class="muted" id="anaThreshVal">0.60</span>
    </div>
    <div class="row" style="margin-bottom:6px">
      <button id="anaRun" class="primary" disabled>解析開始</button>
      <button id="anaCancel" disabled>キャンセル</button>
    </div>
    <div id="anaResult" style="margin-bottom:6px"></div>
    <div class="row" id="anaApplyRow" style="display:none">
      <button id="anaApplyHigh" class="primary">高信頼のみ適用</button>
      <button id="anaApplyAll">全適用</button>
      <button id="anaDiscard" class="danger">破棄</button>
    </div>
    <div class="muted" id="anaWarn" style="margin-top:4px"></div>
  </section>

  <section id="overlaySec">
    <h2>地図オーバーレイ(航空写真)</h2>
    <label class="chk"><input type="checkbox" id="ovShow" /> 表示する</label>
    <div class="row" style="margin-top:4px" title="解析画像 = 「画像から推定」で選んだソースそのもの(判定はこれで)。地理院 = 従来のGSIタイル">
      <span class="muted">画像</span>
      <select id="ovSource" class="grow">
        <option value="analysis">解析画像(推定ソースと連動)</option>
        <option value="gsi">地理院タイル</option>
      </select>
    </div>
    <div class="row" style="margin-top:4px">
      <span class="muted">不透明度</span>
      <input type="range" id="ovOpacity" min="10" max="100" value="85" class="grow" />
    </div>
    <label class="chk" style="margin-top:4px">
      <input type="checkbox" id="ovTop" /> 路面より手前に表示(トレース用)
    </label>
  </section>

  <section id="refsSec" style="display:none">
    <h2>走行データ(参照ライン)</h2>
    <div class="list" id="refsList"></div>
  </section>

  <section>
    <div class="row">
      <button id="saveBtn" class="primary">保存</button>
      <button id="buildBtn" class="primary">保存してGLBビルド</button>
      <button id="dlBtn">JSON</button>
    </div>
  </section>

  <div id="status"></div>
`;

const statusEl = $("#status");
function setStatus(msg: string) {
  statusEl.textContent = msg;
}

// ── scene + model ─────────────────────────────────────────────────────────────

const scene = new EditorScene($("#view"));
const model = new TrackModel();
const tools = new EditTools(scene, model);
const analysis = new AnalysisController(model, scene);
scene.scene.add(model.group, tools.handles);

function updateTitle() {
  $("#dirty").textContent = model.dirty ? "● 未保存" : "";
  const edges = model.def.road.edges;
  $("#stats").textContent =
    `${model.track.totalLength.toFixed(0)} m / ${model.track.stations.length} st / ` +
    `エッジ ${edges ? `${edges.left.length}点×2 (${edges.step}m)` : "未編集"} / 縁石 ${(model.def.curbs ?? []).length}`;
}

function updateEdgeSection() {
  const edges = model.def.road.edges;
  const baseWidth = $("#baseWidth") as HTMLInputElement;
  baseWidth.disabled = !!edges;
  baseWidth.title = edges ? "エッジ編集済みのため無効(リセットで戻る)" : "";
  if (!edges) baseWidth.value = String(model.def.road.width);
  const stepSel = $("#edgeStep") as HTMLSelectElement;
  if (edges) stepSel.value = String(edges.step);
  $("#edgeInfo").textContent = edges
    ? `エッジ点 ${edges.left.length} × 2(${edges.step} m 間隔)を直接ドラッグできます`
    : "「コース幅」モードでエッジ点をドラッグすると自由変形になります";
}

// ── lists ─────────────────────────────────────────────────────────────────────

function numInput(value: number, step: number, onchange: (v: number) => void): HTMLInputElement {
  const inp = document.createElement("input");
  inp.type = "number";
  inp.step = String(step);
  inp.value = String(value);
  inp.addEventListener("click", (e) => e.stopPropagation());
  inp.addEventListener("change", () => {
    const v = Number(inp.value);
    if (Number.isFinite(v)) onchange(v);
  });
  return inp;
}

function commitEdit(mutate: () => void) {
  model.snapshot();
  mutate();
  model.rebuild();
  tools.sync();
  renderLists();
}

function renderLists() {
  const curbs = model.def.curbs ?? [];
  const curbList = $("#curbList");
  curbList.innerHTML = "";
  curbs.forEach((span, i) => {
    const row = document.createElement("div");
    row.className = "item" + (tools.selection?.kind === "curb" && tools.selection.index === i ? " selected" : "");
    row.addEventListener("click", () => tools.select({ kind: "curb", index: i }));
    const tag = document.createElement("span");
    tag.className = `tag ${span.side === "right" ? "R" : "L"}`;
    tag.textContent = span.side === "right" ? "R" : "L";
    tag.title = "クリックで左右反転";
    tag.addEventListener("click", (e) => {
      e.stopPropagation();
      commitEdit(() => (span.side = span.side === "right" ? "left" : "right"));
    });
    const startInp = numInput(span.startDist, 1, (v) =>
      commitEdit(() => (span.startDist = Math.min(v, span.endDist - 2))),
    );
    const dash = document.createElement("span");
    dash.className = "muted";
    dash.textContent = "–";
    const endInp = numInput(span.endDist, 1, (v) =>
      commitEdit(() => (span.endDist = Math.max(v, span.startDist + 2))),
    );
    const wLabel = document.createElement("span");
    wLabel.className = "muted";
    wLabel.textContent = "幅";
    const wInp = numInput(span.width, 0.1, (v) =>
      commitEdit(() => (span.width = Math.min(Math.max(v, 0.3), 5))),
    );
    const del = document.createElement("button");
    del.className = "small danger";
    del.textContent = "✕";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      tools.select({ kind: "curb", index: i });
      tools.deleteSelected();
    });
    row.append(tag, startInp, dash, endInp, wLabel, wInp, del);
    curbList.append(row);
  });
  if (curbs.length === 0) {
    curbList.innerHTML = `<span class="muted">縁石なし。「縁石」モードで路面を2回クリック(開始→終了)。</span>`;
  }

  updateEdgeSection();
  updateTitle();
}

// ── mode / view buttons ───────────────────────────────────────────────────────

const HINTS: Record<Mode, string> = {
  pan: "ドラッグ: 移動 / ホイール: ズーム",
  width: "エッジ点(緑=左 / 桃=右)を横にドラッグ: ふちを自由変形。ブラシ半径で周辺も追従(0=その点のみ)",
  curb: "路面を2回クリック: 縁石追加(開始 → 終了)/ 端点・中央ハンドルをドラッグで移動、Escで取消",
  draw: "ドラッグで既存コースの一部をなぞる: なぞった区間だけ新しいパスに置き換わる（前後のコースは維持）",
};

function setMode(mode: Mode) {
  tools.setMode(mode);
  for (const [id, m] of [["#modePan", "pan"], ["#modeWidth", "width"], ["#modeCurb", "curb"], ["#modeDraw", "draw"]] as const) {
    $(id).classList.toggle("active", m === mode);
  }
  $("#hint").textContent = HINTS[mode];
}
$("#modePan").addEventListener("click", () => setMode("pan"));
$("#modeWidth").addEventListener("click", () => setMode("width"));
$("#modeCurb").addEventListener("click", () => setMode("curb"));
$("#modeDraw").addEventListener("click", () => setMode("draw"));

$("#viewTop").addEventListener("click", () => {
  scene.setViewMode("top");
  $("#viewTop").classList.add("active");
  $("#viewOrbit").classList.remove("active");
});
$("#viewOrbit").addEventListener("click", () => {
  scene.setViewMode("orbit");
  $("#viewOrbit").classList.add("active");
  $("#viewTop").classList.remove("active");
});

// ── actions ───────────────────────────────────────────────────────────────────

$("#undoBtn").addEventListener("click", () => doUndo());
function doUndo() {
  if (model.undo()) {
    tools.selection = null;
    tools.sync();
    renderLists();
    setStatus("元に戻しました");
  }
}

$("#saveBtn").addEventListener("click", async () => {
  try {
    await model.save();
    setStatus(`保存しました → tracks/${model.name}/track.json`);
    renderLists();
  } catch (e) {
    setStatus(`保存エラー: ${e instanceof Error ? e.message : e}`);
  }
});

$("#buildBtn").addEventListener("click", async () => {
  try {
    setStatus("保存してビルド中…");
    await model.save();
    const r = await model.build();
    setStatus(
      `ビルド完了 → ${r.glb} (${r.kb} KB, road ${r.roadTris} tris, curb ${r.curbTris} tris)\n` +
        `プレビュー: /preview/?glb=${model.name}`,
    );
    renderLists();
  } catch (e) {
    setStatus(`ビルドエラー: ${e instanceof Error ? e.message : e}`);
  }
});

$("#dlBtn").addEventListener("click", () => model.downloadJson());

addEventListener("keydown", (ev) => {
  if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) return;
  if (ev.key === "Escape") tools.cancelPending();
  else if (ev.key === "Delete" || ev.key === "Backspace") {
    if (tools.deleteSelected()) setStatus("削除しました");
  } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") {
    ev.preventDefault();
    doUndo();
  } else if (ev.key === "1") setMode("pan");
  else if (ev.key === "2") setMode("width");
  else if (ev.key === "3") setMode("curb");
});

// ── boot ──────────────────────────────────────────────────────────────────────

async function boot() {
  setStatus("読み込み中…");
  await model.load(trackName);

  // camera fit + grid
  const box = model.boundingBox();
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.x, size.z) * 0.55 + 50;
  scene.fit(center, radius);
  const grid = new THREE.GridHelper(radius * 5, 100, 0x2a3240, 0x1a212c);
  grid.position.set(center.x, box.min.y - 2, center.z);
  scene.scene.add(grid);

  // base width input (disabled once free-form edges exist)
  const baseWidth = $("#baseWidth") as HTMLInputElement;
  baseWidth.value = String(model.def.road.width);
  baseWidth.addEventListener("change", () => {
    const v = Number(baseWidth.value);
    if (Number.isFinite(v) && v >= 4) commitEdit(() => (model.def.road.width = v));
  });

  // edge editing controls
  const brush = $("#brush") as HTMLInputElement;
  brush.addEventListener("input", () => {
    tools.brushRadius = Number(brush.value);
    $("#brushVal").textContent = `${brush.value} m`;
  });
  ($("#symmetric") as HTMLInputElement).addEventListener("change", (e) => {
    tools.symmetric = (e.target as HTMLInputElement).checked;
  });
  const edgeStepSel = $("#edgeStep") as HTMLSelectElement;
  edgeStepSel.addEventListener("change", () => {
    model.setEdgeStep(Number(edgeStepSel.value));
    tools.updatePositions();
    renderLists();
    setStatus(`エッジ点間隔を ${edgeStepSel.value} m にしました`);
  });
  $("#edgeReset").addEventListener("click", () => {
    model.resetEdges();
    tools.updatePositions();
    renderLists();
    setStatus("エッジを基本幅にリセットしました");
  });

  // width-mode range operations (straighten / smooth / reset-to-base)
  {
    const buttons = ["#edgeStraighten", "#edgeSmooth", "#edgeBaseWidth", "#edgeRangeClear"]
      .map((sel) => $(sel) as HTMLButtonElement);
    tools.onRangeChange = (active) => {
      for (const b of buttons) b.disabled = !active;
    };
    const run = (op: "straighten" | "smooth" | "reset", label: string) => {
      if (!tools.applyRangeOp(op)) {
        setStatus("先に路面を2クリックして区間を選択してください");
        return;
      }
      renderLists();
      setStatus(`選択区間を${label}しました(区間は選択されたまま — 続けて操作できます)`);
    };
    ($("#edgeStraighten") as HTMLButtonElement).addEventListener("click", () => run("straighten", "直線化"));
    ($("#edgeSmooth") as HTMLButtonElement).addEventListener("click", () => run("smooth", "平滑化"));
    ($("#edgeBaseWidth") as HTMLButtonElement).addEventListener("click", () => run("reset", "基本幅に戻"));
    ($("#edgeRangeClear") as HTMLButtonElement).addEventListener("click", () => {
      tools.clearRange();
      setStatus("区間選択を解除しました");
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") tools.clearRange();
      // Delete = remove the selected points; the edge springs to the chord
      // between the samples just outside the selection (same as 直線化).
      if ((ev.key === "Delete" || ev.key === "Backspace") && tools.hasRange()) {
        const target = ev.target as HTMLElement | null;
        if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
        ev.preventDefault();
        run("straighten", "削除(両端を結ぶ線に張り替え)");
      }
    });
  }

  // Photo overlays (need a geographic origin): GSI tiles or the analysis image
  if (model.def.origin) {
    const overlay = new GsiPhotoOverlay(model.def.origin, box, box.min.y - 1.5, $("#attrib"));
    scene.scene.add(overlay.group);
    const analysisImg = new AnalysisImageOverlay(model.def.origin, box.min.y - 1.4);
    scene.scene.add(analysisImg.group);
    const show = $("#ovShow") as HTMLInputElement;
    const ovSource = $("#ovSource") as HTMLSelectElement;
    const opacity = $("#ovOpacity") as HTMLInputElement;
    const onTop = $("#ovTop") as HTMLInputElement;
    const apply = async () => {
      const useAnalysis = ovSource.value === "analysis";
      overlay.setOpacity(Number(opacity.value) / 100);
      overlay.setOnTop(onTop.checked);
      overlay.setVisible(show.checked && !useAnalysis);
      analysisImg.setOpacity(Number(opacity.value) / 100);
      analysisImg.setOnTop(onTop.checked);
      if (show.checked && useAnalysis) {
        // Follow the analysis-source selector so the backdrop is exactly what
        // the detector sees, at its native resolution.
        const base = (document.querySelector("#anaSource") as HTMLSelectElement).value;
        try {
          await analysisImg.setSource(
            `/data/tracks/${trackName}/${base}_meta.json`,
            `/data/tracks/${trackName}/${base}.jpg`,
          );
          analysisImg.setVisible(true);
        } catch (e) {
          setStatus(`解析画像を読み込めません: ${e instanceof Error ? e.message : e}`);
          analysisImg.setVisible(false);
          overlay.setVisible(show.checked);
        }
      } else {
        analysisImg.setVisible(false);
      }
    };
    show.addEventListener("change", apply);
    ovSource.addEventListener("change", apply);
    opacity.addEventListener("input", apply);
    onTop.addEventListener("change", apply);
    (document.querySelector("#anaSource") as HTMLSelectElement)
      .addEventListener("change", apply);

    // One-touch trace view: the floating 📷 button (or the T key) flips
    // 「路面より手前に表示」, turning the overlay on if it was hidden.
    const traceBtn = $("#traceToggle") as HTMLButtonElement;
    const syncTraceBtn = () =>
      traceBtn.classList.toggle("active", show.checked && onTop.checked);
    const toggleTrace = async () => {
      if (!show.checked) {
        show.checked = true;
        onTop.checked = true;
      } else {
        onTop.checked = !onTop.checked;
      }
      await apply();
      syncTraceBtn();
      setStatus(onTop.checked && show.checked
        ? "航空写真を路面より手前に表示中(もう一度 T で戻す)"
        : "航空写真を路面の下に戻しました");
    };
    traceBtn.addEventListener("click", toggleTrace);
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "t" && ev.key !== "T") return;
      const target = ev.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) return;
      toggleTrace();
    });
    show.addEventListener("change", syncTraceBtn);
    onTop.addEventListener("change", syncTraceBtn);
  } else {
    $("#overlaySec").innerHTML =
      `<h2>地図オーバーレイ</h2><span class="muted">track.json に origin (lat/lng) が無いため使えません。</span>`;
    ($("#traceToggle") as HTMLButtonElement).style.display = "none";
  }

  // reference lines (driven laps)
  if (model.refs && model.refs.lines.length > 0) {
    $("#refsSec").style.display = "";
    const { group, items } = buildRefLines(model.refs);
    scene.scene.add(group);
    const list = $("#refsList");
    for (const item of items) {
      const row = document.createElement("label");
      row.className = "chk item";
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.checked = true;
      chk.addEventListener("change", () => (item.object.visible = chk.checked));
      const chip = document.createElement("span");
      chip.className = "colorchip";
      chip.style.background = item.color;
      row.append(chk, chip, item.name);
      list.append(row);
    }
  }

  // ── image analysis section ────────────────────────────────────────────────
  const anaLoadBtn = $("#anaLoad") as HTMLButtonElement;
  const anaRunBtn = $("#anaRun") as HTMLButtonElement;
  const anaCancelBtn = $("#anaCancel") as HTMLButtonElement;
  const anaResEl = $("#anaRes");
  const anaResultEl = $("#anaResult");
  const anaWarnEl = $("#anaWarn");
  const anaApplyRow = $("#anaApplyRow");
  const anaApplyHigh = $("#anaApplyHigh") as HTMLButtonElement;
  const anaApplyAll = $("#anaApplyAll") as HTMLButtonElement;
  const anaDiscard = $("#anaDiscard") as HTMLButtonElement;
  const anaThresh = $("#anaThresh") as HTMLInputElement;
  const anaThreshVal = $("#anaThreshVal");
  const anaMinOff = $("#anaMinOff") as HTMLInputElement;
  const anaMaxOff = $("#anaMaxOff") as HTMLInputElement;
  const anaCorridors = $("#anaCorridors") as HTMLTextAreaElement;
  const anaStripe = $("#anaStripe") as HTMLInputElement;

  {
    const corridors = model.def.analysisHints?.corridors ?? [];
    anaCorridors.value = corridors
      .map((c) => `${c.startDist},${c.endDist},${c.side === "left" ? "左" : "右"},${c.maxOffset}`)
      .join("\n");
    anaStripe.checked = model.def.analysisHints?.objective === "stripeFirst";
  }

  /** Parse "start,end,左|右,max" lines; invalid lines are reported, not dropped silently. */
  function parseCorridors(text: string): {
    corridors: { startDist: number; endDist: number; side: "left" | "right"; maxOffset: number }[];
    errors: string[];
  } {
    const corridors: { startDist: number; endDist: number; side: "left" | "right"; maxOffset: number }[] = [];
    const errors: string[] = [];
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (line === "") continue;
      const parts = line.split(/[,、\s]+/).filter((p) => p !== "");
      const start = Number(parts[0]);
      const end = Number(parts[1]);
      const side =
        parts[2] === "左" || parts[2] === "left" ? "left" :
        parts[2] === "右" || parts[2] === "right" ? "right" : null;
      const max = Number(parts[3]);
      if (parts.length !== 4 || !Number.isFinite(start) || !Number.isFinite(end) ||
          side === null || !Number.isFinite(max) || max <= 0) {
        errors.push(`制限区間の行を解釈できません: "${line}"`);
        continue;
      }
      corridors.push({ startDist: start, endDist: end, side, maxOffset: max });
    }
    return { corridors, errors };
  }

  /** Persist the corridor lines + objective into def.analysisHints. */
  function syncAnalysisHints(): string[] {
    const hints = { ...model.def.analysisHints };
    const { corridors, errors } = parseCorridors(anaCorridors.value);

    if (corridors.length > 0) hints.corridors = corridors;
    else delete hints.corridors;

    if (anaStripe.checked) hints.objective = "stripeFirst";
    else delete hints.objective;

    if (Object.keys(hints).length > 0) model.def.analysisHints = hints;
    else delete model.def.analysisHints;
    return errors;
  }

  function updateAnalysisUI() {
    const st = analysis.state;
    anaLoadBtn.disabled = st === "loadingImage" || st === "analyzing" || st === "applying";
    anaRunBtn.disabled = st !== "ready";
    anaCancelBtn.disabled = st !== "analyzing";
    anaApplyRow.style.display = st === "preview" ? "" : "none";
    const res = analysis.getResolution();
    anaResEl.textContent = res ? `${res.toFixed(2)} m/px` : "";
    if (st === "error") {
      anaResultEl.innerHTML = `<span style="color:var(--danger)">エラー: ${analysis.error}</span>`;
    } else if (st === "loadingImage") {
      anaResultEl.textContent = "画像読み込み中…";
    } else if (st === "analyzing") {
      anaResultEl.textContent = "解析中…";
    } else if (st === "preview") {
      const p = analysis.getProposal()!;
      const highL = p.confidenceLeft.filter((c) => c >= Number(anaThresh.value) / 100).length;
      const highR = p.confidenceRight.filter((c) => c >= Number(anaThresh.value) / 100).length;
      const total = p.left.length;
      anaResultEl.innerHTML =
        `提案: ${total}点 / 高信頼 L:${highL} R:${highR} / ` +
        `欠損: ${p.diagnostics.missingCount}`;
    } else {
      anaResultEl.textContent = "";
    }
    anaWarnEl.textContent = "";
  }

  anaThresh.addEventListener("input", () => {
    anaThreshVal.textContent = (Number(anaThresh.value) / 100).toFixed(2);
    if (analysis.state === "preview" && analysis.getProposal()) {
      analysis.overlay.update(model, analysis.getProposal()!, Number(anaThresh.value) / 100);
      updateAnalysisUI();
    }
  });

  anaLoadBtn.addEventListener("click", async () => {
    if (!model.def.origin) {
      anaResultEl.innerHTML = `<span style="color:var(--danger)">origin がありません</span>`;
      return;
    }
    // Each source is a (meta, image) pair in the track's public data directory
    // sharing the same bbox; higher-resolution variants just have more pixels.
    const base = (document.querySelector("#anaSource") as HTMLSelectElement).value;
    const metaUrl = `/data/tracks/${trackName}/${base}_meta.json`;
    const imageUrl = `/data/tracks/${trackName}/${base}.jpg`;
    await analysis.loadImage({
      trackName,
      metaUrl,
      imageUrl,
      confidenceThreshold: Number(anaThresh.value) / 100,
      options: {
        minOffset: Number(anaMinOff.value),
        maxOffset: Number(anaMaxOff.value),
      },
    });
    updateAnalysisUI();
  });

  anaRunBtn.addEventListener("click", async () => {
    const hintErrors = syncAnalysisHints();
    if (hintErrors.length > 0) {
      anaWarnEl.textContent = hintErrors.join("; ");
      return;
    }
    await analysis.runAnalysis();
    updateAnalysisUI();
  });

  anaCancelBtn.addEventListener("click", () => {
    analysis.cancel();
    updateAnalysisUI();
  });

  anaApplyHigh.addEventListener("click", () => {
    const result = analysis.applyProposal("highOnly");
    anaWarnEl.textContent = result.warnings.join("; ");
    tools.sync();
    renderLists();
    setStatus(`適用: ${result.applied}点 / スキップ: ${result.skipped}点`);
    updateAnalysisUI();
  });

  anaApplyAll.addEventListener("click", () => {
    const result = analysis.applyProposal("all");
    anaWarnEl.textContent = result.warnings.join("; ");
    tools.sync();
    renderLists();
    setStatus(`全適用: ${result.applied}点 / スキップ: ${result.skipped}点`);
    updateAnalysisUI();
  });

  anaDiscard.addEventListener("click", () => {
    analysis.discard();
    setStatus("提案を破棄しました");
    updateAnalysisUI();
  });

  updateAnalysisUI();

  // track selector
  const sel = $("#trackSel") as HTMLSelectElement;
  const names = (await (await fetch("/api/tracks")).json()) as string[];
  for (const n of names) {
    const opt = document.createElement("option");
    opt.value = n;
    opt.textContent = n;
    opt.selected = n === trackName;
    sel.append(opt);
  }
  sel.addEventListener("change", () => {
    location.search = `?track=${sel.value}`;
  });

  model.onChange = updateTitle;
  tools.onEdit = renderLists;
  tools.onSelect = renderLists;
  tools.onNotify = setStatus;
  tools.sync();
  renderLists();
  setMode("pan");
  setStatus(
    model.hasElevationFile
      ? "準備完了"
      : "注意: elevation.json がありません(高さ0のフラット表示)。npm run elevation を実行してください。",
  );
  // Automation / debugging hooks (playwright E2E, console poking).
  Object.assign(window as object, { __editor: { model, tools, scene }, __editorReady: true });
}

boot().catch((e) => setStatus(`エラー: ${e instanceof Error ? e.message : e}`));
