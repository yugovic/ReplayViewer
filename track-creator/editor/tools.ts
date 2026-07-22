/**
 * Edit interactions.
 *
 * Edge mode ("width") — the track edges are shown as point rows (one point
 * every road.edges.step metres, green = left, pink = right). Dragging a point
 * laterally moves that edge; a configurable brush radius moves neighbouring
 * points with a smooth cosine falloff (radius 0 = only the grabbed point).
 * 左右対称 mode mirrors the change onto the other side.
 *
 * Curb mode — two clicks on the road add a span on the clicked side; each
 * span gets start / mid / end handles (mid drags the whole span along the
 * track). L/R tag in the panel flips the side.
 */

import * as THREE from "three";
import type { EditorScene } from "./scene.ts";
import type { TrackModel } from "./model.ts";

export type Mode = "pan" | "width" | "curb" | "draw";
export type Selection = { kind: "curb"; index: number } | null;

interface CurbHandleData {
  index: number;
  sub: "start" | "mid" | "end";
}

const COLORS = {
  edgeL: 0x6fe08f,
  edgeR: 0xe06f93,
  curbL: 0x6fe08f,
  curbR: 0xe06f93,
  selected: 0xffffff,
  pending: 0xffd24d,
  rangeSel: 0xffd24d,
};

const MIN_OFFSET = 1;
const MAX_OFFSET = 30;
const MIN_CURB_LEN = 4;

const sphereGeo = new THREE.SphereGeometry(1, 16, 12);

function makeEdgePoints(hex: number): THREE.Points {
  const mat = new THREE.PointsMaterial({
    color: hex,
    size: 8,
    sizeAttenuation: false,
    depthTest: false,
  });
  const pts = new THREE.Points(new THREE.BufferGeometry(), mat);
  pts.renderOrder = 150;
  pts.frustumCulled = false;
  pts.visible = false;
  return pts;
}

export class EditTools {
  mode: Mode = "pan";
  selection: Selection = null;
  readonly handles = new THREE.Group();

  /** Edge-drag brush radius along the track, metres. 0 = single point. */
  brushRadius = 20;
  /** Mirror edge edits onto the other side. */
  symmetric = false;

  onEdit: () => void = () => {};
  onSelect: () => void = () => {};
  /** Called when the width-mode range selection changes (for button state). */
  onRangeChange: (active: boolean) => void = () => {};
  /** Status feedback for tool actions (wired to the editor status bar). */
  onNotify: (message: string) => void = () => {};
  pending: { dist: number; side: "left" | "right" } | null = null;
  private pendingMarker: THREE.Mesh | null = null;

  private edgeL = makeEdgePoints(COLORS.edgeL);
  private edgeR = makeEdgePoints(COLORS.edgeR);
  private rangeHighlight = makeEdgePoints(COLORS.rangeSel);

  /** Width-mode range selection: first click, then the confirmed span. */
  private rangeStart: { dist: number; side: "left" | "right" } | null = null;
  private range: { side: "left" | "right"; indices: number[] } | null = null;

  /**
   * Sample indices whose markers are hidden because the user "deleted" them.
   * The grid itself is fixed, so deletion re-interpolates the value and hides
   * the marker; markers reappear on Undo / re-sync / mode re-entry.
   */
  private deletedL = new Set<number>();
  private deletedR = new Set<number>();
  /** Maps the picked (filtered) point index back to the full sample index. */
  private edgeMapL: number[] = [];
  private edgeMapR: number[] = [];

  private dragCurb: { mesh: THREE.Mesh; data: CurbHandleData; mutated: boolean } | null = null;
  private dragEdge: {
    side: "left" | "right";
    index: number;
    origL: number[];
    origR: number[];
    mutated: boolean;
    planeY: number;
  } | null = null;
  private drawDrag: { points: THREE.Vector3[]; planeY: number } | null = null;
  private drawLine: THREE.Line | null = null;
  private downPos = { x: 0, y: 0 };

