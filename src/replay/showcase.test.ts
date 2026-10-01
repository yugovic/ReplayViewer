import { describe, expect, it } from "vitest";
import { advanceInWindow, resolveShowcase } from "./showcase";

describe("Fuji showcase", () => {
  it("only enables for the intended course and real-log session", () => {
    expect(resolveShowcase("?track=fuji&race=fuji_aim_01&showcase=hairpin")).toBe("reference");
    expect(resolveShowcase("?track=fuji&race=fuji_aim_01&showcase=hairpin&look=original")).toBe("original");
    expect(resolveShowcase("?track=fuji&race=fuji_aim_01&showcase=hairpin&look=cg")).toBe("cg");
    expect(resolveShowcase("?track=fuji&race=fuji_aim_01&look=cg")).toBeNull();
    expect(resolveShowcase("?track=suzuka&race=suzuka_preview&showcase=hairpin")).toBeNull();
  });
  it("retains frame overshoot when looping and stops exactly at the end otherwise", () => {
    expect(advanceInWindow(71.9, .3, { start: 47, end: 72 }, true).time).toBeCloseTo(47.2);
    expect(advanceInWindow(71.9, .3, { start: 47, end: 72 }, false)).toEqual({ time: 72, playing: false });
  });
});
