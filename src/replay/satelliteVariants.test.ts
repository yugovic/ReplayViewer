import { describe, expect, it } from "vitest";
import {
  isImageResponseAvailable,
  parseSatVariantParam,
  probeSatelliteVariants,
  satelliteFilename,
  satelliteMetadataFilename,
  SATELLITE_VARIANTS,
  type SatVariantId,
  type VariantProbe,
} from "./satelliteVariants";

describe("isImageResponseAvailable", () => {
  it("is available for a 200 response with an image content-type", () => {
    expect(isImageResponseAvailable(200, "image/jpeg")).toBe(true);
  });

  it("is available for an image content-type with parameters (charset etc.)", () => {
    expect(isImageResponseAvailable(200, "image/jpeg; charset=binary")).toBe(true);
  });

  it("is case-insensitive on the content-type", () => {
    expect(isImageResponseAvailable(200, "IMAGE/JPEG")).toBe(true);
  });

  it("rejects Vite's dev-server SPA fallback (200 + text/html) for a missing path", () => {
    expect(isImageResponseAvailable(200, "text/html")).toBe(false);
  });

  it("rejects a real 404 even if content-type looks image-like", () => {
    expect(isImageResponseAvailable(404, "image/jpeg")).toBe(false);
  });

  it("rejects a 200 with a null/absent content-type", () => {
    expect(isImageResponseAvailable(200, null)).toBe(false);
  });

  it("rejects other non-200 statuses", () => {
    expect(isImageResponseAvailable(500, "image/jpeg")).toBe(false);
    expect(isImageResponseAvailable(304, "image/jpeg")).toBe(false);
  });
});

describe("parseSatVariantParam", () => {
  it("resolves shizuoka, shizuoka_x2, sr, bing and bing_sr", () => {
    expect(parseSatVariantParam("shizuoka")).toBe("shizuoka");
    expect(parseSatVariantParam("shizuoka_x2")).toBe("shizuoka_x2");
    expect(parseSatVariantParam("sr")).toBe("sr");
    expect(parseSatVariantParam("bing")).toBe("bing");
    expect(parseSatVariantParam("bing_sr")).toBe("bing_sr");
  });

  it("falls back to default for null, empty, or unknown values", () => {
    expect(parseSatVariantParam(null)).toBe("default");
    expect(parseSatVariantParam("")).toBe("default");
    expect(parseSatVariantParam("hd")).toBe("default");
    expect(parseSatVariantParam("default")).toBe("default");
  });
});

describe("satelliteFilename", () => {
  it("maps every known variant id to its filename and metadata", () => {
    expect(satelliteFilename("default")).toBe("satellite.jpg");
    expect(satelliteFilename("shizuoka")).toBe("satellite_shizuoka.jpg");
    expect(satelliteFilename("shizuoka_x2")).toBe("satellite_shizuoka.jpg");
    expect(satelliteFilename("sr")).toBe("satellite_sr.jpg");
    expect(satelliteFilename("bing")).toBe("satellite_bing.jpg");
    expect(satelliteFilename("bing_sr")).toBe("satellite_bing_sr.jpg");
    expect(satelliteMetadataFilename("shizuoka")).toBe("satellite_shizuoka_meta.json");
    expect(satelliteMetadataFilename("shizuoka_x2")).toBe("satellite_shizuoka_meta.json");
    expect(satelliteMetadataFilename("bing_sr")).toBe("satellite_bing_meta.json");
  });
});

/** Builds a fake VariantProbe from a filename -> {status, contentType} map;
 * anything not in the map behaves like a 404 with no content-type. */
function fakeProbe(available: Record<string, { status: number; contentType: string | null }>): VariantProbe {
  return async (url) => {
    const filename = url.split("/").pop() ?? "";
    return available[filename] ?? { status: 404, contentType: null };
  };
}

describe("probeSatelliteVariants", () => {
  it("reports only the variants whose probe resolves to an available image", async () => {
    const probe = fakeProbe({
      "satellite.jpg": { status: 200, contentType: "image/jpeg" },
      "satellite_bing.jpg": { status: 200, contentType: "image/jpeg" },
      // optional variants absent -> SPA fallback
      "satellite_shizuoka.jpg": { status: 200, contentType: "text/html" },
      "satellite_sr.jpg": { status: 200, contentType: "text/html" },
      "satellite_bing_sr.jpg": { status: 200, contentType: "text/html" },
      "manifest.json": { status: 200, contentType: "text/html" },
    });

    const result = await probeSatelliteVariants("/data/tracks/barber", probe);
    expect(result).toEqual(["default", "bing"]);
  });

  it("preserves SATELLITE_VARIANTS order regardless of probe resolution order", async () => {
    const probe = fakeProbe({
      "satellite.jpg": { status: 200, contentType: "image/jpeg" },
      "satellite_shizuoka.jpg": { status: 200, contentType: "image/jpeg" },
      "manifest.json": { status: 200, contentType: "application/json" },
      "satellite_sr.jpg": { status: 200, contentType: "image/jpeg" },
      "satellite_bing.jpg": { status: 200, contentType: "image/jpeg" },
      "satellite_bing_sr.jpg": { status: 200, contentType: "image/jpeg" },
    });

    const result = await probeSatelliteVariants("/data/tracks/barber", probe);
    expect(result).toEqual(SATELLITE_VARIANTS.map((v) => v.id));
  });

  it("always includes 'default' even when its own probe fails or is inconclusive", async () => {
    const throwingProbe: VariantProbe = async () => {
      throw new Error("network error");
    };

    const result = await probeSatelliteVariants("/data/tracks/barber", throwingProbe);
    expect(result).toEqual(["default"]);
  });

  it("doesn't let one variant's rejected probe affect the others", async () => {
    const probe: VariantProbe = async (url) => {
      if (url.endsWith("satellite_bing.jpg")) {
        throw new Error("boom");
      }
      return url.endsWith("manifest.json")
        ? { status: 200, contentType: "application/json" }
        : { status: 200, contentType: "image/jpeg" };
    };

    const result = await probeSatelliteVariants("/data/tracks/barber", probe);
    expect(result).toEqual(["default", "shizuoka", "shizuoka_x2", "sr", "bing_sr"] as SatVariantId[]);
  });

  it("normalizes a trackDir without a trailing slash", async () => {
    const seen: string[] = [];
    const probe: VariantProbe = async (url) => {
      seen.push(url);
      return url.endsWith("manifest.json")
        ? { status: 200, contentType: "application/json" }
        : { status: 200, contentType: "image/jpeg" };
    };

    await probeSatelliteVariants("/data/tracks/barber", probe);
    expect(seen).toContain("/data/tracks/barber/satellite.jpg");
  });
});