  constructor(
    private scene: EditorScene,
    private model: TrackModel,
  ) {
    this.handles.name = "handles";
    this.handles.add(this.edgeL, this.edgeR, this.rangeHighlight);
    (this.rangeHighlight.material as THREE.PointsMaterial).size = 11;
    const el = scene.renderer.domElement;
    el.addEventListener("pointerdown", (ev) => this.pointerDown(ev));
    el.addEventListener("pointermove", (ev) => this.pointerMove(ev));
    el.addEventListener("pointerup", (ev) => this.pointerUp(ev));
    // Right-click deletes edge points in width mode; keep the browser menu away.
    el.addEventListener("contextmenu", (ev) => {
      if (this.mode === "width") ev.preventDefault();
    });
    scene.onFrame(() => this.updateScale());
  }

  setMode(mode: Mode) {
    this.mode = mode;
    this.cancelPending();
    this.cancelDraw();
    this.clearRange();
    this.clearDeletedMarkers();
    this.edgeL.visible = mode === "width";
    this.edgeR.visible = mode === "width";
  }

  // ── width-mode range selection + operations ────────────────────────────────

  clearRange() {
    this.rangeStart = null;
    this.range = null;
    this.rangeHighlight.visible = false;
    this.onRangeChange(false);
  }

  hasRange(): boolean {
    return this.range !== null;
  }

  /** Indices covered by [distA..distB] going forward, taking the shorter arc on closed tracks. */
  private rangeIndices(distA: number, distB: number): number[] {
    const step = this.model.edgeStep();
    const n = this.edgeSampleCount();
    const closed = this.model.track.closed;
    const iA = Math.round(distA / step) % n;
    const iB = Math.round(distB / step) % n;
    if (!closed) {
      const lo = Math.min(iA, iB);
      const hi = Math.max(iA, iB);
      return Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
    }
    const forward = (iB - iA + n) % n;
    const backward = (iA - iB + n) % n;
    const [from, count] = forward <= backward ? [iA, forward] : [iB, backward];
    return Array.from({ length: count + 1 }, (_, k) => (from + k) % n);
  }

  private showRangeHighlight() {
    if (!this.range) {
      this.rangeHighlight.visible = false;
      return;
    }
    const { side, indices } = this.range;
    const step = this.model.edgeStep();
    const total = this.model.track.totalLength;
    const pos = new Float32Array(indices.length * 3);
    indices.forEach((i, k) => {
      const s = this.model.sampler(Math.min(i * step, total));
      const sign = side === "right" ? 1 : -1;
      const off = side === "right" ? s.offsetRight : s.offsetLeft;
      pos[k * 3] = s.x + -s.tz * sign * off;
      pos[k * 3 + 1] = (side === "right" ? s.yRight : s.yLeft) + 0.6;
      pos[k * 3 + 2] = s.z + s.tx * sign * off;
    });
    this.rangeHighlight.geometry.dispose();
    this.rangeHighlight.geometry = new THREE.BufferGeometry();
    this.rangeHighlight.geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    this.rangeHighlight.visible = true;
  }

  /** Two-click range selection on the road surface (width mode). */
  private widthClickSelect(ev: PointerEvent) {
    const hit = this.scene.pick(ev, [this.model.roadMesh]);
    if (!hit) {
      if (this.rangeStart || this.range) this.onNotify("選択を解除しました(路面の外をクリック)");
      this.clearRange();
      return;
    }
    const { dist, lateral } = this.model.project(hit.point);
    const side: "left" | "right" = lateral >= 0 ? "right" : "left";
    if (!this.rangeStart || this.range) {
      // first click (or starting over after a confirmed range)
      this.range = null;
      this.rangeStart = { dist, side };
      this.range = { side, indices: this.rangeIndices(dist, dist) };
      this.showRangeHighlight();
      this.range = null;
      this.onRangeChange(false);
      this.onNotify(
        `始点を選択(${side === "left" ? "左" : "右"}エッジ / dist ${dist.toFixed(0)}m) — 終点の路面をクリック`,
      );
    } else {
      this.range = {
        side: this.rangeStart.side,
        indices: this.rangeIndices(this.rangeStart.dist, dist),
      };
      this.rangeStart = null;
      this.showRangeHighlight();
      this.onRangeChange(true);
      this.onNotify(
        `区間を選択しました(${this.range.side === "left" ? "左" : "右"}エッジ・${this.range.indices.length}点) — 直線化/平滑化/基本幅/Delが使えます`,
      );
    }
  }

