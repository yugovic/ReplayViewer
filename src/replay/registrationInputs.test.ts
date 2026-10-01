import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash, webcrypto } from "node:crypto";
import { loadInitialReplay, loadLapFile } from "./dataLoader";

const hash = (text: string) => createHash("sha256").update(text).digest("hex");
const track = JSON.stringify({ version: 1, trackId: "barber", origin: { lat: 35, lng: 139, alt: 0 } });
const road = JSON.stringify({ road: [[0, 1, 2, 0, 1, -5, 5]] });
const lap = JSON.stringify({ meta: { race_id: "barber_r1", vehicle_id: "car", lap: 1 },
  t: [0, 1], lat: [35, 35.1], lng: [139, 139.1] });
const record = { vehicle_id: "car", lap: 1, lap_time_seconds: 1, data_file: "lap.json" };
const base = "/data/races/barber_r1";
const roadUrl = "/data/tracks/barber/cg_study/geometry.json";
const trackUrl = "/data/tracks/barber/track.json";

function mockFetch(changed: "lap" | "road" | "track" | null = null) {
  const registration = { version: 1, kind: "lap-translation", raceId: "barber_r1", trackId: "barber",
    limits: { source: "public" + roadUrl, sha256: hash(road) },
    referenceTrack: { source: "public" + trackUrl, sha256: hash(track) },
    laps: [{ vehicleId: "car", lap: 1, offsetMeters: [1, 2], dataSha256: hash(lap) }] };
  const files: Record<string, string> = {
    [trackUrl]: track + (changed === "track" ? " " : ""),
    [roadUrl]: road + (changed === "road" ? " " : ""),
    [base + "/lap.json"]: lap + (changed === "lap" ? " " : ""),
    [base + "/laps.json"]: JSON.stringify({ laps: [record], selected: [record] }),
    [base + "/gps_registration.json"]: JSON.stringify(registration),
  };
  const fetcher = vi.fn(async (url: string) =>
    new Response(files[url] ?? "", { status: files[url] ? 200 : 404 }));
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("crypto", webcrypto);
  return fetcher;
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("registration input loading", () => {
  it("hashes actual served bytes for lap, road and projection reference, then carries them to subsequent laps", async () => {
    const fetcher = mockFetch();
    const initial = await loadInitialReplay();
    expect(initial.lap.registration).toMatchObject({ verified: true, enabled: true,
      inputVerification: { lap: true, limits: true, referenceTrack: true } });
    const another = await loadLapFile("lap.json", false);
    expect(another.registration).toMatchObject({ verified: true, enabled: false });
    expect(fetcher.mock.calls.filter(([url]) => url === roadUrl)).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url === trackUrl)).toHaveLength(1);
  });

  it.each(["lap", "road", "track"] as const)("disables registration if the %s file changed without a refit", async (input) => {
    mockFetch(input);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect((await loadInitialReplay()).lap.registration).toMatchObject({ verified: false, enabled: false });
    expect((await loadLapFile("lap.json", true)).registration).toMatchObject({ verified: false, enabled: false });
  });

  it("labels verification unknown when WebCrypto is absent instead of claiming an input match", async () => {
    mockFetch();
    vi.stubGlobal("crypto", undefined);
    expect((await loadInitialReplay()).lap.registration).toMatchObject({ verified: null, enabled: true,
      inputVerification: { lap: null, limits: null, referenceTrack: null } });
  });

  it("leaves historical alignment URLs outside the registration path", async () => {
    const fetcher = mockFetch();
    vi.stubGlobal("window", { location: { search: "?alignment=local-raw" } });
    expect((await loadInitialReplay()).lap.registration).toBeUndefined();
    expect(fetcher.mock.calls.some(([url]) => url.endsWith("gps_registration.json"))).toBe(false);
    expect(fetcher.mock.calls.some(([url]) => url === roadUrl)).toBe(false);
  });
});
