/**
 * Data-source attribution (CC BY 4.0 / OpenStreetMap requirement).
 *
 * The credit line is ASSEMBLED from the `source` strings already present in
 * each track's terrain_meta.json and features3d.json — never hardcoded per
 * track — so adding a track needs no code change here. Pure + side-effect free
 * so it is unit-testable; CreditOverlay handles the fetch/render.
 */

export interface CreditSources {
  /** terrain_meta.json `source` (authoritative elevation/imagery attribution). */
  terrainSource?: string | null;
  /** features3d.json `source` (3D-feature provenance; usually mentions OSM). */
  features3dSource?: string | null;
}

export interface CreditLine {
  /** Short, always-visible line for the HUD corner. */
  visible: string;
  /** Full multi-source text for the title/hover tooltip. */
  full: string;
}

const OSM_CREDIT = "Map data © OpenStreetMap contributors";

function mentionsOsm(...sources: Array<string | null | undefined>): boolean {
  return sources.some((s) => !!s && /osm|openstreetmap/i.test(s));
}

/**
 * Build the credit line from the raw data-file source strings. Returns null
 * when nothing is available (so the overlay can render nothing rather than an
 * empty box). The terrain source is shown verbatim because it is the
 * authoritative attribution (it already carries any license, e.g. the
 * VIRTUAL SHIZUOKA "(CC BY 4.0, 静岡県)" clause) and the data files must not be
 * edited to abbreviate it.
 */
export function buildCreditLine(sources: CreditSources): CreditLine | null {
  const terrain = (sources.terrainSource ?? "").trim();
  const features3d = (sources.features3dSource ?? "").trim();
  const primary = terrain || features3d;
  if (!primary) return null;

  const osm = mentionsOsm(terrain, features3d);
  const visible = `Terrain/Features: ${primary}${osm ? ` · ${OSM_CREDIT}` : ""}`;

  const parts: string[] = [];
  if (terrain) parts.push(`Terrain: ${terrain}`);
  if (features3d) parts.push(`Features: ${features3d}`);
  if (osm) parts.push(OSM_CREDIT);
  const full = parts.join(" · ");

  return { visible, full };
}
