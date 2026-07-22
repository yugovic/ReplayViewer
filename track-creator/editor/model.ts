/**
 * Editor-side track state: the TrackDef being edited, its pre-fetched
 * elevation, live meshes rebuilt in-browser with the SAME builders the CLI
 * uses, an undo history, and the save/build API calls.
 */

import * as THREE from "three";
import { resampleCenterline, interpByDist, wrapDist } from "../src/centerline.ts";
import type { Centerline } from "../src/centerline.ts";
import { buildStations, makeTrackSampler } from "../src/mesh/stations.ts";
import type { StationTrack, TrackSample } from "../src/mesh/stations.ts";
import { buildRoadMesh } from "../src/mesh/road.ts";
import { buildCurbsMesh } from "../src/mesh/curbs.ts";
import type { MeshData } from "../src/mesh/road.ts";
import type { TrackDef, ElevationData, RefsData } from "../src/types.ts";

function toGeometry(data: MeshData): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(data.positions, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(data.normals, 3));
  if (data.uvs) g.setAttribute("uv", new THREE.Float32BufferAttribute(data.uvs, 2));
  if (data.colors) g.setAttribute("color", new THREE.Float32BufferAttribute(data.colors, 3));
  g.setIndex(new THREE.Uint32BufferAttribute(data.indices, 1));
  return g;
}

/** Flat placeholder when a track has no elevation.json yet. */
function flatElevation(def: TrackDef, line: Centerline): ElevationData {
  const samples = line.points
    .filter((_, i) => i % 5 === 0)
    .map((p) => ({ dist: p.dist, x: p.x, z: p.z, yLeft: 0, yCenter: 0, yRight: 0 }));
  return {
    source: "flat",
    step: def.road.step * 5,
    corridorHalfWidth: def.road.width / 2 + 2,
    totalLength: line.totalLength,
    samples,
  };
}

export class TrackModel {
  name = "";
  def!: TrackDef;
  elev!: ElevationData;
  refs: RefsData | null = null;
  hasElevationFile = false;

  line!: Centerline;
  track!: StationTrack;
  sampler!: (dist: number) => TrackSample;

  readonly group = new THREE.Group();
  roadMesh: THREE.Mesh;
  curbsMesh: THREE.Mesh;

  dirty = false;
  private history: string[] = [];
  /** Called after every rebuild (panel refresh etc.). */
  onChange: () => void = () => {};

