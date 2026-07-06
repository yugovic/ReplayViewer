import { describe, expect, it } from "vitest";
import { latLngToLocal, localToLatLng } from "./projection";
import type { ProjectionOrigin } from "./types";

const ORIGIN: ProjectionOrigin = { lat: 35.3717, lng: 138.9256, alt: 594.15 };
const METERS_PER_DEGREE = 111_320;

describe("latLngToLocal / localToLatLng chirality", () => {
  it("places a point north-east of the origin at x>0 and z<0 (ENU, Y-up, right-handed)", () => {
    // ~0.001 deg north and east of the origin (~111 m north, ~90 m east at
    // this latitude) — an unambiguous NE point.
    const lat = ORIGIN.lat + 0.001;
    const lng = ORIGIN.lng + 0.001;
    const { x, z } = latLngToLocal(lat, lng, ORIGIN);

    // East -> +X.
    expect(x).toBeGreaterThan(0);
    // North -> -Z (the whole point of this fix): a real-world right-handed
    // ENU frame only matches three.js's right-handed XYZ (Y-up) when north
    // maps to -Z. Mapping north to +Z instead mirrors the world.
    expect(z).toBeLessThan(0);
  });

  it("places a point south-west of the origin at x<0 and z>0", () => {
    const lat = ORIGIN.lat - 0.001;
    const lng = ORIGIN.lng - 0.001;
    const { x, z } = latLngToLocal(lat, lng, ORIGIN);
    expect(x).toBeLessThan(0);
    expect(z).toBeGreaterThan(0);
  });

  it("east X × north (-Z) = up (+Y): a real right turn stays a right turn in scene space", () => {
    // Simulate a car driving due north then taking a real-world right turn
    // (heading swings from north to east — right turn from the driver's seat).
    const north = { lat: ORIGIN.lat + 0.001, lng: ORIGIN.lng };
    const afterRightTurn = { lat: ORIGIN.lat + 0.001, lng: ORIGIN.lng + 0.001 };

    const p0 = latLngToLocal(ORIGIN.lat, ORIGIN.lng, ORIGIN);
    const p1 = latLngToLocal(north.lat, north.lng, ORIGIN);
    const p2 = latLngToLocal(afterRightTurn.lat, afterRightTurn.lng, ORIGIN);

    // Heading in scene space: driving from p0 to p1 (due north) is the -Z
    // direction (forward = (0, 0, -1)-ish); turning right should curve
    // toward +X (east), matching a driver's right hand while heading -Z.
    const fwd1 = { x: p1.x - p0.x, z: p1.z - p0.z };
    const fwd2 = { x: p2.x - p1.x, z: p2.z - p1.z };
    // fwd1 ~ (0, -A) heading north (-Z), fwd2 ~ (+B, 0) heading east (+X)
    // after the right turn: fwd1.x*fwd2.z - fwd1.z*fwd2.x = 0 - (-A)(B) = AB
    // > 0. A real left turn (north then west) gives the opposite sign.
    const turn = fwd1.x * fwd2.z - fwd1.z * fwd2.x;
    expect(turn).toBeGreaterThan(0);
  });

  it("localToLatLng is the exact inverse of latLngToLocal", () => {
    const samples: Array<[number, number]> = [
      [ORIGIN.lat, ORIGIN.lng],
      [ORIGIN.lat + 0.01, ORIGIN.lng + 0.01],
      [ORIGIN.lat - 0.02, ORIGIN.lng + 0.015],
      [ORIGIN.lat + 0.005, ORIGIN.lng - 0.03],
    ];
    for (const [lat, lng] of samples) {
      const local = latLngToLocal(lat, lng, ORIGIN);
      const roundTrip = localToLatLng(local.x, local.z, ORIGIN);
      expect(roundTrip.lat).toBeCloseTo(lat, 10);
      expect(roundTrip.lng).toBeCloseTo(lng, 10);
    }
  });

  it("localToLatLng negates z's contribution to latitude (north = -z)", () => {
    // A point 500 m "north" of the origin (in real-world terms) must land at
    // negative z, and localToLatLng must read that same negative z back as
    // increased latitude.
    const z = -500;
    const { lat } = localToLatLng(0, z, ORIGIN);
    expect(lat).toBeCloseTo(ORIGIN.lat + 500 / METERS_PER_DEGREE, 10);
  });
});