  /** Replace one edge point with the average of its neighbours ("delete" it). */
  private deleteEdgePoint(side: "left" | "right", index: number) {
    this.model.ensureEdges();
    const edges = this.model.def.road.edges!;
    const arr = side === "right" ? edges.right : edges.left;
    const n = arr.length;
    const closed = this.model.track.closed;
    if (!closed && (index === 0 || index === n - 1)) {
      this.onNotify("端の点は削除できません");
      return;
    }
    this.model.snapshot();
    const prev = arr[(index - 1 + n) % n];
    const next = arr[(index + 1) % n];
    const before = arr[index];
    arr[index] = round2(clampOffset((prev + next) / 2));
    (side === "left" ? this.deletedL : this.deletedR).add(index);
    this.model.rebuild();
    this.updatePositions();
    if (this.range) this.showRangeHighlight();
    this.onEdit();
    this.onNotify(
      `${side === "left" ? "左" : "右"}エッジの点を削除しました` +
      ` (${before.toFixed(1)}m → ${arr[index].toFixed(1)}m / マーカーはUndo・モード切替で再表示)`,
    );
  }

  /**
   * Apply an operation to the selected edge range.
   *  straighten — the edge becomes the straight chord between the samples just
   *               outside the selection (points project onto it along their
   *               station normals)
   *  smooth     — moving average inside the selection (ends anchored outside)
   *  reset      — back to half the base road width
   */
  applyRangeOp(op: "straighten" | "smooth" | "reset"): boolean {
    if (!this.range) return false;
    this.clearDeletedMarkers(); // the range is being re-authored; show all points
    this.model.ensureEdges();
    const edges = this.model.def.road.edges!;
    const arr = this.range.side === "right" ? edges.right : edges.left;
    const { side, indices } = this.range;
    const n = arr.length;
    const step = this.model.edgeStep();
    const total = this.model.track.totalLength;
    const closed = this.model.track.closed;
    const sign = side === "right" ? 1 : -1;

    const stationAt = (i: number) => this.model.sampler(Math.min(i * step, total));
    const edgeXZ = (i: number) => {
      const s = stationAt(i);
      const off = arr[i];
      return { x: s.x + -s.tz * sign * off, z: s.z + s.tx * sign * off };
    };

    this.model.snapshot();

    if (op === "reset") {
      const half = this.model.def.road.width / 2;
      for (const i of indices) arr[i] = round2(clampOffset(half));
    } else if (op === "smooth") {
      for (let pass = 0; pass < 2; pass++) {
        const before = arr.slice();
        for (const i of indices) {
          if (!closed && (i === 0 || i === n - 1)) continue;
          const prev = before[(i - 1 + n) % n];
          const next = before[(i + 1) % n];
          arr[i] = round2(clampOffset((prev + before[i] + next) / 3));
        }
      }
    } else {
      // straighten: chord between the anchor samples just outside the range
      const first = indices[0];
      const last = indices[indices.length - 1];
      const a = closed ? (first - 1 + n) % n : Math.max(0, first - 1);
      const b = closed ? (last + 1) % n : Math.min(n - 1, last + 1);
      const E0 = edgeXZ(a);
      const E1 = edgeXZ(b);
      const dx = E1.x - E0.x;
      const dz = E1.z - E0.z;
      for (const i of indices) {
        const s = stationAt(i);
        const nx = -s.tz * sign;
        const nz = s.tx * sign;
        const denom = nx * dz - nz * dx;
        if (Math.abs(denom) < 1e-6) continue; // normal parallel to the chord
        const o = ((E0.x - s.x) * dz - (E0.z - s.z) * dx) / denom;
        arr[i] = round2(clampOffset(o));
      }
    }

    this.model.rebuild();
    this.updatePositions();
    this.showRangeHighlight();
    this.onEdit();
    return true;
  }

  select(sel: Selection) {
    this.selection = sel;
    this.refreshColors();
    this.onSelect();
  }

