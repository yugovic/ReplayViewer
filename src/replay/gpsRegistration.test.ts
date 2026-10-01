import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import {
  describeOffset,
  parseGpsRegistration,
  registrationOffset,
  registrationLimitsUrl,
  setRegistrationEnabled,
  withRegistration,
  type GpsRegistrationFile,
} from "./gpsRegistration";
import { getProjectedLapArcLengths, sampleReplay } from "./interpolation";
import { registeredVehicleGeometry, registeredVehicleWidth } from "./visualAlignment";
import { parseVehicleGeometry, tyreOuterHalfSpan } from "./vehicleGeometry";
import type { LapData, TrackData } from "./types";

const readJson = (path: string) => JSON.parse(readFileSync(path, "utf8"));
const track = readJson("public/data/tracks/fuji/track.json") as TrackData;
const wetFile = readJson("public/data/races/fuji_aim_01/gps_registration.json");
const dryFile = readJson("public/data/races/fuji_aim_2020_07_30/gps_registration.json");
const lap3 = readJson("public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_003.json") as LapData;
const profile = parseVehicleGeometry(readJson("public/data/vehicles/mazda2-dj.json"))!;
const roadSha = createHash("sha256").update(readFileSync("public/data/tracks/fuji/cg_study/geometry.json")).digest("hex");
const trackSha = createHash("sha256").update(readFileSync("public/data/tracks/fuji/track.json")).digest("hex");
const displayWidth = wetFile.method.vehicleGeometry?.bodyWidthMeters ?? wetFile.method.vehicleWidthMeters;
const wet = parseGpsRegistration(wetFile, "fuji_aim_01", "fuji") as GpsRegistrationFile;

describe("GPS registration file", () => {
  it("accepts the shipped Fuji files only for their own race and track", () => {
    expect(wet).not.toBeNull();
    expect(parseGpsRegistration(dryFile, "fuji_aim_2020_07_30", "fuji")).not.toBeNull();
    expect(parseGpsRegistration(wetFile, "fuji_aim_2020_07_30", "fuji")).toBeNull();
    expect(parseGpsRegistration(wetFile, "fuji_aim_01", "suzuka")).toBeNull();
    for (const bad of [null, "<!doctype html>", {}, { ...wetFile, kind: "other" }, { ...wetFile, laps: "x" }]) {
      expect(parseGpsRegistration(bad, "fuji_aim_01", "fuji")).toBeNull();
    }
  });

  it("refuses non-finite or implausibly large translations instead of moving the car", () => {
    const withOffset = (offsetMeters: unknown) =>
      ({ ...wetFile, laps: [{ ...wetFile.laps[0], offsetMeters }] });
    for (const offset of [[NaN, 0], [0, Infinity], [9, 0], [6, 6], [1], "1,2", null]) {
      expect(parseGpsRegistration(withOffset(offset), "fuji_aim_01", "fuji")).toBeNull();
    }
    expect(parseGpsRegistration(withOffset([-1.6, 1.6]), "fuji_aim_01", "fuji")).not.toBeNull();
  });

  it("covers every distributed lap of both sessions with a metre-scale shift", () => {
    for (const [file, race] of [[wetFile, "fuji_aim_01"], [dryFile, "fuji_aim_2020_07_30"]] as const) {
      const index = readJson(`public/data/races/${race}/laps.json`);
      expect(file.laps.map((l: { lap: number }) => l.lap)).toEqual(index.selected.map((s: { lap: number }) => s.lap));
      for (const l of file.laps) {
        expect(Math.hypot(l.offsetMeters[0], l.offsetMeters[1])).toBeGreaterThan(1);
        expect(Math.hypot(l.offsetMeters[0], l.offsetMeters[1])).toBeLessThan(4);
        // The fit must reduce the time spent beyond the traced limits.
        expect(l.registered.secondsBeyondLimit).toBeLessThan(l.raw.secondsBeyondLimit / 2);
      }
    }
  });
});

