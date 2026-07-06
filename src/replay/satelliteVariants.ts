/**
 * Satellite imagery variants: sibling JPGs of `satellite.jpg` in each track's
 * data directory, all sharing the same geographic bbox/mesh (verified
 * upstream — texture swap only, no geometry rebuild needed). See
 * TrackBuilder.buildSatelliteGround for how the texture is applied.
 */

export type SatVariantId = "default" | "sr" | "bing" | "bing_sr";

export interface SatelliteVariantDef {
  id: SatVariantId;
  /** Short UI label shown on the variant button. */
  label: string;
  /** Filename inside `/data/tracks/<trackId>/`. */
  filename: string;
}

/** Every known variant, in the order they should render as buttons. */
export const SATELLITE_VARIANTS: readonly SatelliteVariantDef[] = [
  { id: "default", label: "Esri", filename: "satellite.jpg" },
  { id: "sr", label: "Esri SR", filename: "satellite_sr.jpg" },
  { id: "bing", label: "Bing", filename: "satellite_bing.jpg" },
  { id: "bing_sr", label: "Bing SR", filename: "satellite_bing_sr.jpg" },
];

const DEFAULT_VARIANT: SatVariantId = "default";

/** URL param value ("?sat=...") -> variant id. No entry for "default": the
 * absence of the param (or an unrecognized value) already resolves to it. */
const SAT_PARAM_TO_VARIANT: Record<string, SatVariantId> = {
  sr: "sr",
  bing: "bing",
  bing_sr: "bing_sr",
};

/** Parses the `?sat=` query param into a variant id. Pure/DOM-free so it is
 * unit-testable; unknown or absent values fall back to "default". */
export function parseSatVariantParam(value: string | null): SatVariantId {
  if (value && Object.prototype.hasOwnProperty.call(SAT_PARAM_TO_VARIANT, value)) {
    return SAT_PARAM_TO_VARIANT[value];
  }
  return DEFAULT_VARIANT;
}

export function satelliteFilename(id: SatVariantId): string {
  return SATELLITE_VARIANTS.find((v) => v.id === id)?.filename ?? "satellite.jpg";
}

/**
 * Pure decision: does this HTTP response mean "the image file exists"?
 *
 * Vite's dev-server SPA fallback returns HTTP 200 with `text/html` (the app
 * shell) for paths that don't match a real file on disk, so a bare status
 * check isn't enough — an available image must be status 200 AND a
 * `Content-Type` that starts with `image/`.
 *
 * Kept free of any fetch/network call so it's trivially unit-testable.
 */
export function isImageResponseAvailable(status: number, contentType: string | null): boolean {
  return status === 200 && typeof contentType === "string" && contentType.toLowerCase().startsWith("image/");
}

/** Minimal shape probeSatelliteVariants needs from a single-URL fetch — kept
 * separate from the decision logic above so tests can supply a fake without
 * touching the network, and the real implementation only has to adapt the
 * Fetch API's Response to this shape (see fetchProbe below). */
export type VariantProbe = (url: string) => Promise<{ status: number; contentType: string | null }>;

/**
 * Decides which of SATELLITE_VARIANTS exist for a given track directory by
 * probing each candidate URL. "default" (satellite.jpg) is guaranteed to
 * exist for every track (see TrackBuilder), so it's always reported
 * available even if the probe itself is somehow inconclusive — this keeps
 * the UI from ever showing zero satellite options.
 *
 * A single probe failure (network error, etc.) only removes that one
 * variant; it never rejects the whole probe.
 */
export async function probeSatelliteVariants(
  trackDir: string,
  probe: VariantProbe,
): Promise<SatVariantId[]> {
  const dir = trackDir.endsWith("/") ? trackDir : `${trackDir}/`;
  const flags = await Promise.all(
    SATELLITE_VARIANTS.map(async (variant) => {
      try {
        const { status, contentType } = await probe(`${dir}${variant.filename}`);
        return isImageResponseAvailable(status, contentType);
      } catch {
        return false;
      }
    }),
  );
  const available = new Set<SatVariantId>(
    SATELLITE_VARIANTS.filter((_, i) => flags[i]).map((v) => v.id),
  );
  available.add(DEFAULT_VARIANT);
  // Preserve SATELLITE_VARIANTS order for stable UI button ordering.
  return SATELLITE_VARIANTS.map((v) => v.id).filter((id) => available.has(id));
}

/** Real network-backed probe: a HEAD request per candidate file. */
const fetchProbe: VariantProbe = async (url) => {
  const res = await fetch(url, { method: "HEAD" });
  return { status: res.status, contentType: res.headers.get("content-type") };
};

/** Probes real variant availability for a track directory (e.g.
 * `/data/tracks/barber/`) using the browser's fetch. */
export function probeSatelliteVariantsForTrack(trackDir: string): Promise<SatVariantId[]> {
  return probeSatelliteVariants(trackDir, fetchProbe);
}
