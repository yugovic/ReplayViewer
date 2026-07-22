/**
 * GSI aerial-photo overlay (地理院タイル 全国最新写真 seamlessphoto) placed in
 * local ENU using the track origin, plus reference polylines (logger laps).
 * Attribution is mandatory: the #attrib element is shown while visible.
 */

import * as THREE from "three";
import type { RefsData } from "../src/types.ts";

const DEG = Math.PI / 180;
const MAX_TILES = 150;

interface Origin {
  lat: number;
  lng: number;
}

function localToLatLng(o: Origin, x: number, z: number) {
  return {
    lat: o.lat + -z / 111320,
    lng: o.lng + x / (111320 * Math.cos(o.lat * DEG)),
  };
}

function latLngToLocal(o: Origin, lat: number, lng: number) {
  return {
    x: (lng - o.lng) * 111320 * Math.cos(o.lat * DEG),
    z: -((lat - o.lat) * 111320),
  };
}

function tileIndex(lat: number, lng: number, z: number) {
  const n = 2 ** z;
  const latRad = lat * DEG;
  return {
    tx: Math.floor(((lng + 180) / 360) * n),
    ty: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  };
}

/** North-west corner of a tile in lat/lng. */
function tileCorner(tx: number, ty: number, z: number) {
  const n = 2 ** z;
  const lng = (tx / n) * 360 - 180;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - (2 * ty) / n))) / DEG;
  return { lat, lng };
}

export class GsiPhotoOverlay {
  readonly group = new THREE.Group();
  private materials: THREE.MeshBasicMaterial[] = [];
  private built = false;
  private opacity = 1;
  private onTop = false;

  constructor(
    private origin: Origin,
    private boundsLocal: THREE.Box3,
    private groundY: number,
    private attribEl: HTMLElement,
  ) {
    this.group.visible = false;
    this.group.name = "gsi-photo";
  }

  setVisible(v: boolean) {
    if (v && !this.built) this.buildTiles(17);
    this.group.visible = v;
    this.attribEl.style.display = v ? "block" : "none";
  }

  setOpacity(o: number) {
    this.opacity = o;
    for (const m of this.materials) m.opacity = o;
  }

  /** Draw the photo over the track (semi-transparent) instead of under it. */
  setOnTop(v: boolean) {
    this.onTop = v;
    this.group.position.y = v ? this.groundY + 60 : 0;
    for (const m of this.materials) {
      m.depthTest = !v;
    }
    this.group.renderOrder = v ? 100 : -100;
    for (const c of this.group.children) c.renderOrder = v ? 100 : -100;
  }

