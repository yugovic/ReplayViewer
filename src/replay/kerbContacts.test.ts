import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  EVENT_DISPLAY_PAD_SECONDS,
  PASS_CLASS_LABELS,
  PASS_SILENT_MAX_PEAK,
  PASS_STRONG_MIN_PEAK,
  STRENGTH_BINS,
  activeKerbContact,
  activePass,
  contactText,
  eventContactClass,
  kerbContactText,
  kerbEventsForLap,
  parseKerbContacts,
  passContactDetail,
  passContactText,
  passLabelsForLap,
  strengthBin,
  type KerbContactEvent,
} from "./kerbContacts";

const readJson = (path: string) => JSON.parse(readFileSync(path, "utf8"));
const RACES = ["fuji_aim_01", "fuji_aim_2020_07_30"] as const;
const zoneIds = new Set<string>(readJson("public/data/tracks/fuji/cg_study/kerb_zones.json").kerbs.map((k: { id: string }) => k.id));

const event = (t0: number, t1: number, peak: number, kerb: string | null = "k13"): KerbContactEvent =>
  ({ t0, t1, peak, bin: strengthBin(peak)!, kerb });

describe("strength bins (弱 1.0–1.5, 中 1.5–3, 強 ≥3)", () => {
  it("bins the roll-rate RMS peak with inclusive lower edges", () => {
    expect(strengthBin(0.99)).toBeNull();
    expect(strengthBin(1.0)).toBe("low");
    expect(strengthBin(1.49)).toBe("low");
    expect(strengthBin(1.5)).toBe("mid");
    expect(strengthBin(2.99)).toBe("mid");
    expect(strengthBin(3.0)).toBe("high");
    expect(strengthBin(8.4)).toBe("high");
    for (const bad of [Number.NaN, Infinity, -1]) expect(strengthBin(bad)).toBeNull();
  });

  it("labels the HUD indicator and the KPI contact cell as class・strength", () => {
    // HUD (event): ≥1.5 deg/s gives its pass "strong" (接触あり); a 1.0–1.5 burst alone leaves it "mild" (判定保留).
    expect(kerbContactText(event(1, 2, 1.2))).toBe("判定保留・弱");
    expect(kerbContactText(event(1, 2, 2.0))).toBe("接触あり・中");
    expect(kerbContactText(event(1, 2, 4.5))).toBe("接触あり・強");
    expect(kerbContactText(null)).toBe("—");
    // KPI cell (pass label)
    expect(passContactText({ class: "silent", bin: null })).toBe("接触なし");
    expect(passContactText({ class: "silent", bin: "high" })).toBe("接触なし");
    expect(passContactText({ class: "mild", bin: null })).toBe("判定保留");
    expect(passContactText({ class: "mild", bin: "low" })).toBe("判定保留・弱");
    expect(passContactText({ class: "strong", bin: "mid" })).toBe("接触あり・中");
    expect(passContactText({ class: "strong", bin: "high" })).toBe("接触あり・強");
    expect(passContactText({ class: "strong", bin: null })).toBe("接触あり");
    expect(passContactText(null)).toBe("—");
  });

  it("keeps class words and strength bins on separate scales (no '強い接触（中）')", () => {
    expect(PASS_CLASS_LABELS).toEqual({ strong: "接触あり", silent: "接触なし", mild: "判定保留" });
    expect(eventContactClass("low")).toBe("mild");
    expect(eventContactClass("mid")).toBe("strong");
    expect(eventContactClass("high")).toBe("strong");
    // the class boundary is the lower edge of 中
    expect(STRENGTH_BINS.find((b) => b.id === "mid")?.minPeak).toBe(PASS_STRONG_MIN_PEAK);
    const classes = ["strong", "silent", "mild"] as const;
    const bins = [null, "low", "mid", "high"] as const;
    for (const cls of classes) {
      for (const bin of bins) {
        const text = contactText(cls, bin);
        const [word, strength, ...rest] = text.split("・");
        expect(word).toBe(PASS_CLASS_LABELS[cls]);
        expect(rest).toEqual([]);
        expect(strength === undefined || ["弱", "中", "強"].includes(strength)).toBe(true);
        expect(text).not.toMatch(/強い|弱い|わずか|振動なし|縁石接触/);
      }
    }
    expect(passContactDetail({ class: "strong", bin: "mid", peakRollRms: 2.345 }))
      .toBe("接触あり・中（振動ピーク 2.35 deg/s、1.5以上の振動あり）");
    expect(passContactDetail({ class: "silent", bin: null, peakRollRms: 0.5 }))
      .toBe("接触なし（振動ピーク 0.50 deg/s、0.8以下）");
    expect(passContactDetail({ class: "mild", bin: "low", peakRollRms: 1.2 })).toMatch(/^判定保留・弱（振動ピーク 1\.20 deg\/s、0\.8〜1\.5/);
    expect(passContactDetail({ class: "mild", bin: null, peakRollRms: null })).toMatch(/^判定保留（振動ピーク不明、/);
    expect(passContactDetail(null)).toBe("IMUラベルなし");
  });
});

