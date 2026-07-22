/**
 * Per-track vector overlay data (Task 2b): hand-authored curb spans that ride
 * the road edge, plus room to grow (future per-span width overrides for the
 * edge lines, etc). Ships as `public/data/tracks/<id>/overlays.json`; a track
 * without the file simply has no curbs (loadOverlays resolves to null, and
 * TrackBuilder treats that as "no overlays" — never an error).
 *
 * These are pure types + a tolerant parser so the schema is unit-testable and
 * so a malformed/partial JSON degrades to "no curbs" instead of throwing in
 * the render loop. The geometry that consumes a CurbSpan lives in
 * curbBuilder.ts.
 */

/** One curb ribbon span along the track.
 *
 * `side` is defined RELATIVE TO INCREASING CENTERLINE DISTANCE: "right" is the
 * driver's right hand (the +normal side in TrackBuilder, i.e. positive signed
 * lateral offset — see projectPointToCenterline), "left" is the driver's left.
 * The apex/inside curb of a right-hand corner is therefore `side: "right"`. */
export interface CurbSpan {
  /** Arc-length (m) along the centerline where the curb starts. */
  startDist: number;
  /** Arc-length (m) where the curb ends (must be > startDist). */
  endDist: number;
  /** Which road edge the curb hugs, relative to increasing distance. */
  side: "left" | "right";
  /** Lateral width of the ribbon outward from the road edge (m). */
  width: number;
  /** Red/white stripe repeat length along the arc (m). */
  stripeLength: number;
}

export interface OverlaysData {
  version: number;
  curbs: CurbSpan[];
}

export const DEFAULT_CURB_WIDTH = 1.2;
export const DEFAULT_STRIPE_LENGTH = 2.5;

/** Coerce one raw JSON object into a valid CurbSpan, or null if unusable. */
export function parseCurbSpan(raw: unknown): CurbSpan | null {
  if (raw == null || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const startDist = Number(o.startDist);
  const endDist = Number(o.endDist);
  if (!Number.isFinite(startDist) || !Number.isFinite(endDist) || endDist <= startDist) {
    return null;
  }
  const side = o.side === "left" || o.side === "right" ? o.side : null;
  if (!side) return null;
  const width = Number.isFinite(Number(o.width)) && Number(o.width) > 0 ? Number(o.width) : DEFAULT_CURB_WIDTH;
  const stripeLength =
    Number.isFinite(Number(o.stripeLength)) && Number(o.stripeLength) > 0
      ? Number(o.stripeLength)
      : DEFAULT_STRIPE_LENGTH;
  return { startDist, endDist, side, width, stripeLength };
}

/** Parse a raw overlays.json payload into a validated OverlaysData. Unknown or
 * malformed entries are dropped; a wholly invalid payload yields an empty
 * curbs list (never throws). */
export function parseOverlays(raw: unknown): OverlaysData {
  if (raw == null || typeof raw !== "object") return { version: 1, curbs: [] };
  const o = raw as Record<string, unknown>;
  const version = Number.isFinite(Number(o.version)) ? Number(o.version) : 1;
  const curbsRaw = Array.isArray(o.curbs) ? o.curbs : [];
  const curbs: CurbSpan[] = [];
  for (const entry of curbsRaw) {
    const span = parseCurbSpan(entry);
    if (span) curbs.push(span);
  }
  return { version, curbs };
}

/**
 * Fetches and parses overlays.json for a track directory. A missing file (404)
 * or any fetch/JSON error resolves to null — the track just has no curbs. Only
 * a successfully parsed, non-empty payload returns data.
 */
export async function loadOverlays(url: string): Promise<OverlaysData | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    const json = (await response.json()) as unknown;
    return parseOverlays(json);
  } catch {
    return null;
  }
}