  deleteSelected(): boolean {
    if (!this.selection) return false;
    this.model.snapshot();
    this.model.def.curbs?.splice(this.selection.index, 1);
    this.selection = null;
    this.model.rebuild();
    this.sync();
    this.onEdit();
    return true;
  }

  cancelPending() {
    this.pending = null;
    if (this.pendingMarker) {
      this.handles.remove(this.pendingMarker);
      this.pendingMarker = null;
    }
  }

  cancelDraw() {
    this.drawDrag = null;
    if (this.drawLine) {
      this.handles.remove(this.drawLine);
      this.drawLine.geometry.dispose();
      (this.drawLine.material as THREE.Material).dispose();
      this.drawLine = null;
    }
  }

  // ── handle meshes ──────────────────────────────────────────────────────────

  /** Rebuild curb handles + edge point rows from the current def. */
  sync() {
    this.deletedL.clear();
    this.deletedR.clear();
    for (const c of [...this.handles.children]) {
      if (c !== this.pendingMarker && c !== this.edgeL && c !== this.edgeR &&
          c !== this.rangeHighlight) {
        this.handles.remove(c);
      }
    }
    (this.model.def.curbs ?? []).forEach((_, i) => {
      for (const sub of ["start", "mid", "end"] as const) {
        const mesh = new THREE.Mesh(sphereGeo, new THREE.MeshBasicMaterial({ depthTest: false }));
        mesh.renderOrder = 200;
        mesh.userData = { index: i, sub } satisfies CurbHandleData;
        this.handles.add(mesh);
      }
    });
    this.updatePositions();
    this.refreshColors();
  }

  private curbHandles(): THREE.Mesh[] {
    return this.handles.children.filter(
      (c): c is THREE.Mesh =>
        c !== this.pendingMarker && c !== this.edgeL && c !== this.edgeR && c instanceof THREE.Mesh,
    );
  }

  /** Recompute handle + edge point world positions (values changed). */
  updatePositions() {
    const def = this.model.def;
    for (const c of this.curbHandles()) {
      const d = c.userData as CurbHandleData;
      const span = def.curbs?.[d.index];
      if (!span) continue;
      const dist =
        d.sub === "start"
          ? span.startDist
          : d.sub === "end"
            ? span.endDist
            : (span.startDist + span.endDist) / 2;
      const s = this.model.sampler(dist);
      const sign = span.side === "right" ? 1 : -1;
      const edgeOff = span.side === "right" ? s.offsetRight : s.offsetLeft;
      const off = edgeOff - 0.1 + span.width / 2;
      const y = (span.side === "right" ? s.yRight : s.yLeft) + 0.6;
      c.position.set(s.x + -s.tz * sign * off, y, s.z + s.tx * sign * off);
    }
    this.updateEdgePoints();
  }

  private edgeSampleCount(): number {
    const step = this.model.edgeStep();
    const total = this.model.track.totalLength;
    return this.model.track.closed ? Math.ceil(total / step) : Math.floor(total / step) + 1;
  }

  private updateEdgePoints() {
    const step = this.model.edgeStep();
    const n = this.edgeSampleCount();
    const posL: number[] = [];
    const posR: number[] = [];
    this.edgeMapL = [];
    this.edgeMapR = [];
    for (let i = 0; i < n; i++) {
      const dist = Math.min(i * step, this.model.track.totalLength);
      const s = this.model.sampler(dist);
      const nx = -s.tz;
      const nz = s.tx;
      if (!this.deletedL.has(i)) {
        posL.push(s.x - nx * s.offsetLeft, s.yLeft + 0.5, s.z - nz * s.offsetLeft);
        this.edgeMapL.push(i);
      }
      if (!this.deletedR.has(i)) {
        posR.push(s.x + nx * s.offsetRight, s.yRight + 0.5, s.z + nz * s.offsetRight);
        this.edgeMapR.push(i);
      }
    }
    this.edgeL.geometry.dispose();
    this.edgeL.geometry = new THREE.BufferGeometry();
    this.edgeL.geometry.setAttribute("position", new THREE.Float32BufferAttribute(posL, 3));
    this.edgeR.geometry.dispose();
    this.edgeR.geometry = new THREE.BufferGeometry();
    this.edgeR.geometry.setAttribute("position", new THREE.Float32BufferAttribute(posR, 3));
  }

