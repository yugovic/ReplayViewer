import { describe, expect, it } from "vitest";
import { buildCreditLine } from "./credits";

describe("imagery attribution", () => {
  it("keeps the active VIRTUAL SHIZUOKA orthophoto source visible", () => {
    const credit = buildCreditLine({
      imagerySource: "VIRTUAL SHIZUOKA 2019 LP orthophoto 20 cm (CC BY 4.0, Shizuoka Prefecture)",
      terrainSource: "VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県)",
    });
    expect(credit?.visible).toContain("Imagery: VIRTUAL SHIZUOKA");
    expect(credit?.visible).toContain("20 cm");
    expect(credit?.full).toContain("Terrain: VIRTUAL SHIZUOKA");
  });
});
