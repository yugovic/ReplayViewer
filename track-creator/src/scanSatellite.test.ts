import { describe, expect, it } from "vitest";
import {
  latLngToDiagnosticLocal,
  satelliteUvToLatLng,
  type SatelliteMeta,
} from "../preview/scanSatellite.ts";

const meta: SatelliteMeta = {
  imageFile: "satellite.jpg",
  imageWidth: 100,
  imageHeight: 100,
  bbox: { minLng: 138.91, maxLng: 138.94, minLat: 35.36, maxLat: 35.38 },
  mercator: true,
};

describe("scan satellite overlay projection", () => {
  it("maps image corners to the north-west and south-east bbox corners", () => {
    const northWest = satelliteUvToLatLng(0, 0, meta);
    const southEast = satelliteUvToLatLng(1, 1, meta);
    expect(northWest).toEqual({ lat: meta.bbox.maxLat, lng: meta.bbox.minLng });
    expect(southEast.lat).toBeCloseTo(meta.bbox.minLat, 10);
    expect(southEast.lng).toBe(meta.bbox.maxLng);
  });

  it("uses the same east-positive and south-positive local frame as telemetry", () => {
    const origin = { lat: 35.3717, lng: 138.9256 };
    const east = latLngToDiagnosticLocal(origin.lat, origin.lng + 0.001, origin);
    const north = latLngToDiagnosticLocal(origin.lat + 0.001, origin.lng, origin);
    expect(east.x).toBeGreaterThan(0);
    expect(east.z).toBeCloseTo(0);
    expect(north.x).toBeCloseTo(0);
    expect(north.z).toBeLessThan(0);
  });
});