  /** Translate a picked (filtered) point index back to the full sample index. */
  private pickedToFull(object: THREE.Object3D, pickedIndex: number): number {
    const map = object === this.edgeL ? this.edgeMapL : this.edgeMapR;
    return map[pickedIndex] ?? pickedIndex;
  }

  /** Re-show all "deleted" point markers. */
  clearDeletedMarkers() {
    if (this.deletedL.size === 0 && this.deletedR.size === 0) return;
    this.deletedL.clear();
    this.deletedR.clear();
    this.updateEdgePoints();
  }

  private refreshColors() {
    const def = this.model.def;
    for (const c of this.curbHandles()) {
      const d = c.userData as CurbHandleData;
      const mat = c.material as THREE.MeshBasicMaterial;
      const selected = this.selection && this.selection.index === d.index;
      if (selected) mat.color.setHex(COLORS.selected);
      else mat.color.setHex(def.curbs?.[d.index]?.side === "right" ? COLORS.curbR : COLORS.curbL);
    }
  }

  private updateScale() {
    const s = Math.min(Math.max(this.scene.worldPerPixel() * 6, 0.5), 25);
    for (const c of this.curbHandles()) c.scale.setScalar(s);
    if (this.pendingMarker) this.pendingMarker.scale.setScalar(s);
  }

  // ── pointer interaction ────────────────────────────────────────────────────

  private pointerDown(ev: PointerEvent) {
    this.downPos = { x: ev.clientX, y: ev.clientY };
    if (ev.button !== 0) return;

    // Draw mode: start freehand centerline tracing.
    if (this.mode === "draw") {
      const p = this.scene.pickPlane(ev, 0);
      if (!p) return;
      this.drawDrag = { points: [p.clone()], planeY: 0 };
      this.scene.controls.enabled = false;
      this.scene.renderer.domElement.setPointerCapture(ev.pointerId);
      return;
    }

    // Curb sphere handles first (they sit on top).
    const hit = this.scene.pick(ev, this.curbHandles());
    if (hit) {
      const mesh = hit.object as THREE.Mesh;
      this.dragCurb = { mesh, data: mesh.userData as CurbHandleData, mutated: false };
      this.scene.controls.enabled = false;
      this.scene.renderer.domElement.setPointerCapture(ev.pointerId);
      this.select({ kind: "curb", index: this.dragCurb.data.index });
      return;
    }

    // Edge points (width mode only).
    if (this.mode === "width") {
      // Alt+click deletes the nearest point: its offset is re-interpolated
      // from its neighbours, like removing a vertex from a polyline. The pick
      // window is more forgiving than the drag pick, and a miss says so.
      if (ev.altKey) {
        const wide = this.scene.worldPerPixel() * 14;
        const dhit = this.scene.pickPoints(ev, [this.edgeL, this.edgeR], wide);
        if (dhit && dhit.index !== undefined) {
          this.deleteEdgePoint(
            dhit.object === this.edgeL ? "left" : "right",
            this.pickedToFull(dhit.object, dhit.index),
          );
        } else {
          this.onNotify("エッジ点(緑/桃の点)の上で Alt+クリックしてください — 点にヒットしませんでした");
        }
        return;
      }
      const threshold = this.scene.worldPerPixel() * 8;
      const ehit = this.scene.pickPoints(ev, [this.edgeL, this.edgeR], threshold);
      if (ehit && ehit.index !== undefined) {
        this.model.ensureEdges();
        const edges = this.model.def.road.edges!;
        this.dragEdge = {
          side: ehit.object === this.edgeL ? "left" : "right",
          index: this.pickedToFull(ehit.object, ehit.index),
          origL: edges.left.slice(),
          origR: edges.right.slice(),
          mutated: false,
          planeY: ehit.point.y,
        };
        this.scene.controls.enabled = false;
        this.scene.renderer.domElement.setPointerCapture(ev.pointerId);
      }
    }
  }

