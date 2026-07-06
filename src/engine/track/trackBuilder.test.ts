import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { inflateSync } from "node:zlib";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { projectPointToCenterline, sampleTrackSurface } from "../../replay/interpolation";
import type { TrackData, TrackPoint } from "../../replay/types";
import { bilinearSample, decodeTerrariumHeight, latLngToTerrainUv, type Bbox } from "./groundMath";
import { buildFeaturesGroup, type FeaturesData } from "./FeatureBuilder";
import { computeDrapedGroundHeight, createFeatureDrape } from "./TrackBuilder";

const METERS_PER_DEGREE = 111_320;

function makeTrack(points: Array<{ x: number; z: number; dist: number; y?: number }>): TrackData {
  const centerline: TrackPoint[] = points.map((point) => ({
    lat: 0,
    lng: 0,
    alt: point.y ?? 0,
    dist: point.dist,
    x: point.x,
    y: point.y ?? 0,
    z: point.z,
  }));

  return {
    version: 1,
    trackId: "test",
    trackName: "Test",
    width: 12,
    totalLength: centerline[centerline.length - 1]?.dist ?? 0,
    origin: { lat: 0, lng: 0, alt: 0 },
    bounds: {
      lat: [0, 0],
      lng: [0, 0],
      x: [Math.min(...points.map((point) => point.x)), Math.max(...points.map((point) => point.x))],
      z: [Math.min(...points.map((point) => point.z)), Math.max(...points.map((point) => point.z))],
    },
    centerline,
  };
}

function readJson<T>(relativePath: string): T {
  return JSON.parse(readFileSync(join(process.cwd(), relativePath), "utf8")) as T;
}

function normalAt(track: TrackData, index: number): { x: number; z: number } {
  const points = track.centerline;
  const prev = points[Math.max(0, index - 1)];
  const next = points[Math.min(points.length - 1, index + 1)];
  const dx = next.x - prev.x;
  const dz = next.z - prev.z;
  const length = Math.hypot(dx, dz);

  if (length <= 0.001) {
    return { x: 1, z: 0 };
  }

  return { x: -dz / length, z: dx / length };
}

/** Inverse of src/replay/projection.ts's latLngToLocal (north = -z). */
function localToLatLng(track: TrackData, x: number, z: number): { lat: number; lng: number } {
  const lngScale = METERS_PER_DEGREE * Math.cos((track.origin.lat * Math.PI) / 180);
  return {
    lat: track.origin.lat - z / METERS_PER_DEGREE,
    lng: track.origin.lng + x / lngScale,
  };
}

describe("computeDrapedGroundHeight", () => {
  it("returns the sampler's terrain height (minus origin.alt) once past blendEnd", () => {
    const track = makeTrack([
      { x: 0, z: 0, y: 10, dist: 0 },
      { x: 100, z: 0, y: 20, dist: 100 },
    ]);
    track.origin.alt = 5;
    const sampler = { heightAt: () => 42 };

    // lateralDistance 30 at halfRoadWidth 6 is past blendEnd (26).
    expect(computeDrapedGroundHeight(track, 50, 30, 0, 0, sampler)).toBe(42 - 5);
  });

  it("falls back to roadY when the sampler is null (pure corridor mode)", () => {
    const track = makeTrack([
      { x: 0, z: 0, y: 10, dist: 0 },
      { x: 100, z: 0, y: 20, dist: 100 },
    ]);

    // roadY at the midpoint is 15; lateralDistance 30 is past blendEnd (26).
    expect(computeDrapedGroundHeight(track, 50, 30, 0, 0, null)).toBe(15);
  });
});

// --- Minimal Node-side PNG decoder (8-bit, non-interlaced RGB/RGBA) -------
// TerrainSampler.ts decodes terrain.png via the browser Canvas API; tests
// run under Node (no DOM). pipeline/fetch_terrain_tiles.py always writes an
// 8-bit, non-interlaced PNG (Pillow's default), so this only needs to cover
// that case to mirror it for the regression test below.

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

