import { describe, expect, it } from "vitest";
import { buildCreditLine } from "./credits";

describe("buildCreditLine", () => {
  it("builds a Barber (USGS 3DEP + OSM) credit from the raw source strings", () => {
    const credit = buildCreditLine({
      terrainSource: "USGS 3DEP 1m AL_11County_B23",
      features3dSource: "USGS 3DEP EPT AL_11County_2_B23 class-1 nDSM + OSM",
    });
    expect(credit).not.toBeNull();
    expect(credit!.visible).toContain("USGS 3DEP");
    expect(credit!.visible).toContain("Map data © OpenStreetMap contributors");
    expect(credit!.full).toContain("Terrain: USGS 3DEP 1m AL_11County_B23");
    expect(credit!.full).toContain("Features: USGS 3DEP EPT AL_11County_2_B23 class-1 nDSM + OSM");
  });

  it("carries the VIRTUAL SHIZUOKA CC BY 4.0 attribution for Fuji", () => {
    const credit = buildCreditLine({
      terrainSource: "VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県)",
      features3dSource: "VIRTUAL SHIZUOKA 2019 LP (CC BY 4.0, 静岡県) class-1 nDSM + OSM",
    });
    expect(credit).not.toBeNull();
    expect(credit!.visible).toContain("VIRTUAL SHIZUOKA");
    expect(credit!.visible).toContain("CC BY 4.0");
    expect(credit!.visible).toContain("静岡県");
    expect(credit!.visible).toContain("Map data © OpenStreetMap contributors");
  });

  it("omits the OSM clause when no source mentions OSM", () => {
    const credit = buildCreditLine({ terrainSource: "Some Elevation Source", features3dSource: null });
    expect(credit!.visible).toBe("Terrain/Features: Some Elevation Source");
    expect(credit!.visible).not.toContain("OpenStreetMap");
  });

  it("falls back to the features3d source when terrain is missing", () => {
    const credit = buildCreditLine({ terrainSource: null, features3dSource: "X data + OSM" });
    expect(credit!.visible).toContain("X data");
    expect(credit!.visible).toContain("OpenStreetMap");
  });

  it("returns null when there is nothing to attribute", () => {
    expect(buildCreditLine({})).toBeNull();
    expect(buildCreditLine({ terrainSource: "  ", features3dSource: "" })).toBeNull();
  });
});
