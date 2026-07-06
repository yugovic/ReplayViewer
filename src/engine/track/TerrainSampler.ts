import { bilinearSample, decodeTerrariumHeight, latLngToTerrainUv, type Bbox } from "./groundMath";

interface TerrainMeta {
  imageFile: string;
  imageWidth: number;
  imageHeight: number;
  bbox: Bbox;
  mercator: boolean;
  encoding: string;
  zoom: number;
}

/**
 * Decoded terrain heightfield (see pipeline/fetch_terrain_tiles.py), sampled
 * bilinearly by (lat, lng). Loading never throws: any failure resolves to
 * null so callers can fall back to a road-only drape.
 */
export class TerrainSampler {
  private constructor(
    private readonly heights: Float32Array,
    private readonly width: number,
    private readonly height: number,
    private readonly bbox: Bbox,
  ) {}

  static async load(metaUrl: string, imageBaseUrl: string): Promise<TerrainSampler | null> {
    try {
      const metaResponse = await fetch(metaUrl);
      if (!metaResponse.ok) {
        throw new Error(`terrain meta fetch failed: ${metaResponse.status}`);
      }
      const meta = (await metaResponse.json()) as TerrainMeta;
      const base = imageBaseUrl.endsWith("/") ? imageBaseUrl : `${imageBaseUrl}/`;
      const image = await loadImage(`${base}${meta.imageFile}`);

      const canvas = document.createElement("canvas");
      canvas.width = meta.imageWidth;
      canvas.height = meta.imageHeight;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        throw new Error("2d canvas context unavailable");
      }
      ctx.drawImage(image, 0, 0);
      const { data } = ctx.getImageData(0, 0, meta.imageWidth, meta.imageHeight);

      const heights = new Float32Array(meta.imageWidth * meta.imageHeight);
      for (let i = 0; i < heights.length; i += 1) {
        const o = i * 4;
        heights[i] = decodeTerrariumHeight(data[o], data[o + 1], data[o + 2]);
      }

      // A corrupt/truncated download tends to decode to wildly out-of-range
      // values; spot-check the corners rather than trust the fetch blindly.
      const { imageWidth: w, imageHeight: h } = meta;
      const corners = [heights[0], heights[w - 1], heights[(h - 1) * w], heights[h * w - 1]];
      if (corners.some((height) => !Number.isFinite(height) || height < -100 || height > 1000)) {
        throw new Error("decoded corner heights out of plausible range");
      }

      return new TerrainSampler(heights, meta.imageWidth, meta.imageHeight, meta.bbox);
    } catch (err) {
      console.warn("TerrainSampler.load failed, falling back to road-only drape:", err);
      return null;
    }
  }

  heightAt(lat: number, lng: number): number | null {
    const { bbox } = this;
    if (lat < bbox.minLat || lat > bbox.maxLat || lng < bbox.minLng || lng > bbox.maxLng) {
      return null;
    }
    const { u, v } = latLngToTerrainUv(lat, lng, bbox);
    return bilinearSample(this.heights, this.width, this.height, u, v);
  }
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`failed to load image: ${url}`));
    image.src = url;
  });
}
