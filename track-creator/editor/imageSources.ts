/**
 * Browser-side image source adapters.
 * Loads georeferenced images (satellite_meta.json + image file) into a
 * PixelBuffer and wraps them as a GeoImageRasterSource.
 */

import type { RasterSource, GeoImageMeta } from "../src/image-analysis/types.ts";
import { GeoImageRasterSource } from "../src/image-analysis/raster.ts";
import type { PixelBuffer } from "../src/image-analysis/raster.ts";
import type { Origin } from "../src/image-analysis/coordinates.ts";

/**
 * Load an image file into a PixelBuffer via an offscreen canvas.
 * Returns RGBA data at the image's native resolution.
 */
async function loadImageToBuffer(url: string): Promise<PixelBuffer> {
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = url;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(img, 0, 0);
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  return {
    data: imageData.data,
    width: canvas.width,
    height: canvas.height,
  };
}

/**
 * Load a georeferenced image (meta + image file) from a track directory.
 * The meta JSON and image are expected at the given URLs.
 */
export async function loadGeoImageSource(
  metaUrl: string,
  imageUrl: string,
  origin: Origin,
  id: string,
): Promise<{ source: RasterSource; meta: GeoImageMeta }> {
  const metaRes = await fetch(metaUrl);
  if (!metaRes.ok) throw new Error(`メタデータ取得失敗: ${metaUrl} (${metaRes.status})`);
  const meta = (await metaRes.json()) as GeoImageMeta;

  const buf = await loadImageToBuffer(imageUrl);
  const source = new GeoImageRasterSource(id, buf, origin, meta);

  return { source, meta };
}

/**
 * Load a user-selected image file with manual calibration (2-point affine).
 * The calibration maps two known local-coordinate ↔ pixel pairs.
 */
export async function loadUserImageSource(
  file: File,
  calibration: {
    p1Local: { x: number; z: number };
    p1Pixel: { px: number; py: number };
    p2Local: { x: number; z: number };
    p2Pixel: { px: number; py: number };
  },
  id: string,
): Promise<RasterSource> {
  const url = URL.createObjectURL(file);
  try {
    const buf = await loadImageToBuffer(url);

    // Compute affine transform from local (x, z) to pixel (px, py)
    // using two points (rotation + uniform scale + translation).
    const dx = calibration.p2Local.x - calibration.p1Local.x;
    const dz = calibration.p2Local.z - calibration.p1Local.z;
    const dpx = calibration.p2Pixel.px - calibration.p1Pixel.px;
    const dpy = calibration.p2Pixel.py - calibration.p1Pixel.py;

    const localDist = Math.hypot(dx, dz);
    const pixelDist = Math.hypot(dpx, dpy);
    if (localDist < 1 || pixelDist < 1) {
      throw new Error("キャリブレーション: 2点が近すぎます");
    }

    const scale = pixelDist / localDist;
    const angle = Math.atan2(dpy, dpx) - Math.atan2(dz, dx);

    const cos = Math.cos(-angle);
    const sin = Math.sin(-angle);

    const transform = (x: number, z: number): { px: number; py: number } | null => {
      const rx = (x - calibration.p1Local.x) * cos - (z - calibration.p1Local.z) * sin;
      const rz = (x - calibration.p1Local.x) * sin + (z - calibration.p1Local.z) * cos;
      const px = calibration.p1Pixel.px + rx * scale;
      const py = calibration.p1Pixel.py + rz * scale;
      if (px < 0 || px > buf.width - 1 || py < 0 || py > buf.height - 1) return null;
      return { px, py };
    };

    const resolution = 1 / scale; // m/px

    return {
      id,
      resolutionMetersPerPixel: resolution,
      sampleLocal(x: number, z: number) {
        const p = transform(x, z);
        if (!p) return null;
        return sampleBilinearFromBuffer(buf, p.px, p.py);
      },
      containsLocal(x: number, z: number) {
        return transform(x, z) !== null;
      },
    };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Bilinear sample from a PixelBuffer. */
function sampleBilinearFromBuffer(buf: PixelBuffer, px: number, py: number): { r: number; g: number; b: number } | null {
  const { data, width, height } = buf;
  if (px < 0 || px > width - 1 || py < 0 || py > height - 1) return null;

  const x0 = Math.floor(px);
  const y0 = Math.floor(py);
  const x1 = Math.min(x0 + 1, width - 1);
  const y1 = Math.min(y0 + 1, height - 1);
  const fx = px - x0;
  const fy = py - y0;

  const i = (xx: number, yy: number) => (yy * width + xx) * 4;

  const r =
    data[i(x0, y0)] * (1 - fx) * (1 - fy) +
    data[i(x1, y0)] * fx * (1 - fy) +
    data[i(x0, y1)] * (1 - fx) * fy +
    data[i(x1, y1)] * fx * fy;
  const g =
    data[i(x0, y0) + 1] * (1 - fx) * (1 - fy) +
    data[i(x1, y0) + 1] * fx * (1 - fy) +
    data[i(x0, y1) + 1] * (1 - fx) * fy +
    data[i(x1, y1) + 1] * fx * fy;
  const b =
    data[i(x0, y0) + 2] * (1 - fx) * (1 - fy) +
    data[i(x1, y0) + 2] * fx * (1 - fy) +
    data[i(x0, y1) + 2] * (1 - fx) * fy +
    data[i(x1, y1) + 2] * fx * fy;

  return { r: Math.round(r), g: Math.round(g), b: Math.round(b) };
}