function decodePngToRgba(buffer: Buffer): { width: number; height: number; data: Uint8Array } {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (!buffer.subarray(0, 8).equals(signature)) {
    throw new Error("Not a PNG file");
  }

  let offset = 8;
  let width = 0;
  let height = 0;
  const idatChunks: Buffer[] = [];
  let channels = 3;

  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    const dataStart = offset + 8;
    const data = buffer.subarray(dataStart, dataStart + length);

    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      const bitDepth = data.readUInt8(8);
      const colorType = data.readUInt8(9);
      const interlace = data.readUInt8(12);
      if (bitDepth !== 8 || (colorType !== 2 && colorType !== 6) || interlace !== 0) {
        throw new Error(
          `Unsupported PNG for test decoder: bitDepth=${bitDepth} colorType=${colorType} interlace=${interlace}`,
        );
      }
      channels = colorType === 6 ? 4 : 3;
    } else if (type === "IDAT") {
      idatChunks.push(Buffer.from(data));
    } else if (type === "IEND") {
      break;
    }

    offset = dataStart + length + 4; // skip CRC
  }

  const raw = inflateSync(Buffer.concat(idatChunks));
  const stride = width * channels;
  const out = new Uint8Array(width * height * 4);
  let prevRow = new Uint8Array(stride);
  let rawOffset = 0;

  for (let y = 0; y < height; y += 1) {
    const filterType = raw[rawOffset];
    rawOffset += 1;
    const row = new Uint8Array(stride);
    for (let x = 0; x < stride; x += 1) {
      const rawByte = raw[rawOffset + x];
      const a = x >= channels ? row[x - channels] : 0;
      const b = prevRow[x];
      const c = x >= channels ? prevRow[x - channels] : 0;
      let value: number;
      switch (filterType) {
        case 0:
          value = rawByte;
          break;
        case 1:
          value = rawByte + a;
          break;
        case 2:
          value = rawByte + b;
          break;
        case 3:
          value = rawByte + Math.floor((a + b) / 2);
          break;
        case 4:
          value = rawByte + paeth(a, b, c);
          break;
        default:
          throw new Error(`Unsupported PNG filter type ${filterType}`);
      }
      row[x] = value & 0xff;
    }
    rawOffset += stride;

    for (let x = 0; x < width; x += 1) {
      const si = x * channels;
      const di = (y * width + x) * 4;
      out[di] = row[si];
      out[di + 1] = row[si + 1];
      out[di + 2] = row[si + 2];
      out[di + 3] = channels === 4 ? row[si + 3] : 255;
    }
    prevRow = row;
  }

  return { width, height, data: out };
}

const TERRAIN_IMAGE_PATH = join(process.cwd(), "public/data/tracks/barber/terrain.png");
const TERRAIN_META_PATH = join(process.cwd(), "public/data/tracks/barber/terrain_meta.json");
const terrainAssetsExist = existsSync(TERRAIN_IMAGE_PATH) && existsSync(TERRAIN_META_PATH);

function loadTestTerrainSampler(): { heightAt(lat: number, lng: number): number | null } {
  const meta = JSON.parse(readFileSync(TERRAIN_META_PATH, "utf8")) as { bbox: Bbox };
  const png = decodePngToRgba(readFileSync(TERRAIN_IMAGE_PATH));
  const heights = new Float32Array(png.width * png.height);
  for (let i = 0; i < heights.length; i += 1) {
    const o = i * 4;
    heights[i] = decodeTerrariumHeight(png.data[o], png.data[o + 1], png.data[o + 2]);
  }
  const { bbox } = meta;

  return {
    heightAt(lat: number, lng: number): number | null {
      if (lat < bbox.minLat || lat > bbox.maxLat || lng < bbox.minLng || lng > bbox.maxLng) {
        return null;
      }
      const { u, v } = latLngToTerrainUv(lat, lng, bbox);
      return bilinearSample(heights, png.width, png.height, u, v);
    },
  };
}