describe("HUD indicator: playback time inside a contact event", () => {
  const events = [event(10, 10.6, 1.2), event(10.4, 11, 4.0, "k1"), event(20, 20, 2.0), event(30, 31, 5, null)];

  it("lights inside [t0, t1] and for half the 0.2 s RMS window around it", () => {
    expect(activeKerbContact(events, 10.2)?.peak).toBe(1.2);
    expect(activeKerbContact(events, 10.0 - EVENT_DISPLAY_PAD_SECONDS)?.peak).toBe(1.2);
    expect(activeKerbContact(events, 9.85)).toBeNull();
    // single-sample event stays visible for ±0.1 s
    expect(activeKerbContact(events, 20.05)?.peak).toBe(2.0);
    expect(activeKerbContact(events, 20.2)).toBeNull();
    expect(activeKerbContact(events, 10.2, 0)?.peak).toBe(1.2);
    expect(activeKerbContact(events, 9.95, 0)).toBeNull();
  });

  it("resolves overlaps to the strongest burst and ignores bursts not attributed to a kerb", () => {
    expect(activeKerbContact(events, 10.5)?.kerb).toBe("k1");
    expect(activeKerbContact(events, 30.5)).toBeNull();
    expect(activeKerbContact(events, Number.NaN)).toBeNull();
    expect(activeKerbContact([], 10)).toBeNull();
  });

  it("finds the inside-kerb pass window holding the playback time", () => {
    const labels = [{ kerb: "k13", class: "strong" as const, peakRollRms: 4, bin: "high" as const, t0: 15, t1: 18 }];
    expect(activePass(labels, 16)?.kerb).toBe("k13");
    expect(activePass(labels, 19)).toBeNull();
  });
});

