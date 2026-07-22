import * as THREE from "three";
import type { TrackData } from "../../replay/types";
import { latLngToLocal } from "../../replay/projection";
import { corridorBlendHeight, type Bbox, type DrapeCache } from "./groundMath";
import { attachGroundDetailShader, type GroundDetailUniforms } from "./groundDetail";

const MAX_PARALLEL_REQUESTS = 6;
const TILE_SEGMENTS = 16;
const SURFACE_OFFSET = 0.015;
const MAX_LOAD_DISTANCE_M = 200;
const UNLOAD_DISTANCE_M = MAX_LOAD_DISTANCE_M * 1.5;
const MIN_LOAD_SCREEN_PX = 64;
const MIN_KEEP_SCREEN_PX = 48;

export function estimatePerspectiveScreenPixels(
  sizeM: number,
  distanceM: number,
  viewportHeightPx: number,
  verticalFovDegrees: number,
): number {
  if (sizeM <= 0 || viewportHeightPx <= 0 || verticalFovDegrees <= 0) return 0;
  const focalLengthPx = viewportHeightPx / (2 * Math.tan(THREE.MathUtils.degToRad(verticalFovDegrees) / 2));
  return (sizeM / Math.max(distanceM, 0.01)) * focalLengthPx;
}

export interface EnhancedCorridorTile {
  file: string;
  outputPixel: { width: number; height: number };
  uv: { u0: number; v0: number; u1: number; v1: number };
  /** Draw above normal corridor tiles for reversible visualization trials. */
  priority?: number;
}

export interface EnhancedCorridorManifest {
  schemaVersion: number;
  kind: "track-corridor-enhancement";
  visualizationOnly: boolean;
  bbox: Bbox;
  mercator: boolean;
  tileCount: number;
  tiles: EnhancedCorridorTile[];
}

interface EnhancedCorridorGroundOptions {
  manifestUrl: string;
  overrideManifestUrl?: string;
  track: TrackData;
  drapeInputs: (
    x: number,
    z: number,
    lat: number,
    lng: number,
  ) => { roadY: number; terrainY: number; lateralDistance: number };
  /** Shared close-range detail uniforms (see groundDetail.ts). When provided,
   * every tile material gets the same asphalt/grass material response as the
   * base satellite ground — without it the SR layer would hide that response
   * exactly where chase cameras look. The SAME object must be shared with the
   * base ground so the layers-panel toggle drives both at once. */
  detailUniforms?: GroundDetailUniforms;
}

type TileStatus = "idle" | "loading" | "loaded" | "error";

interface TileEntry {
  tile: EnhancedCorridorTile;
  url: string;
  bbox: Bbox;
  bounds: THREE.Box3;
  /** Ground-plane diagonal, used for a conservative projected-size estimate. */
  sizeM: number;
  status: TileStatus;
  mesh: THREE.Mesh | null;
  /** From the optional override manifest (trial/experimental tiles). */
  override: boolean;
}

function latitudeAtV(v: number, bbox: Bbox, mercator: boolean): number {
  if (!mercator) return bbox.maxLat + v * (bbox.minLat - bbox.maxLat);
  const north = Math.asinh(Math.tan(THREE.MathUtils.degToRad(bbox.maxLat)));
  const south = Math.asinh(Math.tan(THREE.MathUtils.degToRad(bbox.minLat)));
  return THREE.MathUtils.radToDeg(Math.atan(Math.sinh(north + v * (south - north))));
}

export function manifestTileToBbox(
  tile: EnhancedCorridorTile,
  bbox: Bbox,
  mercator: boolean,
): Bbox {
  return {
    minLng: bbox.minLng + tile.uv.u0 * (bbox.maxLng - bbox.minLng),
    maxLng: bbox.minLng + tile.uv.u1 * (bbox.maxLng - bbox.minLng),
    minLat: latitudeAtV(tile.uv.v1, bbox, mercator),
    maxLat: latitudeAtV(tile.uv.v0, bbox, mercator),
  };
}

