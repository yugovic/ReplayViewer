/** Data-source attribution assembled from per-track metadata. */

export interface CreditSources {
  /** Active satellite variant metadata `source`. */
  imagerySource?: string | null;
  /** terrain_meta.json `source`. */
  terrainSource?: string | null;
  /** features3d.json `source`; usually mentions OSM. */
  features3dSource?: string | null;
}

export interface CreditLine {
  visible: string;
  full: string;
}

const OSM_CREDIT = "Map data © OpenStreetMap contributors";

function mentionsOsm(...sources: Array<string | null | undefined>): boolean {
  return sources.some((source) => !!source && /osm|openstreetmap/i.test(source));
}

export function buildCreditLine(sources: CreditSources): CreditLine | null {
  const imagery = (sources.imagerySource ?? "").trim();
  const terrain = (sources.terrainSource ?? "").trim();
  const features3d = (sources.features3dSource ?? "").trim();
  const primary = terrain || features3d;
  if (!imagery && !primary) return null;

  const osm = mentionsOsm(terrain, features3d);
  const visibleParts: string[] = [];
  if (imagery) visibleParts.push(`Imagery: ${imagery}`);
  if (primary) visibleParts.push(`Terrain/Features: ${primary}`);
  if (osm) visibleParts.push(OSM_CREDIT);

  const fullParts: string[] = [];
  if (imagery) fullParts.push(`Imagery: ${imagery}`);
  if (terrain) fullParts.push(`Terrain: ${terrain}`);
  if (features3d) fullParts.push(`Features: ${features3d}`);
  if (osm) fullParts.push(OSM_CREDIT);
  return { visible: visibleParts.join(" · "), full: fullParts.join(" · ") };
}