describe("kerb_contacts.json (shipped)", () => {
  it("parses both Fuji races and rejects other races, kinds and the SPA HTML fallback", () => {
    for (const race of RACES) {
      const raw = readJson(`public/data/races/${race}/kerb_contacts.json`);
      expect(parseKerbContacts(raw, race, "fuji")).not.toBeNull();
      expect(parseKerbContacts(raw, race === RACES[0] ? RACES[1] : RACES[0], "fuji")).toBeNull();
      expect(parseKerbContacts(raw, race, "suzuka")).toBeNull();
      expect(parseKerbContacts({ ...raw, kind: "other" }, race, "fuji")).toBeNull();
    }
    for (const bad of [null, "<!doctype html>", {}, 42]) expect(parseKerbContacts(bad, "fuji_aim_01", "fuji")).toBeNull();
  });

  it("publishes no left/right side attribution (review: confounded with corner direction)", () => {
    for (const race of RACES) {
      const text = readFileSync(`public/data/races/${race}/kerb_contacts.json`, "utf8");
      expect(text).not.toMatch(/"side"\s*:/);
      expect(text).not.toMatch(/ripLR/);
    }
  });

  it("covers every distributed lap with one label per inside kerb and kerb ids from kerb_zones.json", () => {
    for (const race of RACES) {
      const file = parseKerbContacts(readJson(`public/data/races/${race}/kerb_contacts.json`), race, "fuji")!;
      const index = readJson(`public/data/races/${race}/laps.json`);
      for (const { lap } of index.selected as { lap: number }[]) {
        const labels = passLabelsForLap(file, race, lap);
        expect(labels.length).toBe(14);
        expect(new Set(labels.map((l) => l.kerb)).size).toBe(14);
        for (const l of labels) {
          expect(zoneIds.has(l.kerb)).toBe(true);
          if (l.class === "silent") {
            expect(l.peakRollRms).toBeLessThanOrEqual(PASS_SILENT_MAX_PEAK);
            expect(l.bin).toBeNull();
          }
          if (l.class === "strong") expect(["mid", "high"]).toContain(l.bin);
        }
      }
    }
  });

  it("names a pass the same in the HUD (strongest inside-pass event) and in the KPI cell", () => {
    let compared = 0;
    for (const race of RACES) {
      const file = parseKerbContacts(readJson(`public/data/races/${race}/kerb_contacts.json`), race, "fuji")!;
      for (const lap of Object.keys(file.laps).map(Number)) {
        const events = kerbEventsForLap(file, race, lap);
        for (const label of passLabelsForLap(file, race, lap)) {
          const strongest = events.filter((e) => e.kerb === label.kerb && e.insidePass)
            .reduce<KerbContactEvent | null>((best, e) => (!best || e.peak > best.peak ? e : best), null);
          if (!strongest) {
            // no event ≥ 1.0 deg/s: nothing lights in the HUD, and the pass is not "接触あり"
            expect(label.class).not.toBe("strong");
            continue;
          }
          expect(kerbContactText(strongest)).toBe(passContactText(label));
          compared += 1;
        }
      }
    }
    expect(compared).toBeGreaterThan(20);
  });

  it("keeps events on the lap's GPS time base with bins derived from the peak", () => {
    for (const race of RACES) {
      const raw = readJson(`public/data/races/${race}/kerb_contacts.json`);
      const file = parseKerbContacts(raw, race, "fuji")!;
      for (const [lap, events] of Object.entries(file.events)) {
        const lapData = readJson(`public/data/races/${race}/osaki_hmr_demio_101_lap_${lap.padStart(3, "0")}.json`);
        const end = lapData.t[lapData.t.length - 1];
        expect(events.length).toBe(raw.events[lap].length);
        for (const e of events) {
          expect(e.t0).toBeGreaterThanOrEqual(0);
          expect(e.t1).toBeLessThanOrEqual(end);
          expect(e.bin).toBe(strengthBin(e.peak));
          if (e.kerb) expect(zoneIds.has(e.kerb)).toBe(true);
        }
        // The 7/30 IMU uses the same per-lap logger-clock shift as the shipped channels (GPS-REG-08).
        const cta = lapData.meta.channel_time_alignment;
        if (cta) expect(file.timeBase?.shiftSecondsByLap?.[lap]).toBeCloseTo(cta.shiftSeconds, 3);
      }
    }
  });

  it("only kerb-attributed events reach the display, and only for the matching race", () => {
    const race = "fuji_aim_2020_07_30";
    const file = parseKerbContacts(readJson(`public/data/races/${race}/kerb_contacts.json`), race, "fuji")!;
    const all = Object.values(file.events).flat();
    const shown = Object.keys(file.events).flatMap((lap) => kerbEventsForLap(file, race, Number(lap)));
    expect(shown.length).toBe(all.filter((e) => e.kerb !== null).length);
    expect(kerbEventsForLap(file, "fuji_aim_01", 2)).toEqual([]);
    expect(kerbEventsForLap(null, race, 2)).toEqual([]);
  });

  it("drops malformed entries and re-derives a wrong bin from the peak", () => {
    const parsed = parseKerbContacts({
      kind: "kerb-contacts", raceId: "r", trackId: "t", version: 2,
      laps: { "1": [{ kerb: "k1", class: "strong", peakRollRms: 2, bin: "mid", t0: 1, t1: 2 },
        { kerb: "../x", class: "strong", peakRollRms: 2, bin: "mid", t0: 1, t1: 2 },
        { kerb: "k2", class: "silent", peakRollRms: 0.5, bin: "high", t0: 3, t1: 4 }], bad: [] },
      events: { "1": [{ t0: 2, t1: 1, peak: 2, kerb: "k1" }, { t0: 1, t1: 1.2, peak: 0.5, kerb: "k1" },
        { t0: 1, t1: 1.2, peak: 3.5, bin: "low", kerb: "k1", side: "left" }] },
    }, "r", "t")!;
    expect(parsed.laps["1"].map((l) => l.kerb)).toEqual(["k1", "k2"]);
    expect(parsed.laps["1"][1].bin).toBeNull();
    expect(parsed.laps.bad).toBeUndefined();
    expect(parsed.events["1"]).toHaveLength(1);
    expect(parsed.events["1"][0].bin).toBe("high");
    expect(parsed.events["1"][0]).not.toHaveProperty("side");
  });
});
