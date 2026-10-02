import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  REGISTRATION_FILE_BY_VARIANT,
  loadRegistrationVariant,
  registrationVariantText,
  requestedRegistrationVariant,
} from "./registrationVariant";
import { gpsRegistrationInitiallyEnabled } from "./dataLoader";

const readJson = (path: string) => JSON.parse(readFileSync(path, "utf8"));
const RACE = "fuji_aim_2020_07_30";
const BASE = `/data/races/${RACE}`;
const shipped = readJson(`public/data/races/${RACE}/gps_registration.json`);
// Fixture for the opt-in candidate (task D writes the real file): the shipped
// contract with the kerb-contact method and its fitted session translation.
const kerbFixture = {
  ...shipped,
  version: 2,
  status: "track-limit+kerb-contact-fit",
  sessionOffsetMeters: [0.515, -2.185],
  laps: shipped.laps.map((l: { offsetMeters: [number, number] }) => ({ ...l, offsetMeters: [0.515, -2.185] })),
};

/** Fake static host: only the listed files exist; everything else is "absent" (null). */
function host(files: Record<string, unknown>) {
  const requested: string[] = [];
  const fetchOptional = async (url: string) => {
    requested.push(url);
    return url in files ? files[url] : null;
  };
  return { fetchOptional, requested };
}

describe("?gps= registration switch", () => {
  it("parses the URL parameter; only gps=kerb asks for the candidate", () => {
    expect(requestedRegistrationVariant("")).toBe("standard");
    expect(requestedRegistrationVariant("?gps=kerb")).toBe("kerb");
    expect(requestedRegistrationVariant("?track=fuji&gps=kerb")).toBe("kerb");
    expect(requestedRegistrationVariant("?gps=raw")).toBe("standard");
    expect(requestedRegistrationVariant("?gps=KERB")).toBe("standard");
    // the candidate starts switched on like the shipped file; gps=raw still starts off
    expect(gpsRegistrationInitiallyEnabled("?gps=kerb")).toBe(true);
    expect(gpsRegistrationInitiallyEnabled("?gps=raw")).toBe(false);
  });

  it("loads gps_registration_kerb.json when it exists and is valid", async () => {
    const { fetchOptional, requested } = host({
      [`${BASE}/gps_registration_kerb.json`]: kerbFixture,
      [`${BASE}/gps_registration.json`]: shipped,
    });
    const result = await loadRegistrationVariant(fetchOptional, BASE, RACE, "fuji", "kerb");
    expect(result.variant).toBe("kerb");
    expect(result.fellBack).toBe(false);
    expect(result.file?.laps[0].offsetMeters).toEqual([0.515, -2.185]);
    expect(requested).toEqual([`${BASE}/${REGISTRATION_FILE_BY_VARIANT.kerb}`]);
    expect(registrationVariantText(result)).toBe("GPS補正: 縁石接触候補");
  });

  it("falls back to the shipped file when the candidate is missing (404 / SPA HTML)", async () => {
    const { fetchOptional, requested } = host({ [`${BASE}/gps_registration.json`]: shipped });
    const result = await loadRegistrationVariant(fetchOptional, BASE, RACE, "fuji", "kerb");
    expect(result.variant).toBe("standard");
    expect(result.fellBack).toBe(true);
    expect(result.file?.laps[0].offsetMeters).toEqual(shipped.laps[0].offsetMeters);
    expect(requested).toEqual([`${BASE}/gps_registration_kerb.json`, `${BASE}/gps_registration.json`]);
    expect(registrationVariantText(result)).toBe("GPS補正: 標準（縁石候補なし）");
  });

  it("falls back when the candidate belongs to another race or is malformed", async () => {
    for (const bad of [{ ...kerbFixture, raceId: "fuji_aim_01" }, { ...kerbFixture, laps: "x" }, "<!doctype html>"]) {
      const { fetchOptional } = host({ [`${BASE}/gps_registration_kerb.json`]: bad, [`${BASE}/gps_registration.json`]: shipped });
      const result = await loadRegistrationVariant(fetchOptional, BASE, RACE, "fuji", "kerb");
      expect(result.variant).toBe("standard");
      expect(result.fellBack).toBe(true);
    }
  });

  it("never requests the candidate by default", async () => {
    const { fetchOptional, requested } = host({
      [`${BASE}/gps_registration_kerb.json`]: kerbFixture,
      [`${BASE}/gps_registration.json`]: shipped,
    });
    const result = await loadRegistrationVariant(fetchOptional, BASE, RACE, "fuji", "standard");
    expect(result.variant).toBe("standard");
    expect(result.fellBack).toBe(false);
    expect(requested).toEqual([`${BASE}/gps_registration.json`]);
    expect(registrationVariantText(result)).toBe("GPS補正: 標準");
  });

  it("reports no registration when neither file is usable", async () => {
    const { fetchOptional } = host({});
    const result = await loadRegistrationVariant(fetchOptional, BASE, RACE, "fuji", "kerb");
    expect(result.file).toBeNull();
    expect(result.variant).toBeNull();
    expect(registrationVariantText(result)).toBeNull();
    expect(registrationVariantText(null)).toBeNull();
  });
});