describe("per-lap registration", () => {
  const registered = withRegistration(lap3, wet, true);
  const sameWidthRaw = setRegistrationEnabled(registered, false);
  const [dx, dz] = wet.laps.find((l) => l.lap === 3)!.offsetMeters;

  it("stamps only the matching lap and never touches the recorded arrays", () => {
    const before = JSON.stringify(lap3);
    expect(registered).not.toBe(lap3);
    expect(registered.lat).toBe(lap3.lat);
    expect(registered.registration).toMatchObject({ offsetMeters: [dx, dz], enabled: true, vehicleWidthMeters: displayWidth });
    expect(withRegistration(lap3, null, true)).toBe(lap3);
    for (const meta of [{ lap: 9 }, { vehicle_id: "other" }, { race_id: "fuji_aim_2020_07_30" }]) {
      const other = { ...lap3, meta: { ...lap3.meta, ...meta } };
      expect(withRegistration(other, wet, true)).toBe(other);
    }
    expect(JSON.stringify(lap3)).toBe(before);
    expect(lap3.registration).toBeUndefined();
  });

  it("is one rigid translation over the whole lap: heading and telemetry unchanged", () => {
    for (let t = 0; t <= 141; t += 0.7) {
      const a = sampleReplay(lap3, track, t), b = sampleReplay(registered, track, t);
      expect(b.x - a.x).toBeCloseTo(dx, 8);
      expect(b.z - a.z).toBeCloseTo(dz, 8);
      expect(b.heading).toBeCloseTo(a.heading, 8);
      expect(b.telemetry).toEqual(a.telemetry);
      expect(sampleReplay(sameWidthRaw, track, t)).toEqual(a);
    }
  });

  it("toggling yields a new lap object so identity-keyed position caches cannot leak", () => {
    expect(setRegistrationEnabled(registered, true)).toBe(registered);
    expect(setRegistrationEnabled(lap3, false)).toBe(lap3);
    expect(sameWidthRaw).not.toBe(registered);
    expect(registrationOffset(sameWidthRaw)).toEqual({ x: 0, z: 0 });
    expect(registrationOffset(registered)).toEqual({ x: dx, z: dz });
    const a = getProjectedLapArcLengths(registered, track), b = getProjectedLapArcLengths(sameWidthRaw, track);
    expect(a).not.toBe(b);
    expect(getProjectedLapArcLengths(registered, track)).toBe(a);
  });

  it("keeps the fitted vehicle width for the raw comparison, and none for unstamped laps", () => {
    expect(registeredVehicleWidth(registered, track)).toBe(displayWidth);
    expect(registeredVehicleWidth(sameWidthRaw, track)).toBe(displayWidth);
    expect(registeredVehicleWidth(lap3, track)).toBeNull();
  });

  it("a measured antenna offset is a body-frame shift that turns with the car", () => {
    const measured = parseGpsRegistration(
      { ...wetFile, method: { ...wetFile.method, antennaOffsetMeters: { right: 0.5, forward: 0 } } }, "fuji_aim_01", "fuji");
    const lap = withRegistration(lap3, measured, true);
    expect(registered.registration?.antennaMeters).toBeNull(); // shipped files: position unknown = none
    for (const t of [22, 55, 84, 122]) { // south-, north-west-, north- and north-east-bound
      const a = sampleReplay(registered, track, t), b = sampleReplay(lap, track, t);
      const rightX = -Math.cos(a.heading), rightZ = Math.sin(a.heading);
      expect((b.x - a.x) * rightX + (b.z - a.z) * rightZ).toBeCloseTo(-0.5, 1); // centre is left of the antenna
      expect(Math.hypot(b.x - a.x, b.z - a.z)).toBeCloseTo(0.5, 2);
    }
    expect(sampleReplay(setRegistrationEnabled(lap, false), track, 55)).toEqual(sampleReplay(lap3, track, 55));
    for (const bad of [{ right: NaN, forward: 0 }, { right: 4, forward: 0 }, { right: "0.5" }, null]) {
      expect(parseGpsRegistration({ ...wetFile, method: { ...wetFile.method, antennaOffsetMeters: bad } }, "fuji_aim_01", "fuji")).toBeNull();
    }
  });

  it("never applies an offset to a lap regenerated after the fit (hash mismatch)", () => {
    const bytes = readFileSync("public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_003.json");
    const sha = createHash("sha256").update(bytes).digest("hex");
    expect(wet.laps.find((l) => l.lap === 3)?.dataSha256).toBe(sha); // shipped file matches the shipped lap
    expect(withRegistration(lap3, wet, true, sha, roadSha, trackSha).registration).toMatchObject({ verified: true, enabled: true });
    expect(withRegistration(lap3, wet, true, null).registration).toMatchObject({ verified: null, enabled: true });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const stale = withRegistration(lap3, wet, true, "0".repeat(64), roadSha, trackSha);
    warn.mockRestore();
    expect(stale.registration).toMatchObject({ verified: false, enabled: false });
    expect(setRegistrationEnabled(stale, true)).toBe(stale); // cannot be switched on
    expect(sampleReplay(stale, track, 55)).toEqual(sampleReplay(lap3, track, 55));
    expect(parseGpsRegistration({ ...wetFile, laps: [{ ...wetFile.laps[0], dataSha256: "zz" }] }, "fuji_aim_01", "fuji")).toBeNull();
  });

  it("validates the traced road and projection reference as well as the lap, with explicit unknown states", () => {
    const lapSha = createHash("sha256").update(readFileSync("public/data/races/fuji_aim_01/osaki_hmr_demio_101_lap_003.json")).digest("hex");
    const file = { ...wet, referenceTrack: { source: "public/data/tracks/fuji/track.json", sha256: trackSha } };
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const changedRoad = withRegistration(lap3, file, true, lapSha, "0".repeat(64), trackSha);
      expect(changedRoad.registration).toMatchObject({ verified: false, enabled: false,
        inputVerification: { lap: true, limits: false, referenceTrack: true } });
      expect(setRegistrationEnabled(changedRoad, true)).toBe(changedRoad);
      expect(sampleReplay(changedRoad, track, 55)).toEqual(sampleReplay(lap3, track, 55));
      const changedOrigin = withRegistration(lap3, file, true, lapSha, roadSha, "0".repeat(64));
      expect(changedOrigin.registration).toMatchObject({ verified: false, enabled: false,
        inputVerification: { referenceTrack: false } });
      for (const inputs of [[null, null, null], [lapSha, null, trackSha], [lapSha, roadSha, null]] as const) {
        expect(withRegistration(lap3, file, true, ...inputs).registration).toMatchObject({ verified: null, enabled: true });
      }
      expect(withRegistration(lap3, file, true, lapSha, roadSha, trackSha).registration?.verified).toBe(true);
      const legacy = { ...file, limits: undefined, referenceTrack: undefined };
      expect(withRegistration(lap3, legacy, true, lapSha).registration?.verified).toBe(true);
    } finally { warn.mockRestore(); }
    for (const source of ["https://example.com/road.json", "public/data/tracks/fuji/../other.json",
      "public/data/tracks/barber/geometry.json", "public/data/tracks/fuji/cg_study/geometry.json?x=1"]) {
      expect(parseGpsRegistration({ ...wetFile, limits: { sha256: roadSha, source } }, "fuji_aim_01", "fuji")).toBeNull();
    }
    expect(registrationLimitsUrl(wet)).toBe("/data/tracks/fuji/cg_study/geometry.json");
  });

  it("keeps separate Mazda2 axle dimensions when raw, rejects malformed profiles, and scopes them to matching laps", () => {
    const file = { ...wet, method: { ...wet.method, vehicleGeometry: profile } };
    const lap = withRegistration(lap3, file, true);
    expect(registeredVehicleGeometry(lap, track)).toEqual(profile);
    const raw = setRegistrationEnabled(lap, false);
    expect(registeredVehicleGeometry(raw, track)).toEqual(profile);
    expect(raw.registration?.vehicleGeometry).toBe(lap.registration?.vehicleGeometry);
    expect(registeredVehicleWidth(raw, track)).toBe(1.695);
    const other = { ...lap3, meta: { ...lap3.meta, vehicle_id: "other" } };
    expect(registeredVehicleGeometry(withRegistration(other, file, true), track)).toBeNull();
    const historical = { ...track, visualProfile: "cg" as const, trackId: "fuji",
      replayAlignment: { raceId: lap.meta.race_id, vehicleId: lap.meta.vehicle_id, lap: lap.meta.lap,
        status: "visual-fit" as const, kind: "local-windows" as const, widthMeters: 1.7, segments: [] } };
    expect(registeredVehicleGeometry(lap, historical)).toBeNull();
    expect(registeredVehicleWidth(lap, historical)).toBe(1.7);
    expect(parseGpsRegistration({ ...file, method: { vehicleGeometry: { ...profile, frontTyreWidthMeters: NaN } } },
      "fuji_aim_01", "fuji")).toBeNull();
  });

  it("describes the shift in compass words", () => {
    expect(describeOffset([-1.64, 1.58])).toBe("西1.64m・南1.58m");
    expect(describeOffset([0.5, -2.8])).toBe("東0.50m・北2.80m");
  });
});