export function isEnhancedCorridorManifest(value: unknown): value is EnhancedCorridorManifest {
  if (!value || typeof value !== "object") return false;
  const manifest = value as Partial<EnhancedCorridorManifest>;
  return (
    manifest.schemaVersion === 1 &&
    manifest.kind === "track-corridor-enhancement" &&
    manifest.visualizationOnly === true &&
    typeof manifest.bbox === "object" &&
    Array.isArray(manifest.tiles) &&
    manifest.tiles.length > 0 &&
    manifest.tileCount === manifest.tiles.length
  );
}

function disposeTexture(texture: THREE.Texture | null): void {
  if (!texture) return;
  const image = texture.image as { close?: () => void } | undefined;
  image?.close?.();
  texture.dispose();
}

/**
 * Local, preprocessed orthophoto tiles. Only frustum-visible tiles live on the
 * GPU; tiles leaving the frustum are disposed and can be loaded again later.
 */
export class EnhancedCorridorGround {
  readonly group = new THREE.Group();

  private readonly options: EnhancedCorridorGroundOptions;
  private readonly projection = new THREE.Matrix4();
  private readonly frustum = new THREE.Frustum();
  private readonly cameraPoint = new THREE.Vector3();
  private entries: TileEntry[] = [];
  private manifest: EnhancedCorridorManifest | null = null;
  private controller: AbortController | null = null;
  private enabled = false;
  private disposed = false;
  private generation = 0;
  private inFlight = 0;
  private roadClearance = 0.6;
  private overrideTilesVisible = true;

  constructor(options: EnhancedCorridorGroundOptions) {
    this.options = options;
    this.group.name = "enhanced-corridor-ground";
    this.group.visible = false;
    this.updateDebugState();
  }

  setEnabled(enabled: boolean): void {
    if (this.disposed || enabled === this.enabled) return;
    this.enabled = enabled;
    this.group.visible = enabled;
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;

    if (!enabled) {
      this.clearLoadedTiles();
      return;
    }

    this.controller = new AbortController();
    if (this.manifest) {
      this.resetEntries();
      return;
    }
    void this.loadManifest(this.generation, this.controller.signal);
  }

  /** Show/hide the trial tiles from the override manifest, leaving the
   * standard SR tiles untouched — the in-app A/B switch between the shipped
   * 静岡20cm SR look and experimental (e.g. generative) patches. Hidden
   * override tiles also stop being fetched. */
  setOverrideTilesVisible(visible: boolean): void {
    this.overrideTilesVisible = visible;
    for (const entry of this.entries) {
      if (entry.override && entry.mesh) entry.mesh.visible = visible;
    }
  }