  private pointerMove(ev: PointerEvent) {
    if (this.drawDrag) return this.moveDraw(ev);
    if (this.dragEdge) return this.moveEdge(ev);
    if (this.dragCurb) return this.moveCurb(ev);
  }

  private moveDraw(ev: PointerEvent) {
    const drag = this.drawDrag!;
    const p = this.scene.pickPlane(ev, drag.planeY);
    if (!p) return;
    const last = drag.points[drag.points.length - 1];
    // Only add points that are >2 m apart to avoid clutter
    if (p.distanceTo(last) < 2) return;
    drag.points.push(p.clone());
    this.updateDrawPreview();
  }

  private updateDrawPreview() {
    const pts = this.drawDrag!.points;
    if (pts.length < 2) return;
    if (this.drawLine) {
      this.handles.remove(this.drawLine);
      this.drawLine.geometry.dispose();
      (this.drawLine.material as THREE.Material).dispose();
    }
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color: 0x4da3ff, depthTest: false, linewidth: 3 });
    this.drawLine = new THREE.Line(geo, mat);
    this.drawLine.renderOrder = 160;
    this.handles.add(this.drawLine);
  }

  private moveEdge(ev: PointerEvent) {
    const drag = this.dragEdge!;
    const p = this.scene.pickPlane(ev, drag.planeY);
    if (!p) return;
    if (!drag.mutated) {
      this.model.snapshot();
      drag.mutated = true;
    }
    const edges = this.model.def.road.edges!;
    const step = edges.step;
    const total = this.model.track.totalLength;
    const closed = this.model.track.closed;
    const n = edges.left.length;

    const dist = Math.min(drag.index * step, total);
    const s = this.model.sampler(dist);
    const lateral = (p.x - s.x) * -s.tz + (p.z - s.z) * s.tx; // + = right
    const target = Math.min(
      Math.max(drag.side === "right" ? lateral : -lateral, MIN_OFFSET),
      MAX_OFFSET,
    );
    const orig = drag.side === "right" ? drag.origR : drag.origL;
    const delta = target - orig[drag.index];

    const r = this.brushRadius;
    const apply = (arr: number[], base: number[]) => {
      if (r <= 0) {
        arr[drag.index] = round2(clampOffset(base[drag.index] + delta));
        return;
      }
      for (let j = 0; j < n; j++) {
        let arc = Math.abs(j - drag.index) * step;
        if (closed) arc = Math.min(arc, total - arc);
        if (arc > r) continue;
        const w = 0.5 * (1 + Math.cos((Math.PI * arc) / r));
        arr[j] = round2(clampOffset(base[j] + delta * w));
      }
    };
    if (drag.side === "right" || this.symmetric) apply(edges.right, drag.origR);
    if (drag.side === "left" || this.symmetric) apply(edges.left, drag.origL);

    this.model.rebuild();
    this.updatePositions();
  }

  private moveCurb(ev: PointerEvent) {
    const { mesh, data } = this.dragCurb!;
    const p = this.scene.pickPlane(ev, mesh.position.y);
    if (!p) return;
    if (!this.dragCurb!.mutated) {
      this.model.snapshot();
      this.dragCurb!.mutated = true;
    }
    const span = this.model.def.curbs?.[data.index];
    if (!span) return;
    const dist = Math.round(this.model.project(p).dist * 2) / 2;
    const total = this.model.track.totalLength;
    if (data.sub === "start") {
      span.startDist = Math.min(dist, span.endDist - MIN_CURB_LEN);
    } else if (data.sub === "end") {
      span.endDist = Math.max(dist, span.startDist + MIN_CURB_LEN);
    } else {
      const len = span.endDist - span.startDist;
      let start = dist - len / 2;
      start = Math.min(Math.max(start, 0), Math.max(total - len, 0));
      span.startDist = Math.round(start * 2) / 2;
      span.endDist = span.startDist + len;
    }
    this.model.rebuild();
    this.updatePositions();
  }

  private pointerUp(ev: PointerEvent) {
    // Right-CLICK (not a right-drag pan) deletes the nearest edge point.
    if (ev.button === 2 && this.mode === "width") {
      const moved = Math.hypot(ev.clientX - this.downPos.x, ev.clientY - this.downPos.y);
      if (moved <= 5) {
        const wide = this.scene.worldPerPixel() * 14;
        const hit = this.scene.pickPoints(ev, [this.edgeL, this.edgeR], wide);
        if (hit && hit.index !== undefined) {
          this.deleteEdgePoint(
            hit.object === this.edgeL ? "left" : "right",
            this.pickedToFull(hit.object, hit.index),
          );
        }
      }
      return;
    }
    if (this.drawDrag) {
      this.finishDraw();
      this.scene.controls.enabled = true;
      return;
    }
    const moved = Math.hypot(ev.clientX - this.downPos.x, ev.clientY - this.downPos.y);

    if (this.dragEdge || this.dragCurb) {
      const wasEdgeGrab = this.dragEdge !== null;
      const mutated = this.dragEdge?.mutated || this.dragCurb?.mutated || false;
      this.dragEdge = null;
      this.dragCurb = null;
      this.scene.controls.enabled = true;
      if (mutated) {
        this.onEdit();
        return;
      }
      // Grabbing a point without moving it is a CLICK, not a drag — fall
      // through to range selection (otherwise clicks near edge points are
      // silently swallowed and the selection never starts).
      if (!(wasEdgeGrab && this.mode === "width" && moved <= 5)) return;
    }

    // Click (not a pan) in width mode: two-click range selection on the road.
    if (moved <= 5 && this.mode === "width") {
      this.widthClickSelect(ev);
      return;
    }

    // Click (not a pan): curb-mode add.
    if (moved > 5 || this.mode !== "curb") return;
    const hit = this.scene.pick(ev, [this.model.roadMesh]);
    if (!hit) return;
    const { dist, lateral } = this.model.project(hit.point);

    const side: "left" | "right" = lateral >= 0 ? "right" : "left";
    if (!this.pending) {
      this.pending = { dist: Math.round(dist * 2) / 2, side };
      const marker = new THREE.Mesh(
        sphereGeo,
        new THREE.MeshBasicMaterial({ color: COLORS.pending, depthTest: false }),
      );
      marker.renderOrder = 201;
      const s = this.model.sampler(this.pending.dist);
      const sign = side === "right" ? 1 : -1;
      const off = (side === "right" ? s.offsetRight : s.offsetLeft) + 0.5;
      marker.position.set(
        s.x + -s.tz * sign * off,
        (side === "right" ? s.yRight : s.yLeft) + 0.6,
        s.z + s.tx * sign * off,
      );
      this.pendingMarker = marker;
      this.handles.add(marker);
    } else {
      const d2 = Math.round(dist * 2) / 2;
      const start = Math.min(this.pending.dist, d2);
      const end = Math.max(this.pending.dist, d2);
      const span = {
        startDist: start,
        endDist: Math.max(end, start + MIN_CURB_LEN),
        side: this.pending.side,
        width: 1.2,
        stripeLength: 4,
      };
      this.cancelPending();
      this.model.snapshot();
      const curbs = (this.model.def.curbs ??= []);
      curbs.push(span);
      this.model.rebuild();
      this.sync();
      this.select({ kind: "curb", index: curbs.length - 1 });
      this.onEdit();
    }
  }

  private finishDraw() {
    const drag = this.drawDrag;
    if (!drag || drag.points.length < 2) {
      this.cancelDraw();
      return;
    }

    // Simplify with Douglas-Peucker (tolerance ~2 m)
    const simplified = douglasPeucker(drag.points, 2);
    if (simplified.length < 2) {
      this.cancelDraw();
      return;
    }

    // Project first and last drawn points onto the existing centerline
    // to find the arc-length range to replace.
    const closed = this.model.def.closed;
    const proj0 = this.model.project(simplified[0]);
    const proj1 = this.model.project(simplified[simplified.length - 1]);
    let dStart = proj0.dist;
    let dEnd = proj1.dist;

    // Handle wrap-around for closed tracks
    if (closed && dEnd < dStart) {
      [dStart, dEnd] = [dEnd, dStart];
    }
    // Ensure dStart < dEnd
    if (dStart > dEnd) {
      [dStart, dEnd] = [dEnd, dStart];
    }

    // Convert drawn points to controlPoints { x, z }
    const drawnCPs = simplified.map((p) => ({
      x: Math.round(p.x * 100) / 100,
      z: Math.round(p.z * 100) / 100,
    }));

    // Map existing control points to arc lengths
    const existingCPs = this.model.def.controlPoints;
    const cpArcs = existingCPs.map((cp) => {
      const v = new THREE.Vector3(cp.x, 0, cp.z);
      return this.model.project(v).dist;
    });

    // Build new control point array:
    // - Keep CPs before dStart
    // - Insert drawn CPs
    // - Keep CPs after dEnd
    const newCPs: { x: number; z: number }[] = [];
    const margin = 5; // small margin so we don't cut exactly at the boundary

    if (closed) {
      // For closed tracks, find the range [dStart, dEnd] and replace it
      // Keep points outside the range, insert drawn points in between
      let inserted = false;
      for (let i = 0; i < existingCPs.length; i++) {
        const arc = cpArcs[i];
        const inRange = arc >= dStart - margin && arc <= dEnd + margin;
        if (!inRange) {
          newCPs.push(existingCPs[i]);
        } else if (!inserted) {
          // Insert drawn points at the first in-range position
          newCPs.push(...drawnCPs);
          inserted = true;
        }
        // Skip subsequent in-range points (they're being replaced)
      }
      if (!inserted) {
        // All points were in range (unlikely for closed track) — just use drawn
        newCPs.length = 0;
        newCPs.push(...drawnCPs);
      }
    } else {
      // Open track: straightforward before/after split
      for (let i = 0; i < existingCPs.length; i++) {
        if (cpArcs[i] < dStart - margin) {
          newCPs.push(existingCPs[i]);
        }
      }
      newCPs.push(...drawnCPs);
      for (let i = 0; i < existingCPs.length; i++) {
        if (cpArcs[i] > dEnd + margin) {
          newCPs.push(existingCPs[i]);
        }
      }
    }

    // Need at least 2 control points
    if (newCPs.length < 2) {
      this.cancelDraw();
      return;
    }

    // Apply: snapshot for undo, replace controlPoints, rebuild
    this.model.snapshot();
    this.model.def.controlPoints = newCPs;
    // Keep edges and curbs — they're distance-based and still roughly valid
    this.model.rebuildCenterline();

    this.cancelDraw();
    this.sync();
    this.onEdit();
  }
}

