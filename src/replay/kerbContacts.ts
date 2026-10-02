/**
 * IMU kerb-contact events and labels (public/data/races/<race>/kerb_contacts.json,
 * written by scripts/quality/apex_kpi/export_inputs.py).
 *
 * Events are roll-rate bursts of the logger's own IMU, already on the lap's
 * GPS time base (`t` of the shipped lap JSON = the playback clock). They are
 * GPS-independent facts apart from locating the kerb. No left/right side is
 * shown: the wheel-speed ripple ratio is confounded with the corner
 * direction (docs/fuji-apex-kerb-kpi-2026-10-02.md), so the file carries none.
 */

export type ContactStrengthBin = "low" | "mid" | "high";
export type KerbPassClass = "strong" | "silent" | "mild";

/*
 * Display vocabulary (one meaning per word):
 *   contact CLASS  strong / silent / mild → 接触あり / 接触なし / 判定保留
 *   STRENGTH bin   low / mid / high       → 弱 / 中 / 強 (roll-rate RMS peak)
 * Both are shown side by side as "<class>・<bin>", e.g. "接触あり・中". The
 * class word never encodes a strength ("強い接触（中）" read as a contradiction).
 */

/** Label rule of kerb_contacts.json: a pass is strong when an attributed event peaks ≥ this (deg/s). */
export const PASS_STRONG_MIN_PEAK = 1.5;
/** Label rule: a pass is silent when its roll-rate RMS peak is ≤ this (deg/s); otherwise mild. */
export const PASS_SILENT_MAX_PEAK = 0.8;

export const PASS_CLASS_LABELS: Readonly<Record<KerbPassClass, string>> = {
  strong: "接触あり",
  silent: "接触なし",
  mild: "判定保留",
};

export interface StrengthBinDef {
  id: ContactStrengthBin;
  /** UI label (弱/中/強). */
  label: string;
  /** Inclusive lower edge of the roll-rate RMS peak (deg/s). */
  minPeak: number;
  /** Exclusive upper edge; null = open. */
  maxPeak: number | null;
}

/** Review-agreed display bins: 弱 1.0–1.5, 中 1.5–3, 強 ≥ 3 (deg/s RMS peak). */
export const STRENGTH_BINS: readonly StrengthBinDef[] = [
  { id: "low", label: "弱", minPeak: 1.0, maxPeak: 1.5 },
  { id: "mid", label: "中", minPeak: 1.5, maxPeak: 3.0 },
  { id: "high", label: "強", minPeak: 3.0, maxPeak: null },
];

/**
 * The roll-rate RMS uses a 0.2 s window, so a burst detected on one sample
 * happened within ±0.1 s of it. The HUD keeps an event lit for that half
 * window on either side (single-sample events would otherwise never show).
 */
export const EVENT_DISPLAY_PAD_SECONDS = 0.1;

export interface KerbContactEvent {
  /** Lap t (s) where the RMS first/last exceeds the event threshold. */
  t0: number;
  t1: number;
  /** Lap t (s) of the RMS peak (optional in older files). */
  tPeak?: number;
  /** Roll-rate high-pass RMS peak (deg/s). */
  peak: number;
  /** Always re-derived from `peak` by the parser. */
  bin: ContactStrengthBin;
  /** kerb_zones.json id the burst is attributed to; null = not near a kerb. */
  kerb: string | null;
  /** True when attributed to an inside-kerb (clipping point) pass. */
  insidePass?: boolean;
  stationAtPeak?: number;
}

export interface KerbPassLabel {
  kerb: string;
  class: KerbPassClass;
  peakRollRms: number;
  bin: ContactStrengthBin | null;
  /** Pass window in lap t (s). */
  t0: number;
  t1: number;
}