  private buildTiles(zoom: number) {
    this.built = true;
    const min = this.boundsLocal.min;
    const max = this.boundsLocal.max;
    const nw = localToLatLng(this.origin, min.x - 150, min.z - 150);
    const se = localToLatLng(this.origin, max.x + 150, max.z + 150);
    const a = tileIndex(nw.lat, nw.lng, zoom);
    const b = tileIndex(se.lat, se.lng, zoom);
    const tx0 = Math.min(a.tx, b.tx);
    const tx1 = Math.max(a.tx, b.tx);
    const ty0 = Math.min(a.ty, b.ty);
    const ty1 = Math.max(a.ty, b.ty);
    if ((tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TILES) {
      console.warn("GSI overlay: too many tiles, skipping");
      return;
    }

    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    for (let tx = tx0; tx <= tx1; tx++) {
      for (let ty = ty0; ty <= ty1; ty++) {
        const cNW = tileCorner(tx, ty, zoom);
        const cSE = tileCorner(tx + 1, ty + 1, zoom);
        const pNW = latLngToLocal(this.origin, cNW.lat, cNW.lng);
        const pSE = latLngToLocal(this.origin, cSE.lat, cSE.lng);
        const w = pSE.x - pNW.x;
        const h = pSE.z - pNW.z;

        const tex = loader.load(
          `https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/${zoom}/${tx}/${ty}.jpg`,
        );
        tex.colorSpace = THREE.SRGBColorSpace;
        const mat = new THREE.MeshBasicMaterial({
          map: tex,
          transparent: true,
          opacity: this.opacity,
          depthWrite: false,
          depthTest: !this.onTop,
        });
        this.materials.push(mat);

        const geo = new THREE.PlaneGeometry(w, h);
        geo.rotateX(-Math.PI / 2); // +z toward south, texture top = north
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(pNW.x + w / 2, this.groundY, pNW.z + h / 2);
        mesh.renderOrder = -100;
        this.group.add(mesh);
      }
    }
  }
}

function mercY(lat: number): number {
  return Math.log(Math.tan(Math.PI / 4 + (lat * DEG) / 2));
}

function mercYInv(y: number): number {
  return (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / DEG;
}

/**
 * Overlay that shows a georeferenced analysis image (satellite_meta.json +
 * jpg pair) exactly where the width analysis samples it — so the backdrop the
 * user judges against is the very image the detector saw, at full resolution.
 * The image rows are uniform in Mercator y, so the plane is split into
 * horizontal strips with per-strip latitudes (≈0.2 m error otherwise).
 */
export class AnalysisImageOverlay {
  readonly group = new THREE.Group();
  private materials: THREE.MeshBasicMaterial[] = [];
  private opacity = 1;
  private onTop = false;
  private currentUrl = "";

  constructor(
    private origin: Origin,
    private groundY: number,
  ) {
    this.group.visible = false;
    this.group.name = "analysis-image";
  }

  /** Load (or switch to) a meta+image pair. No-op when already showing it. */
  async setSource(metaUrl: string, imageUrl: string): Promise<void> {
    if (imageUrl === this.currentUrl) return;
    const metaRes = await fetch(metaUrl);
    if (!metaRes.ok) throw new Error(`オーバーレイのメタ取得失敗: ${metaUrl}`);
    const meta = await metaRes.json();

    const tex = await new THREE.TextureLoader().loadAsync(imageUrl);
    tex.colorSpace = THREE.SRGBColorSpace;

    this.group.clear();
    this.materials = [];
    this.currentUrl = imageUrl;

    const { minLat, maxLat, minLng, maxLng } = meta.bbox;
    const yTop = mercY(maxLat);
    const yBottom = mercY(minLat);
    const STRIPS = 8;
    for (let i = 0; i < STRIPS; i++) {
      const v0 = i / STRIPS;
      const v1 = (i + 1) / STRIPS;
      const latTop = meta.mercator
        ? mercYInv(yTop + (yBottom - yTop) * v0)
        : maxLat + (minLat - maxLat) * v0;
      const latBottom = meta.mercator
        ? mercYInv(yTop + (yBottom - yTop) * v1)
        : maxLat + (minLat - maxLat) * v1;
      const pNW = latLngToLocal(this.origin, latTop, minLng);
      const pSE = latLngToLocal(this.origin, latBottom, maxLng);
      const w = pSE.x - pNW.x;
      const h = pSE.z - pNW.z;

      const geo = new THREE.PlaneGeometry(w, h);
      const uv = geo.attributes.uv as THREE.BufferAttribute;
      uv.setXY(0, 0, 1 - v0);
      uv.setXY(1, 1, 1 - v0);
      uv.setXY(2, 0, 1 - v1);
      uv.setXY(3, 1, 1 - v1);
      uv.needsUpdate = true;
      geo.rotateX(-Math.PI / 2);

      const mat = new THREE.MeshBasicMaterial({
        map: tex,
        transparent: true,
        opacity: this.opacity,
        depthWrite: false,
        depthTest: !this.onTop,
      });
      this.materials.push(mat);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(pNW.x + w / 2, this.groundY, pNW.z + h / 2);
      mesh.renderOrder = this.onTop ? 100 : -100;
      this.group.add(mesh);
    }
  }

  setVisible(v: boolean) {
    this.group.visible = v;
  }

  setOpacity(o: number) {
    this.opacity = o;
    for (const m of this.materials) m.opacity = o;
  }

  setOnTop(v: boolean) {
    this.onTop = v;
    this.group.position.y = v ? 60 : 0;
    for (const m of this.materials) m.depthTest = !v;
    for (const c of this.group.children) c.renderOrder = v ? 100 : -100;
  }
}

/** Reference polylines (driven laps etc.) lifted slightly above the road. */
export function buildRefLines(refs: RefsData): {
  group: THREE.Group;
  items: { name: string; color: string; object: THREE.Object3D }[];
} {
  const group = new THREE.Group();
  group.name = "refs";
  const items: { name: string; color: string; object: THREE.Object3D }[] = [];
  for (const line of refs.lines) {
    const color = line.color ?? "#4da3ff";
    const pts = line.points.map(([x, y, z]) => new THREE.Vector3(x, y + 0.4, z));
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color });
    const obj = line.closed ? new THREE.LineLoop(geo, mat) : new THREE.Line(geo, mat);
    obj.name = `ref:${line.name}`;
    obj.renderOrder = 10;
    group.add(obj);
    items.push({ name: line.name, color, object: obj });
  }
  return { group, items };
}