/** Signed gap from a point to the traced road edges (positive = on the road). */
function roadGaps(road: number[][], x: number, z: number) {
  let best = road[0], bestD = Infinity;
  for (const row of road) {
    const d = (row[1] - x) ** 2 + (row[2] - z) ** 2;
    if (d < bestD) { bestD = d; best = row; }
  }
  const q = (x - best[1]) * best[3] + (z - best[2]) * best[4];
  const left = best.length >= 11 ? (best[7] - best[1]) * best[3] + (best[8] - best[2]) * best[4] : best[5];
  const right = best.length >= 11 ? (best[9] - best[1]) * best[3] + (best[10] - best[2]) * best[4] : best[6];
  return { left: q - left, right: right - q };
}

describe("track-limit evidence (Fuji 7/29 lap 3, traced CG road)", () => {
  const road = readJson("public/data/tracks/fuji/cg_study/geometry.json").road as number[][];
  const registered = withRegistration(lap3, wet, true);
  // Nominal tyre outside span is independent of body width.
  const halfSpan = wet.method?.vehicleGeometry ? tyreOuterHalfSpan(wet.method.vehicleGeometry, "front") : 0.85;
  const rightTyreGap = (lap: LapData, t: number) => {
    const s = sampleReplay(lap, track, t);
    const rx = -Math.cos(s.heading), rz = Math.sin(s.heading); // driver's right in XZ
    return roadGaps(road, s.x + halfSpan * rx, s.z + halfSpan * rz).right;
  };

  it("55 s: raw puts the right tyres ~2 m off the road; registered stays near the traced edge (not an independent ground-truth test)", () => {
    expect(rightTyreGap(lap3, 55)).toBeLessThan(-1.5);
    expect(Math.abs(rightTyreGap(registered, 55))).toBeLessThan(0.35);
  });

  it("84 s: the left road-edge gap remains near the historical display target, from about 4.9 m raw", () => {
    const leftGap = (lap: LapData) => {
      const s = sampleReplay(lap, track, 84);
      const rx = -Math.cos(s.heading), rz = Math.sin(s.heading);
      return roadGaps(road, s.x - halfSpan * rx, s.z - halfSpan * rz).left;
    };
    expect(leftGap(lap3)).toBeGreaterThan(4.5);
    expect(leftGap(registered)).toBeGreaterThan(2.7);
    expect(leftGap(registered)).toBeLessThan(3.6);
  });
});