export interface KerbContactsFile {
  version: number;
  kind: "kerb-contacts";
  raceId: string;
  trackId: string;
  /** Pass labels per lap number (string keys as in JSON). */
  laps: Record<string, KerbPassLabel[]>;
  events: Record<string, KerbContactEvent[]>;
  timeBase?: { shiftSecondsByLap?: Record<string, number>; basis?: string };
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const KERB_ID = /^k[0-9A-Za-z_-]{1,24}$/;
const PASS_CLASSES: readonly KerbPassClass[] = ["strong", "silent", "mild"];

/** Bin of a roll-rate RMS peak; null below the 1.0 deg/s event threshold or for bad input. */
export function strengthBin(peak: number): ContactStrengthBin | null {
  if (!finite(peak)) return null;
  for (const bin of STRENGTH_BINS) {
    if (peak >= bin.minPeak && (bin.maxPeak === null || peak < bin.maxPeak)) return bin.id;
  }
  return null;
}

export function strengthLabel(bin: ContactStrengthBin | null | undefined): string {
  return STRENGTH_BINS.find((b) => b.id === bin)?.label ?? "";
}

function parseEvent(raw: unknown): KerbContactEvent | null {
  const e = raw as Partial<KerbContactEvent> | null;
  if (!e || !finite(e.t0) || !finite(e.t1) || e.t1 < e.t0 || !finite(e.peak)) return null;
  const bin = strengthBin(e.peak);
  if (!bin) return null;
  const kerb = typeof e.kerb === "string" && KERB_ID.test(e.kerb) ? e.kerb : null;
  return {
    t0: e.t0,
    t1: e.t1,
    tPeak: finite(e.tPeak) && e.tPeak >= e.t0 && e.tPeak <= e.t1 ? e.tPeak : undefined,
    peak: e.peak,
    bin,
    kerb,
    insidePass: e.insidePass === true,
    stationAtPeak: finite(e.stationAtPeak) ? e.stationAtPeak : undefined,
  };
}

function parseLabel(raw: unknown): KerbPassLabel | null {
  const l = raw as Partial<KerbPassLabel> | null;
  if (!l || typeof l.kerb !== "string" || !KERB_ID.test(l.kerb) ||
    !PASS_CLASSES.includes(l.class as KerbPassClass) || !finite(l.peakRollRms) ||
    !finite(l.t0) || !finite(l.t1)) return null;
  // A pass bin is the bin of its strongest attributed event; a silent pass has none.
  const bin = l.class !== "silent" && STRENGTH_BINS.some((b) => b.id === l.bin) ? l.bin as ContactStrengthBin : null;
  return { kerb: l.kerb, class: l.class as KerbPassClass, peakRollRms: l.peakRollRms, bin, t0: l.t0, t1: l.t1 };
}

/**
 * Validates kerb_contacts.json for this race/track. Malformed entries are
 * dropped individually; a wrong race/track/kind (or an HTML SPA fallback)
 * rejects the whole file.
 */
export function parseKerbContacts(value: unknown, raceId: string, trackId: string): KerbContactsFile | null {
  const file = value as Partial<KerbContactsFile> | null;
  if (!file || typeof file !== "object" || file.kind !== "kerb-contacts" || file.raceId !== raceId ||
    file.trackId !== trackId || !file.laps || typeof file.laps !== "object") return null;
  const laps: Record<string, KerbPassLabel[]> = {};
  for (const [lap, rows] of Object.entries(file.laps)) {
    if (!/^\d+$/.test(lap) || !Array.isArray(rows)) continue;
    laps[lap] = rows.map(parseLabel).filter((r): r is KerbPassLabel => r !== null);
  }
  const events: Record<string, KerbContactEvent[]> = {};
  for (const [lap, rows] of Object.entries(file.events ?? {})) {
    if (!/^\d+$/.test(lap) || !Array.isArray(rows)) continue;
    events[lap] = rows.map(parseEvent).filter((e): e is KerbContactEvent => e !== null)
      .sort((a, b) => a.t0 - b.t0);
  }
  return {
    version: typeof file.version === "number" ? file.version : 0,
    kind: "kerb-contacts",
    raceId,
    trackId,
    laps,
    events,
    timeBase: file.timeBase,
  };
}

/** Kerb-attributed events of one lap (bursts not near any kerb are not shown as kerb contact). */
export function kerbEventsForLap(file: KerbContactsFile | null, raceId: string | undefined,
  lap: number | undefined): KerbContactEvent[] {
  if (!file || lap === undefined || file.raceId !== raceId) return [];
  return (file.events[String(lap)] ?? []).filter((e) => e.kerb !== null);
}

export function passLabelsForLap(file: KerbContactsFile | null, raceId: string | undefined,
  lap: number | undefined): KerbPassLabel[] {
  if (!file || lap === undefined || file.raceId !== raceId) return [];
  return file.laps[String(lap)] ?? [];
}

/**
 * The contact event lit at playback time `time` (lap t): t0 − pad ≤ time ≤
 * t1 + pad. Overlapping events resolve to the strongest. Null otherwise.
 */
export function activeKerbContact(events: readonly KerbContactEvent[], time: number,
  pad = EVENT_DISPLAY_PAD_SECONDS): KerbContactEvent | null {
  if (!finite(time)) return null;
  let best: KerbContactEvent | null = null;
  for (const e of events) {
    if (e.kerb === null) continue;
    if (time >= e.t0 - pad && time <= e.t1 + pad && (!best || e.peak > best.peak)) best = e;
  }
  return best;
}

/** The inside-kerb pass whose window contains `time`, if any. */
export function activePass(labels: readonly KerbPassLabel[], time: number): KerbPassLabel | null {
  if (!finite(time)) return null;
  return labels.find((l) => time >= l.t0 && time <= l.t1) ?? null;
}

/**
 * Contact class an event of this strength gives the pass it is attributed to
 * (label rule: ≥ 1.5 deg/s = strong; a 1.0–1.5 burst alone leaves it mild).
 * On the shipped files the strongest inside-pass event always has the pass's
 * bin, so the HUD and the KPI table name a pass identically.
 */
export function eventContactClass(bin: ContactStrengthBin): KerbPassClass {
  return bin === "low" ? "mild" : "strong";
}

/** "接触あり・中" / "判定保留・弱" / "判定保留" / "接触なし" (a silent pass has no bin). */
export function contactText(cls: KerbPassClass, bin: ContactStrengthBin | null | undefined): string {
  const strength = cls === "silent" ? "" : strengthLabel(bin);
  return strength ? `${PASS_CLASS_LABELS[cls]}・${strength}` : PASS_CLASS_LABELS[cls];
}

/** HUD text for the lit event, e.g. "接触あり・強" / "判定保留・弱"; "—" when none is lit. */
export function kerbContactText(event: KerbContactEvent | null): string {
  return event ? contactText(eventContactClass(event.bin), event.bin) : "—";
}

/** Short label of a pass's IMU contact for the KPI table (same words as the HUD). */
export function passContactText(label: { class: KerbPassClass; bin: ContactStrengthBin | null } | null | undefined): string {
  return label ? contactText(label.class, label.bin) : "—";
}

/** Tooltip of a pass's contact cell: the label, the peak and the rule that gave the class. */
export function passContactDetail(
  label: { class: KerbPassClass; bin: ContactStrengthBin | null; peakRollRms: number | null } | null | undefined,
): string {
  if (!label) return "IMUラベルなし";
  const peak = label.peakRollRms !== null && finite(label.peakRollRms)
    ? `振動ピーク ${label.peakRollRms.toFixed(2)} deg/s`
    : "振動ピーク不明";
  const rule = label.class === "strong"
    ? `${PASS_STRONG_MIN_PEAK}以上の振動あり`
    : label.class === "silent"
      ? `${PASS_SILENT_MAX_PEAK}以下`
      : `${PASS_SILENT_MAX_PEAK}〜${PASS_STRONG_MIN_PEAK}で接触とも非接触とも言い切れない`;
  return `${passContactText(label)}（${peak}、${rule}）`;
}
