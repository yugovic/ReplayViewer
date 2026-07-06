import { describe, expect, it } from "vitest";
import {
  selectShot,
  cutDuration,
  hash01,
  COCKPIT_EVERY_N_CUTS,
  CUT_MIN_S,
  CUT_MAX_S,
  type DirectorInput,
} from "./director";

function input(overrides: Partial<DirectorInput> = {}): DirectorInput {
  return {
    dist: 1000,
    speedKmh: 120,
    aps: 30,
    brake: 0,
    cutIndex: 1,
    ...overrides,
  };
}

describe("selectShot", () => {
  it("cuts to a fixed TV point when braking into a corner", () => {
    expect(selectShot(input({ brake: 60, aps: 0, speedKmh: 150 }))).toBe("tv");
    expect(selectShot(input({ brake: 20, aps: 0, speedKmh: 90 }))).toBe("tv");
  });

  it("cuts to chase on corner-exit acceleration", () => {
    expect(selectShot(input({ brake: 0, aps: 90, speedKmh: 80 }))).toBe("chase");
    expect(selectShot(input({ brake: 0, aps: 60, speedKmh: 140 }))).toBe("chase");
  });

  it("cuts to a low TV pan on flat-out high-speed sections", () => {
    expect(selectShot(input({ brake: 0, aps: 100, speedKmh: 200 }))).toBe("tv_low");
    expect(selectShot(input({ brake: 0, aps: 40, speedKmh: 175 }))).toBe("tv_low");
  });

  it("braking wins over high speed (corner entry from a straight)", () => {
    expect(selectShot(input({ brake: 80, aps: 0, speedKmh: 210 }))).toBe("tv");
  });

  it("occasionally cuts to cockpit (every Nth cut), but never on the first cut", () => {
    expect(selectShot(input({ cutIndex: COCKPIT_EVERY_N_CUTS }))).toBe("cockpit");
    expect(selectShot(input({ cutIndex: COCKPIT_EVERY_N_CUTS * 2 }))).toBe("cockpit");
    expect(selectShot(input({ cutIndex: 0, brake: 50 }))).toBe("tv");
  });

  it("falls back to alternating tv/chase on neutral sections", () => {
    const neutral = { brake: 0, aps: 20, speedKmh: 120 };
    expect(selectShot(input({ ...neutral, cutIndex: 2 }))).toBe("tv");
    expect(selectShot(input({ ...neutral, cutIndex: 3 }))).toBe("chase");
  });

  it("is deterministic for identical inputs", () => {
    const a = input({ brake: 10, aps: 70, speedKmh: 100, cutIndex: 3 });
    expect(selectShot(a)).toBe(selectShot({ ...a }));
  });
});

describe("cutDuration", () => {
  it("stays within [4, 8) seconds for many cut indices", () => {
    for (let i = 0; i < 200; i++) {
      const d = cutDuration(i);
      expect(d).toBeGreaterThanOrEqual(CUT_MIN_S);
      expect(d).toBeLessThan(CUT_MAX_S);
    }
  });

  it("is deterministic", () => {
    expect(cutDuration(7)).toBe(cutDuration(7));
  });

  it("varies between cuts", () => {
    const values = new Set<number>();
    for (let i = 0; i < 10; i++) values.add(cutDuration(i));
    expect(values.size).toBeGreaterThan(5);
  });
});

describe("hash01", () => {
  it("returns values in [0, 1)", () => {
    for (let i = 0; i < 100; i++) {
      const h = hash01(i);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(1);
    }
  });
});