// Regression for the screenshot bug: satellite-ground triangles poking
// through the asphalt on steep terrain. Walks the real Barber centerline and
// asserts every drape vertex within the paved corridor stays comfortably
// below the road surface, mirroring TrackBuilder's own per-vertex placement.
describe("terrain-aware ground drape (screenshot-bug regression)", () => {
  it.skipIf(!terrainAssetsExist)(
    "keeps every drape vertex within the road corridor below the asphalt",
    () => {
      const track = readJson<TrackData>("public/data/tracks/barber/track.json");
      const sampler = loadTestTerrainSampler();
      const halfWidth = track.width / 2;

      for (let i = 0; i < track.centerline.length; i += 5) {
        const point = track.centerline[i];
        const normal = normalAt(track, i);

        for (let offset = -halfWidth; offset <= halfWidth; offset += 1) {
          const x = point.x + normal.x * offset;
          const z = point.z + normal.z * offset;
          const { arcLength, signedLateralDistance } = projectPointToCenterline(track, x, z);
          // Reference the cambered surface at this vertex's lateral offset (the
          // same value the drape uses), so the corridor-clearance check stays
          // meaningful when the road ribbon tilts (P2).
          const roadY = sampleTrackSurface(track, arcLength, signedLateralDistance);
          const { lat, lng } = localToLatLng(track, x, z);
          const vertexY = computeDrapedGroundHeight(track, x, z, lat, lng, sampler);

          expect(
            vertexY,
            `vertex above road at centerline index ${i}, offset ${offset.toFixed(1)} m`,
          ).toBeLessThanOrEqual(roadY - 0.2);
        }
      }
    },
  );
});

// Regression for the sky-smear bug: feature meshes rendered as gigantic
// sheets over the track (a paved "lid" hung 9 m above the S/F straight
// because ShapeGeometry puts no vertices inside a polygon, so interior
// triangles spanned between outline points on higher ground). Builds the OSM
// feature layer exactly as TrackBuilder does — real features.json, real
// terrain heightfield — and asserts every vertex is finite and inside the
// scene's physical envelope, and that no feature triangle passes above the
// road anywhere along the centerline.
const FEATURES_PATH = join(process.cwd(), "public/data/tracks/barber/features.json");
const featureAssetsExist = terrainAssetsExist && existsSync(FEATURES_PATH);

describe("OSM feature drape (sky-smear regression)", () => {
  it.skipIf(!featureAssetsExist)(
    "keeps every feature vertex finite, inside the scene envelope, and never above the road",
    () => {
      const track = readJson<TrackData>("public/data/tracks/barber/track.json");
      const data = readJson<FeaturesData>("public/data/tracks/barber/features.json");
      const sampler = loadTestTerrainSampler();

      // Build with the exact production drape/refinement helpers.
      const { stepAt, groundHeightAt } = createFeatureDrape(track, sampler);
      const group = buildFeaturesGroup(data, groundHeightAt, stepAt);
      expect(group.children.length).toBeGreaterThan(0);

      // The Barber terrain heightfield spans 170.2..228.4 m, i.e. y in about
      // [-25.5, +32.6] relative to origin.alt — real hillsides west of the
      // track sit above +30. The sky-smear signature is NaN or values far
      // outside that envelope, so allow the real range plus slack.
      const violations: string[] = [];
      for (const child of group.children) {
        const mesh = child as THREE.Mesh;
        const posAttr = mesh.geometry.getAttribute("position");
        for (let i = 0; i < posAttr.count; i += 1) {
          const x = posAttr.getX(i);
          const y = posAttr.getY(i);
          const z = posAttr.getZ(i);
          if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
            violations.push(`${mesh.name} vertex ${i}: non-finite (${x}, ${y}, ${z})`);
          } else if (y < -40 || y > 40) {
            violations.push(`${mesh.name} vertex ${i}: y=${y.toFixed(2)} out of range`);
          } else if (Math.abs(x) > 800 || Math.abs(z) > 800) {
            violations.push(`${mesh.name} vertex ${i}: (${x.toFixed(0)}, ${z.toFixed(0)}) beyond the satellite ground`);
          }
        }
      }
      expect(violations.slice(0, 20), `${violations.length} bad vertices`).toEqual([]);

      // The visible symptom: interior triangles bridging the road corridor
      // ("lids"). Cast rays straight up from just above car height along the
      // centerline — nothing from the feature layer may hang overhead.
      const raycaster = new THREE.Raycaster();
      const up = new THREE.Vector3(0, 1, 0);
      const canopies: string[] = [];
      for (let i = 0; i < track.centerline.length; i += 4) {
        const point = track.centerline[i];
        raycaster.set(new THREE.Vector3(point.x, point.y + 2, point.z), up);
        const hits = raycaster.intersectObjects(group.children, false);
        if (hits.length > 0 && canopies.length < 10) {
          canopies.push(
            `centerline #${i}: ${hits[0].object.name} at ${(point.y + 2 + hits[0].distance).toFixed(1)} m`,
          );
        }
      }
      expect(canopies, "feature triangles hanging over the road").toEqual([]);
    },
    60_000,
  );
});