  private roadMat = new THREE.MeshStandardMaterial({ color: 0x3a3d42, roughness: 0.95 });
  private curbMat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.7,
    side: THREE.DoubleSide,
  });

  constructor() {
    this.roadMesh = new THREE.Mesh(new THREE.BufferGeometry(), this.roadMat);
    this.roadMesh.name = "road";
    this.curbsMesh = new THREE.Mesh(new THREE.BufferGeometry(), this.curbMat);
    this.curbsMesh.name = "curbs";
    this.group.add(this.roadMesh, this.curbsMesh);
  }

  async load(name: string): Promise<void> {
    const res = await fetch(`/api/track/${name}`);
    if (!res.ok) throw new Error(`track "${name}" の読み込みに失敗 (${res.status})`);
    const data = (await res.json()) as {
      def: TrackDef;
      elevation: ElevationData | null;
      refs: RefsData | null;
    };
    this.name = name;
    this.def = data.def;
    this.refs = data.refs;
    this.line = resampleCenterline(this.def.controlPoints, this.def.road.step, this.def.closed);
    this.hasElevationFile = data.elevation !== null;
    this.elev = data.elevation ?? flatElevation(this.def, this.line);
    this.history = [];
    this.dirty = false;
    this.rebuild();
  }

  /** Recompute the centerline from def.controlPoints, then rebuild meshes. */
  rebuildCenterline(): void {
    this.line = resampleCenterline(this.def.controlPoints, this.def.road.step, this.def.closed);
    this.rebuild();
  }

  /** Recompute stations + meshes from the current def (centerline is cached). */
  rebuild(): void {
    this.track = buildStations(this.def, this.elev, this.line);
    this.sampler = makeTrackSampler(this.track);

    this.roadMesh.geometry.dispose();
    this.roadMesh.geometry = toGeometry(buildRoadMesh(this.track));

    this.curbsMesh.geometry.dispose();
    const curbs = buildCurbsMesh(this.track, this.def.curbs ?? []);
    this.curbsMesh.geometry = curbs ? toGeometry(curbs) : new THREE.BufferGeometry();
    this.curbsMesh.visible = curbs !== null;

    this.onChange();
  }

  /** Push an undo snapshot. Call BEFORE mutating def (e.g. at drag start). */
  snapshot(): void {
    this.history.push(JSON.stringify(this.def));
    if (this.history.length > 100) this.history.shift();
    this.dirty = true;
  }

  undo(): boolean {
    const prev = this.history.pop();
    if (!prev) return false;
    this.def = JSON.parse(prev) as TrackDef;
    this.rebuildCenterline();
    return true;
  }

  // ── geometry queries ─────────────────────────────────────────────────────

  boundingBox(): THREE.Box3 {
    const box = new THREE.Box3();
    for (const s of this.track.stations) {
      box.expandByPoint(new THREE.Vector3(s.x, s.yCenter, s.z));
    }
    return box;
  }

  /** Arc-length + signed lateral (m, + = right) of a world point. */
  project(p: THREE.Vector3): { dist: number; lateral: number } {
    const st = this.track.stations;
    let bi = 0;
    let bd = Infinity;
    for (let i = 0; i < st.length; i++) {
      const d = (st[i].x - p.x) ** 2 + (st[i].z - p.z) ** 2;
      if (d < bd) {
        bd = d;
        bi = i;
      }
    }
    const s = st[bi];
    const dx = p.x - s.x;
    const dz = p.z - s.z;
    let dist = s.dist + dx * s.tx + dz * s.tz;
    dist = this.track.closed
      ? wrapDist(dist, this.track.totalLength)
      : Math.min(Math.max(dist, 0), this.track.totalLength);
    return { dist, lateral: dx * -s.tz + dz * s.tx };
  }

  /** Paved width at an arc-length, from the profile (or the base width). */
  widthAt(dist: number): number {
    const prof = this.def.road.widthProfile ?? [];
    if (prof.length === 0) return this.def.road.width;
    const sorted = prof.slice().sort((a, b) => a.dist - b.dist);
    return interpByDist(
      sorted.map((k) => k.dist),
      sorted.map((k) => k.width),
      dist,
      this.track.totalLength,
      this.track.closed,
    );
  }

  // ── free-form edge offsets ────────────────────────────────────────────────

  /** Spacing of the edge point rows (existing edges array, or the default). */
  edgeStep(): number {
    return this.def.road.edges?.step ?? 2;
  }

  private edgeSampleCount(step: number): number {
    const total = this.track.totalLength;
    return this.track.closed ? Math.ceil(total / step) : Math.floor(total / step) + 1;
  }

  /**
   * Materialize road.edges from the current effective widths so edge points
   * become individually editable. No-op when edges already exist.
   */
  ensureEdges(step?: number): void {
    if (this.def.road.edges) return;
    const s = step ?? this.edgeStep();
    const n = this.edgeSampleCount(s);
    const left: number[] = new Array(n);
    const right: number[] = new Array(n);
    for (let i = 0; i < n; i++) {
      const half = Math.round((this.widthAt(i * s) / 2) * 100) / 100;
      left[i] = half;
      right[i] = half;
    }
    this.def.road.edges = { step: s, left, right };
  }

  /** Resample the edges arrays to a new spacing (keeps the current shape). */
  setEdgeStep(step: number): void {
    this.snapshot();
    this.ensureEdges(step);
    const edges = this.def.road.edges!;
    if (edges.step !== step) {
      const n = this.edgeSampleCount(step);
      const left: number[] = new Array(n);
      const right: number[] = new Array(n);
      for (let i = 0; i < n; i++) {
        const s = this.sampler(Math.min(i * step, this.track.totalLength));
        left[i] = Math.round(s.offsetLeft * 100) / 100;
        right[i] = Math.round(s.offsetRight * 100) / 100;
      }
      this.def.road.edges = { step, left, right };
    }
    this.rebuild();
  }

  /** Drop the free-form edges and fall back to width/widthProfile. */
  resetEdges(): void {
    if (!this.def.road.edges) return;
    this.snapshot();
    delete this.def.road.edges;
    this.rebuild();
  }

  // ── persistence ──────────────────────────────────────────────────────────

  async save(): Promise<void> {
    const res = await fetch(`/api/track/${this.name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(this.def),
    });
    if (!res.ok) throw new Error((await res.json()).error ?? `保存失敗 (${res.status})`);
    this.dirty = false;
    this.onChange();
  }

  async build(): Promise<{ glb: string; kb: number; roadTris: number; curbTris: number }> {
    const res = await fetch(`/api/build/${this.name}`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error ?? `ビルド失敗 (${res.status})`);
    return body;
  }

  downloadJson(): void {
    const blob = new Blob([JSON.stringify(this.def, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${this.name}.track.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}