  setRoadVisible(visible: boolean): void {
    this.roadClearance = visible ? 0.6 : 0.08;
    for (const entry of this.entries) {
      const mesh = entry.mesh;
      const cache = mesh?.userData.enhancedDrapeCache as DrapeCache | undefined;
      if (!mesh || !cache) continue;
      const positions = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let i = 0; i < positions.count; i += 1) {
        positions.setY(
          i,
          corridorBlendHeight(
            {
              roadY: cache.roadY[i],
              terrainY: cache.terrainY[i],
              lateralDistance: cache.lateralDistance[i],
              halfRoadWidth: cache.halfRoadWidth,
            },
            this.roadClearance,
          ) + SURFACE_OFFSET,
        );
      }
      positions.needsUpdate = true;
      mesh.geometry.computeVertexNormals();
      mesh.geometry.computeBoundingBox();
      mesh.geometry.computeBoundingSphere();
    }
  }

  update(camera: THREE.Camera, viewportHeightPx: number, overview = false): void {
    if (!this.enabled || !this.manifest || this.disposed) return;
    camera.updateMatrixWorld();
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.projection);
    this.cameraPoint.setFromMatrixPosition(camera.matrixWorld);

    for (const entry of this.entries) {
      if (entry.status !== "loaded") continue;
      const distanceM = entry.bounds.distanceToPoint(this.cameraPoint);
      const screenPx = this.screenPixels(entry, camera, viewportHeightPx, distanceM);
      if (
        !this.frustum.intersectsBox(entry.bounds) ||
        (!overview && distanceM > UNLOAD_DISTANCE_M) ||
        screenPx < MIN_KEEP_SCREEN_PX
      ) {
        this.unloadTile(entry);
      }
    }

    const available = Math.max(0, MAX_PARALLEL_REQUESTS - this.inFlight);
    if (available === 0) return;
    const candidates = this.entries
      .filter((entry) => entry.status === "idle" && (!entry.override || this.overrideTilesVisible))
      .map((entry) => ({ entry, distanceM: entry.bounds.distanceToPoint(this.cameraPoint) }))
      .filter(
        ({ entry, distanceM }) =>
          this.frustum.intersectsBox(entry.bounds) &&
          (overview || distanceM <= MAX_LOAD_DISTANCE_M) &&
          this.screenPixels(entry, camera, viewportHeightPx, distanceM) >= MIN_LOAD_SCREEN_PX,
      )
      .sort((a, b) => a.distanceM - b.distanceM);
    for (const { entry } of candidates.slice(0, available)) {
      void this.loadTile(entry, this.generation);
    }
    this.group.userData.candidateTiles = candidates.length;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.enabled = false;
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
    this.clearLoadedTiles();
    this.entries = [];
    this.manifest = null;
    this.group.removeFromParent();
    this.updateDebugState();
  }

  private async loadManifest(generation: number, signal: AbortSignal): Promise<void> {
    try {
      // no-cache (revalidate): the manifest is tiny and is the version anchor
      // for the whole tile set. force-cache once served a stale manifest into
      // a session whose tiles had been regenerated on a NEW pixel grid under
      // the SAME filenames — tiles then appeared tens of metres off. Tile
      // entries now carry a ?v= stamp, so tile fetches stay cache-friendly.
      const response = await fetch(this.options.manifestUrl, { signal, cache: "no-cache" });
      if (!response.ok) throw new Error(`Enhanced corridor manifest ${response.status} ${response.statusText}`);
      const value = (await response.json()) as unknown;
      if (!isEnhancedCorridorManifest(value)) {
        throw new Error("Invalid enhanced corridor manifest.");
      }
      const baseUrl = new URL(this.options.manifestUrl, window.location.href);
      let tiles = value.tiles.map((tile) => ({ tile, baseUrl, override: false }));
      if (this.options.overrideManifestUrl) {
        try {
          const overrideUrl = new URL(this.options.overrideManifestUrl, window.location.href);
          const overrideResponse = await fetch(overrideUrl, { signal, cache: "no-cache" });
          if (overrideResponse.ok) {
            const overrideValue = (await overrideResponse.json()) as unknown;
            if (isEnhancedCorridorManifest(overrideValue)) {
              tiles = tiles.concat(
                overrideValue.tiles.map((tile) => ({ tile, baseUrl: overrideUrl, override: true })),
              );
            }
          }
        } catch (reason) {
          if (!(reason instanceof DOMException && reason.name === "AbortError")) {
            console.warn("[EnhancedCorridor] Optional override manifest failed:", reason);
          }
        }
      }
      if (!this.isCurrent(generation)) return;
      this.manifest = value;
      this.entries = tiles.map(({ tile, baseUrl: tileBaseUrl, override }) => {
        const bbox = manifestTileToBbox(tile, value.bbox, value.mercator !== false);
        return {
          tile,
          url: new URL(tile.file, tileBaseUrl).toString(),
          bbox,
          bounds: this.createBounds(bbox),
          sizeM: 0,
          status: "idle",
          mesh: null,
          override,
        };
      });
      for (const entry of this.entries) {
        const width = entry.bounds.max.x - entry.bounds.min.x;
        const depth = entry.bounds.max.z - entry.bounds.min.z;
        entry.sizeM = Math.hypot(width, depth);
      }
      this.updateDebugState();
    } catch (reason) {
      if (!this.isCurrent(generation) || (reason instanceof DOMException && reason.name === "AbortError")) return;
      console.warn("[EnhancedCorridor] Failed to load manifest:", reason);
      this.group.userData.error = reason instanceof Error ? reason.message : String(reason);
    }
  }

  private async loadTile(entry: TileEntry, generation: number): Promise<void> {
    if (!this.controller || entry.status !== "idle") return;
    entry.status = "loading";
    this.inFlight += 1;
    this.updateDebugState();
    try {
      const response = await fetch(entry.url, { signal: this.controller.signal, cache: "force-cache" });
      if (!response.ok) throw new Error(`Enhanced tile ${response.status} ${response.statusText}`);
      const bitmap = await createImageBitmap(await response.blob());
      if (!this.isCurrent(generation)) {
        bitmap.close();
        return;
      }
      const texture = new THREE.Texture(bitmap);
      texture.flipY = false;
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = 16;
      texture.needsUpdate = true;
      const mesh = this.createMesh(entry.bbox, texture, entry.tile.priority ?? 0);
      mesh.visible = !entry.override || this.overrideTilesVisible;
      entry.mesh = mesh;
      entry.status = "loaded";
      this.group.add(mesh);
    } catch (reason) {
      if (this.isCurrent(generation) && !(reason instanceof DOMException && reason.name === "AbortError")) {
        entry.status = "error";
        console.warn(`[EnhancedCorridor] Tile failed: ${entry.tile.file}`, reason);
      }
    } finally {
      if (generation === this.generation) this.inFlight = Math.max(0, this.inFlight - 1);
      this.updateDebugState();
    }
  }

  private createBounds(bbox: Bbox): THREE.Box3 {
    const bounds = new THREE.Box3();
    const samples = [
      [bbox.minLat, bbox.minLng],
      [bbox.minLat, bbox.maxLng],
      [bbox.maxLat, bbox.minLng],
      [bbox.maxLat, bbox.maxLng],
      [(bbox.minLat + bbox.maxLat) / 2, (bbox.minLng + bbox.maxLng) / 2],
    ];
    for (const [lat, lng] of samples) {
      const local = latLngToLocal(lat, lng, this.options.track.origin);
      const input = this.options.drapeInputs(local.x, local.z, lat, lng);
      bounds.expandByPoint(new THREE.Vector3(local.x, Math.min(input.roadY, input.terrainY), local.z));
      bounds.expandByPoint(new THREE.Vector3(local.x, Math.max(input.roadY, input.terrainY), local.z));
    }
    bounds.min.y -= 20;
    bounds.max.y += 20;
    return bounds;
  }

  private screenPixels(
    entry: TileEntry,
    camera: THREE.Camera,
    viewportHeightPx: number,
    distanceM: number,
  ): number {
    if (camera instanceof THREE.PerspectiveCamera) {
      return estimatePerspectiveScreenPixels(entry.sizeM, distanceM, viewportHeightPx, camera.fov);
    }
    if (camera instanceof THREE.OrthographicCamera) {
      const visibleHeightM = Math.abs(camera.top - camera.bottom) / Math.max(camera.zoom, 0.01);
      return visibleHeightM > 0 ? (entry.sizeM / visibleHeightM) * viewportHeightPx : 0;
    }
    return Number.POSITIVE_INFINITY;
  }

  private createMesh(bbox: Bbox, texture: THREE.Texture, priority: number): THREE.Mesh {
    const geometry = new THREE.PlaneGeometry(1, 1, TILE_SEGMENTS, TILE_SEGMENTS);
    geometry.rotateX(-Math.PI / 2);
    const index = geometry.getIndex();
    if (index) {
      const array = index.array as Uint16Array | Uint32Array;
      for (let i = 0; i < array.length; i += 3) {
        const second = array[i + 1];
        array[i + 1] = array[i + 2];
        array[i + 2] = second;
      }
      index.needsUpdate = true;
    }

    const positions = geometry.getAttribute("position") as THREE.BufferAttribute;
    const uvs = geometry.getAttribute("uv") as THREE.BufferAttribute;
    const cache: DrapeCache = {
      roadY: new Float32Array(positions.count),
      terrainY: new Float32Array(positions.count),
      lateralDistance: new Float32Array(positions.count),
      halfRoadWidth: this.options.track.width / 2,
    };
    for (let i = 0; i < positions.count; i += 1) {
      const u = uvs.getX(i);
      const v = uvs.getY(i);
      const lng = bbox.minLng + u * (bbox.maxLng - bbox.minLng);
      const lat = latitudeAtV(v, bbox, this.manifest?.mercator !== false);
      const local = latLngToLocal(lat, lng, this.options.track.origin);
      const input = this.options.drapeInputs(local.x, local.z, lat, lng);
      cache.roadY[i] = input.roadY;
      cache.terrainY[i] = input.terrainY;
      cache.lateralDistance[i] = input.lateralDistance;
      const height =
        corridorBlendHeight({ ...input, halfRoadWidth: cache.halfRoadWidth }, this.roadClearance) +
        SURFACE_OFFSET +
        priority * 0.002;
      positions.setXYZ(i, local.x, height, local.z);
    }
    positions.needsUpdate = true;
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    const material = new THREE.MeshStandardMaterial({
      map: texture,
      transparent: true,
      alphaTest: 0.005,
      depthWrite: false,
      roughness: 0.92,
      metalness: 0,
      polygonOffset: true,
      polygonOffsetFactor: -3 - priority,
      polygonOffsetUnits: -3 - priority,
    });
    if (this.options.detailUniforms) {
      // Tiles carry no baked corridor attributes, so the asphalt/grass zone
      // comes from the tile photo itself (coarse-mip ExG) and the effect is
      // gated by the tile's own alpha fade. One cache key → all tiles share
      // a single compiled program.
      attachGroundDetailShader(material, this.options.detailUniforms, {
        maskMode: "photoExg",
        edgeLines: false,
        cacheKey: "corridor-tile-detail-v1",
      });
    }
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = "enhanced-corridor-tile";
    mesh.receiveShadow = true;
    mesh.renderOrder = 1 + priority;
    mesh.userData.enhancedDrapeCache = cache;
    return mesh;
  }

  private unloadTile(entry: TileEntry): void {
    const mesh = entry.mesh;
    if (mesh) {
      this.group.remove(mesh);
      mesh.geometry.dispose();
      const material = mesh.material as THREE.MeshStandardMaterial;
      disposeTexture(material.map);
      material.dispose();
    }
    entry.mesh = null;
    entry.status = "idle";
    this.updateDebugState();
  }

  private clearLoadedTiles(): void {
    for (const entry of this.entries) {
      if (entry.mesh) this.unloadTile(entry);
      entry.status = "idle";
    }
    this.inFlight = 0;
    this.updateDebugState();
  }

  private resetEntries(): void {
    for (const entry of this.entries) {
      if (entry.status === "error") entry.status = "idle";
    }
    this.updateDebugState();
  }

  private updateDebugState(): void {
    this.group.userData.enabled = this.enabled;
    this.group.userData.totalTiles = this.entries.length;
    this.group.userData.loadedTiles = this.entries.filter((entry) => entry.status === "loaded").length;
    this.group.userData.loadingTiles = this.inFlight;
    this.group.userData.maxLoadDistanceM = MAX_LOAD_DISTANCE_M;
    this.group.userData.minLoadScreenPx = MIN_LOAD_SCREEN_PX;
  }

  private isCurrent(generation: number): boolean {
    return !this.disposed && this.enabled && generation === this.generation;
  }
}
