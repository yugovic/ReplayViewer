import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  apexKpiRowsForLap,
  cornerName,
  formatDelta,
  formatMeters,
  labelCheckText,
  parseApexKpi,
} from "./apexKpi";
import { parseKerbContacts, passLabelsForLap } from "./kerbContacts";

const readJson = (path: string) => JSON.parse(readFileSync(path, "utf8"));
const RACES = ["fuji_aim_01", "fuji_aim_2020_07_30"] as const;
const INSIDE = ["k13", "k1", "k15", "k3", "k18", "k19", "kDunlopR", "k7", "k20", "k21", "k9", "k10", "k23", "k24"];

describe("apex_kpi.json (shipped)", () => {
  it("parses both Fuji races only for their own race/track", () => {
    for (const race of RACES) {
      const raw = readJson(`public/data/races/${race}/apex_kpi.json`);
      expect(parseApexKpi(raw, race, "fuji")).not.toBeNull();
      expect(parseApexKpi(raw, race === RACES[0] ? RACES[1] : RACES[0], "fuji")).toBeNull();
      expect(parseApexKpi({ ...raw, kind: "kerb-contacts" }, race, "fuji")).toBeNull();
    }
    for (const bad of [null, "<!doctype html>", {}, []]) expect(parseApexKpi(bad, "fuji_aim_01", "fuji")).toBeNull();
  });

  it("states the ±0.7 m absolute uncertainty with its reason and the delta as primary comparison", () => {
    for (const race of RACES) {
      const raw = readJson(`public/data/races/${race}/apex_kpi.json`);
      const file = parseApexKpi(raw, race, "fuji")!;
      expect(file.absoluteUncertaintyMeters).toBe(0.7);
      expect(file.absoluteUncertaintyReason).toMatch(/registration/);
      expect(raw.primaryComparison).toBe("deltaVsSessionMedian");
      // computed with the SHIPPED registration, whose hash it records
      const reg = readFileSync(`public/data/races/${race}/gps_registration.json`);
      expect(raw.registration.source).toBe(`public/data/races/${race}/gps_registration.json`);
      expect(raw.registration.sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(reg.length).toBeGreaterThan(0);
    }
  });

  it("has one row per inside kerb and lap, in course order, with readable corner names", () => {
    for (const race of RACES) {
      const file = parseApexKpi(readJson(`public/data/races/${race}/apex_kpi.json`), race, "fuji")!;
      const index = readJson(`public/data/races/${race}/laps.json`);
      expect(file.corners.map((c) => c.kerb)).toEqual(INSIDE);
      for (const { lap } of index.selected as { lap: number }[]) {
        const rows = apexKpiRowsForLap(file, race, lap);
        expect(rows.map((r) => r.kerb)).toEqual(INSIDE);
        for (let i = 1; i < rows.length; i++) expect(rows[i].apexStation).toBeGreaterThan(rows[i - 1].apexStation);
        for (const r of rows) {
          expect(r.gBlockFront).not.toBeNull();
          // the paint (white strip) starts road-side of the blocks
          expect(r.gPaintFront!).toBeLessThanOrEqual(r.gBlockFront! + 0.05);
          expect(r.onBlockLengthM).toBeGreaterThanOrEqual(0);
          if (r.gBlockFront! > 0) expect(r.onBlockLengthM).toBe(0);
        }
      }
      expect(cornerName(file, "k13")).toBe("1コーナー");
      expect(cornerName(file, "k99")).toBeNull();
    }
  });

  it("deltas are value minus the per-corner session median", () => {
    for (const race of RACES) {
      const raw = readJson(`public/data/races/${race}/apex_kpi.json`);
      const file = parseApexKpi(raw, race, "fuji")!;
      for (const rows of Object.values(file.laps)) {
        for (const r of rows) {
          const med = raw.sessionMedian[r.kerb];
          expect(r.deltaVsSessionMedian.gBlockFront!).toBeCloseTo(r.gBlockFront! - med.gBlockFront, 1);
          expect(r.deltaVsSessionMedian.apexStation!).toBeCloseTo(r.apexStation - med.apexStation, 0);
        }
      }
    }
  });

  it("carries the same IMU labels as kerb_contacts.json", () => {
    for (const race of RACES) {
      const kpi = parseApexKpi(readJson(`public/data/races/${race}/apex_kpi.json`), race, "fuji")!;
      const contacts = parseKerbContacts(readJson(`public/data/races/${race}/kerb_contacts.json`), race, "fuji")!;
      for (const lap of Object.keys(kpi.laps).map(Number)) {
        const labels = new Map(passLabelsForLap(contacts, race, lap).map((l) => [l.kerb, l]));
        for (const r of apexKpiRowsForLap(kpi, race, lap)) {
          expect(r.contact?.class).toBe(labels.get(r.kerb)?.class);
          expect(r.contact?.bin ?? null).toBe(labels.get(r.kerb)?.bin ?? null);
        }
      }
    }
  });

  it("flags label/geometry disagreements without hiding the row", () => {
    const race = "fuji_aim_2020_07_30";
    const file = parseApexKpi(readJson(`public/data/races/${race}/apex_kpi.json`), race, "fuji")!;
    const flagged = Object.entries(file.laps).flatMap(([lap, rows]) =>
      rows.filter((r) => r.labelCheck && r.labelCheck !== "ok").map((r) => `${lap}:${r.kerb}:${r.labelCheck}`));
    // Known shipped-registration contradictions. 2:k23 appeared with the iteration-2 re-timing
    // (2026-10-02): the lap-2 4.48 deg/s burst moved 1.2 m past the midpoint of the k10/k23 overlap,
    // inside the flagged lap-2 GPS anomaly (3250-3900 m); laps 2's k21/k23 rows are anomaly-sector rows.
    expect(flagged.sort()).toEqual(["2:k21:strong-but-off", "2:k23:strong-but-off", "5:k10:strong-but-off", "5:k9:silent-but-deep"]);
    expect(labelCheckText("strong-but-off")).toMatch(/GPS/);
    expect(labelCheckText("ok")).toBeNull();
  });
});

describe("KPI number formatting", () => {
  it("formats metres and signed deltas", () => {
    expect(formatMeters(-0.4249)).toBe("-0.42");
    expect(formatMeters(-0.001)).toBe("0.00");
    expect(formatMeters(null)).toBe("—");
    expect(formatMeters(12.345, 1)).toBe("12.3");
    expect(formatDelta(0.1)).toBe("+0.10");
    expect(formatDelta(-0.81)).toBe("-0.81");
    expect(formatDelta(0.004)).toBe("±0.00");
    expect(formatDelta(-1.84, 1)).toBe("-1.8");
    expect(formatDelta(undefined)).toBe("—");
  });

  it("drops malformed rows and keeps unknown kerbs last", () => {
    const file = parseApexKpi({
      kind: "apex-kpi", raceId: "r", trackId: "t", version: 1, absoluteUncertaintyMeters: -1,
      corners: [{ kerb: "k2", name: "B", order: 1 }, { kerb: "k1", name: "A", order: 0 }],
      laps: { "1": [
        { kerb: "kX", apexStation: 5, apexT: 1, onBlockLengthM: 0, gBlockFront: 1 },
        { kerb: "k2", apexStation: 20, apexT: 3, onBlockLengthM: 0, gBlockFront: "x" },
        { kerb: "k1", apexStation: 10, apexT: 2, onBlockLengthM: 1.5, gBlockFront: -0.2,
          contact: { class: "strong", bin: "mid", peakRollRms: 2 }, deltaVsSessionMedian: { gBlockFront: -0.1 } },
        { kerb: "k3", apexStation: Number.NaN, apexT: 2, onBlockLengthM: 0 },
      ] },
    }, "r", "t")!;
    expect(file.absoluteUncertaintyMeters).toBe(0.7);
    const rows = apexKpiRowsForLap(file, "r", 1);
    expect(rows.map((r) => r.kerb)).toEqual(["k1", "k2", "kX"]);
    expect(rows[1].gBlockFront).toBeNull();
    expect(rows[0].deltaVsSessionMedian.gBlockFront).toBe(-0.1);
    expect(rows[0].deltaVsSessionMedian.apexStation).toBeNull();
    expect(apexKpiRowsForLap(file, "other", 1)).toEqual([]);
  });
});
