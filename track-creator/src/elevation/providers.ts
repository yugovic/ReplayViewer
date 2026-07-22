/**
 * Elevation providers. All return LOCAL-frame metres for a local (x, z) query
 * unless noted; the GSI provider returns ASL metres and the CLI rebases them
 * onto the track origin altitude.
 *
 * GSI 標高タイル (https://maps.gsi.go.jp/development/ichiran.html):
 *   https://cyberjapandata.gsi.go.jp/xyz/{src}/{z}/{x}/{y}.txt
 *   256×256 comma-separated metres per tile, "e" = no data.
 *   dem5a (airborne laser, z15) with dem10b (z14) as fallback.
 */

import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export interface ElevationProvider {
  /** Elevation at local (x = east, z = -north). */
  sample(x: number, z: number): Promise<number>;
}

export class FlatProvider implements ElevationProvider {
  async sample(): Promise<number> {
    return 0;
  }
}

/** Deterministic rolling hills (~±10 m over a few hundred metres) for offline demos. */
export class SyntheticProvider implements ElevationProvider {
  async sample(x: number, z: number): Promise<number> {
    return 5 * Math.sin(x / 170) + 4 * Math.cos(z / 140) + 1.5 * Math.sin((x + z) / 80);
  }
}

// ─── GSI ─────────────────────────────────────────────────────────────────────

const DEG = Math.PI / 180;
/** Tile sources tried in order, with the zoom each is published at. */
const GSI_SOURCES: { name: string; zoom: number }[] = [
  { name: "dem5a", zoom: 15 },
  { name: "dem10b", zoom: 14 },
];

type Tile = Float32Array | null; // null = tile missing (404)

export class GsiProvider implements ElevationProvider {
  private tiles = new Map<string, Promise<Tile>>();

  constructor(
    private origin: { lat: number; lng: number },
    private cacheDir: string,
  ) {
    mkdirSync(cacheDir, { recursive: true });
  }

  /** Local ENU metres → WGS84 (equirectangular around the origin; fine at track scale). */
  localToLatLng(x: number, z: number): { lat: number; lng: number } {
    const north = -z;
    const lat = this.origin.lat + north / 111320;
    const lng = this.origin.lng + x / (111320 * Math.cos(this.origin.lat * DEG));
    return { lat, lng };
  }

  async sample(x: number, z: number): Promise<number> {
    const { lat, lng } = this.localToLatLng(x, z);
    for (const src of GSI_SOURCES) {
      const v = await this.sampleLatLng(src, lat, lng);
      if (Number.isFinite(v)) return v;
    }
    throw new Error(`GSI: no elevation data at lat=${lat.toFixed(6)} lng=${lng.toFixed(6)}`);
  }

  /** Bilinear sample from one tile source; NaN when any contributing pixel is missing. */
  private async sampleLatLng(
    src: { name: string; zoom: number },
    lat: number,
    lng: number,
  ): Promise<number> {
    const worldPx = 256 * 2 ** src.zoom;
    const latRad = lat * DEG;
    const px = ((lng + 180) / 360) * worldPx - 0.5;
    const py =
      ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * worldPx - 0.5;

    const x0 = Math.floor(px);
    const y0 = Math.floor(py);
    const fx = px - x0;
    const fy = py - y0;

    const v00 = await this.pixel(src, x0, y0);
    const v10 = await this.pixel(src, x0 + 1, y0);
    const v01 = await this.pixel(src, x0, y0 + 1);
    const v11 = await this.pixel(src, x0 + 1, y0 + 1);
    const top = v00 + (v10 - v00) * fx;
    const bot = v01 + (v11 - v01) * fx;
    return top + (bot - top) * fy; // NaN propagates if any pixel is missing
  }

  private async pixel(
    src: { name: string; zoom: number },
    ix: number,
    iy: number,
  ): Promise<number> {
    const tx = Math.floor(ix / 256);
    const ty = Math.floor(iy / 256);
    const tile = await this.tile(src.name, src.zoom, tx, ty);
    if (!tile) return NaN;
    return tile[(iy - ty * 256) * 256 + (ix - tx * 256)];
  }

  private tile(name: string, zoom: number, tx: number, ty: number): Promise<Tile> {
    const key = `${name}_${zoom}_${tx}_${ty}`;
    let p = this.tiles.get(key);
    if (!p) {
      p = this.loadTile(name, zoom, tx, ty, key);
      this.tiles.set(key, p);
    }
    return p;
  }

  private async loadTile(
    name: string,
    zoom: number,
    tx: number,
    ty: number,
    key: string,
  ): Promise<Tile> {
    const cachePath = join(this.cacheDir, `${key}.txt`);
    let text: string;
    if (existsSync(cachePath)) {
      text = readFileSync(cachePath, "utf8");
      if (text === "404") return null;
    } else {
      const url = `https://cyberjapandata.gsi.go.jp/xyz/${name}/${zoom}/${tx}/${ty}.txt`;
      const res = await fetch(url);
      if (res.status === 404) {
        writeFileSync(cachePath, "404");
        return null;
      }
      if (!res.ok) throw new Error(`GSI tile fetch failed: ${url} (${res.status})`);
      text = await res.text();
      writeFileSync(cachePath, text);
    }
    const data = new Float32Array(256 * 256).fill(NaN);
    const rows = text.split("\n");
    for (let r = 0; r < 256 && r < rows.length; r++) {
      const cols = rows[r].split(",");
      for (let c = 0; c < 256 && c < cols.length; c++) {
        const v = cols[c];
        if (v !== "e" && v !== "") data[r * 256 + c] = Number(v);
      }
    }
    return data;
  }
}
