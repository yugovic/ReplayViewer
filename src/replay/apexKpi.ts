/**
 * Per-corner clipping-point KPI (public/data/races/<race>/apex_kpi.json,
 * written by scripts/quality/apex_kpi/build_apex_kpi.py with the SHIPPED
 * gps_registration.json).
 *
 * Absolute gaps carry about ±0.7 m (GPS lateral error left after the
 * road-limit registration + 2019 ortho kerb lines). The lap-to-lap delta at
 * the same corner (vs the session median) is the primary comparison.
 */
import type { ContactStrengthBin, KerbPassClass } from "./kerbContacts";
import { STRENGTH_BINS } from "./kerbContacts";

export type LabelCheck = "ok" | "strong-but-off" | "silent-but-deep";

export interface ApexKpiDelta {
  gBlockFront: number | null;
  gBlockRear: number | null;
  gPaintFront: number | null;
  gPaintRear: number | null;
  apexStation: number | null;
  onBlockLengthM: number | null;
  exitOuterGapM: number | null;
}

export interface ApexKpiRow {
  kerb: string;
  /** m; inner tyre outer edge → block start (negative = on the blocks). */
  gBlockFront: number | null;
  gBlockRear: number | null;
  /** m; inner tyre outer edge → paint start (white strip = track limit). */
  gPaintFront: number | null;
  gPaintRear: number | null;
  apexStation: number;
  /** Playback clock (lap t) at the clipping point. */
  apexT: number;
  /** apexT + lap-start offset (HUD lap clock). */
  apexLapTime: number | null;
  onBlockLengthM: number;
  exitOuterGapM: number | null;
  contact: { class: KerbPassClass; bin: ContactStrengthBin | null; peakRollRms: number | null } | null;
  labelCheck: LabelCheck | null;
  deltaVsSessionMedian: ApexKpiDelta;
}

export interface ApexKpiCorner {
  kerb: string;
  name: string;
  order: number;
}