// ── Douglas-Peucker line simplification ──────────────────────────────────────

function douglasPeucker(points: THREE.Vector3[], tolerance: number): THREE.Vector3[] {
  if (points.length < 3) return points;

  // Find the point with maximum distance from the line between first and last
  let maxDist = 0;
  let maxIdx = 0;
  const first = points[0];
  const last = points[points.length - 1];
  const lineVec = new THREE.Vector3().subVectors(last, first);
  const lineLen = lineVec.length();

  for (let i = 1; i < points.length - 1; i++) {
    let dist: number;
    if (lineLen < 1e-6) {
      dist = points[i].distanceTo(first);
    } else {
      const t = new THREE.Vector3().subVectors(points[i], first).dot(lineVec) / (lineLen * lineLen);
      const proj = new THREE.Vector3().copy(first).addScaledVector(lineVec, t);
      dist = points[i].distanceTo(proj);
    }
    if (dist > maxDist) {
      maxDist = dist;
      maxIdx = i;
    }
  }

  if (maxDist > tolerance) {
    // Recursively simplify both halves
    const left = douglasPeucker(points.slice(0, maxIdx + 1), tolerance);
    const right = douglasPeucker(points.slice(maxIdx), tolerance);
    // Combine (drop the shared endpoint)
    return [...left, ...right.slice(1)];
  } else {
    // All points are close enough to the line — keep only endpoints
    return [first, last];
  }
}

function clampOffset(v: number): number {
  return Math.min(Math.max(v, MIN_OFFSET), MAX_OFFSET);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}