export interface ApexKpiFile {
  version: number;
  kind: "apex-kpi";
  raceId: string;
  trackId: string;
  absoluteUncertaintyMeters: number;
  absoluteUncertaintyReason: string;
  corners: ApexKpiCorner[];
  laps: Record<string, ApexKpiRow[]>;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableFinite = (v: unknown): number | null => (finite(v) ? v : null);
const KERB_ID = /^k[0-9A-Za-z_-]{1,24}$/;
const DELTA_KEYS: (keyof ApexKpiDelta)[] = ["gBlockFront", "gBlockRear", "gPaintFront", "gPaintRear",
  "apexStation", "onBlockLengthM", "exitOuterGapM"];
const CLASSES: readonly KerbPassClass[] = ["strong", "silent", "mild"];
const CHECKS: readonly LabelCheck[] = ["ok", "strong-but-off", "silent-but-deep"];

/** Fallback when a file omits the figure; the review-agreed width. */
export const DEFAULT_ABSOLUTE_UNCERTAINTY_METERS = 0.7;

function parseRow(raw: unknown): ApexKpiRow | null {
  const r = raw as Partial<ApexKpiRow> | null;
  if (!r || typeof r.kerb !== "string" || !KERB_ID.test(r.kerb) || !finite(r.apexStation) ||
    !finite(r.apexT) || !finite(r.onBlockLengthM)) return null;
  const d = (r.deltaVsSessionMedian ?? {}) as Partial<Record<keyof ApexKpiDelta, unknown>>;
  const delta = Object.fromEntries(DELTA_KEYS.map((k) => [k, nullableFinite(d[k])])) as unknown as ApexKpiDelta;
  const c = r.contact as Partial<NonNullable<ApexKpiRow["contact"]>> | null | undefined;
  const contact = c && CLASSES.includes(c.class as KerbPassClass)
    ? {
      class: c.class as KerbPassClass,
      bin: c.class !== "silent" && STRENGTH_BINS.some((b) => b.id === c.bin) ? c.bin as ContactStrengthBin : null,
      peakRollRms: nullableFinite(c.peakRollRms),
    }
    : null;
  return {
    kerb: r.kerb,
    gBlockFront: nullableFinite(r.gBlockFront),
    gBlockRear: nullableFinite(r.gBlockRear),
    gPaintFront: nullableFinite(r.gPaintFront),
    gPaintRear: nullableFinite(r.gPaintRear),
    apexStation: r.apexStation,
    apexT: r.apexT,
    apexLapTime: nullableFinite(r.apexLapTime),
    onBlockLengthM: r.onBlockLengthM,
    exitOuterGapM: nullableFinite(r.exitOuterGapM),
    contact,
    labelCheck: CHECKS.includes(r.labelCheck as LabelCheck) ? r.labelCheck as LabelCheck : null,
    deltaVsSessionMedian: delta,
  };
}

/** Validates apex_kpi.json for this race/track; null for anything else (incl. the SPA HTML fallback). */
export function parseApexKpi(value: unknown, raceId: string, trackId: string): ApexKpiFile | null {
  const file = value as Partial<ApexKpiFile> | null;
  if (!file || typeof file !== "object" || file.kind !== "apex-kpi" || file.raceId !== raceId ||
    file.trackId !== trackId || !file.laps || typeof file.laps !== "object") return null;
  const corners = (Array.isArray(file.corners) ? file.corners : [])
    .filter((c): c is ApexKpiCorner => !!c && typeof c.kerb === "string" && KERB_ID.test(c.kerb) &&
      typeof c.name === "string" && finite(c.order))
    .map((c) => ({ kerb: c.kerb, name: c.name, order: c.order }))
    .sort((a, b) => a.order - b.order);
  const laps: Record<string, ApexKpiRow[]> = {};
  for (const [lap, rows] of Object.entries(file.laps)) {
    if (!/^\d+$/.test(lap) || !Array.isArray(rows)) continue;
    laps[lap] = rows.map(parseRow).filter((r): r is ApexKpiRow => r !== null);
  }
  const unc = file.absoluteUncertaintyMeters;
  return {
    version: typeof file.version === "number" ? file.version : 0,
    kind: "apex-kpi",
    raceId,
    trackId,
    absoluteUncertaintyMeters: finite(unc) && unc > 0 && unc < 10 ? unc : DEFAULT_ABSOLUTE_UNCERTAINTY_METERS,
    absoluteUncertaintyReason: typeof file.absoluteUncertaintyReason === "string" ? file.absoluteUncertaintyReason : "",
    corners,
    laps,
  };
}

/** Rows of one lap in course order (unknown kerbs last). */
export function apexKpiRowsForLap(file: ApexKpiFile | null, raceId: string | undefined,
  lap: number | undefined): ApexKpiRow[] {
  if (!file || lap === undefined || file.raceId !== raceId) return [];
  const order = new Map(file.corners.map((c) => [c.kerb, c.order]));
  return [...(file.laps[String(lap)] ?? [])].sort((a, b) =>
    (order.get(a.kerb) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.kerb) ?? Number.MAX_SAFE_INTEGER) ||
    a.apexStation - b.apexStation);
}

export function cornerName(file: ApexKpiFile | null, kerb: string | null | undefined): string | null {
  if (!file || !kerb) return null;
  return file.corners.find((c) => c.kerb === kerb)?.name ?? null;
}

/** "-0.42" / "1.03"; "—" for missing. */
export function formatMeters(v: number | null | undefined, digits = 2): string {
  if (!finite(v)) return "—";
  const s = v.toFixed(digits);
  return /^-0(\.0*)?$/.test(s) ? s.slice(1) : s;
}

/** Signed delta, "+0.10" / "-0.81" / "±0.00"; "—" for missing. */
export function formatDelta(v: number | null | undefined, digits = 2): string {
  if (!finite(v)) return "—";
  const rounded = Number(v.toFixed(digits));
  if (rounded === 0) return `±${(0).toFixed(digits)}`;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(digits)}`;
}

export function labelCheckText(check: LabelCheck | null): string | null {
  if (check === "strong-but-off") return "IMUは接触ありだが、GPS位置ではブロックから0.15m以上離れている（GPS/コース線の誤差の可能性）";
  if (check === "silent-but-deep") return "IMUは接触なしだが、GPS位置ではブロックに0.55m以上乗っている（GPS/コース線の誤差の可能性）";
  return null;
}
