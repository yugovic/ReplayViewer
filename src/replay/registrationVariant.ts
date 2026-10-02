/**
 * Which GPS registration the viewer loads.
 *
 *   (default)  gps_registration.json        shipped track-limit fit ("標準")
 *   ?gps=kerb  gps_registration_kerb.json   opt-in 7/30 candidate fitted with
 *                                            the IMU kerb-contact labels
 *                                            ("縁石接触候補"); falls back to the
 *                                            shipped file when absent/invalid
 *   ?gps=raw   shipped file, switched off at start (unchanged behaviour)
 *
 * The candidate never replaces the shipped file (review decision 2026-10-02).
 */
import { parseGpsRegistration, type GpsRegistrationFile } from "./gpsRegistration";

export type GpsRegistrationVariant = "standard" | "kerb";

export const REGISTRATION_FILE_BY_VARIANT: Readonly<Record<GpsRegistrationVariant, string>> = {
  standard: "gps_registration.json",
  kerb: "gps_registration_kerb.json",
};

export const REGISTRATION_VARIANT_LABEL: Readonly<Record<GpsRegistrationVariant, string>> = {
  standard: "標準",
  kerb: "縁石接触候補",
};

export interface RegistrationLoadResult {
  file: GpsRegistrationFile | null;
  /** Variant actually loaded; null when no valid registration was found. */
  variant: GpsRegistrationVariant | null;
  /** Variant the URL asked for. */
  requested: GpsRegistrationVariant;
  /** True when ?gps=kerb was requested but the candidate was missing/invalid. */
  fellBack: boolean;
}

/** `?gps=kerb` requests the kerb-contact candidate; anything else the shipped file. */
export function requestedRegistrationVariant(search: string): GpsRegistrationVariant {
  return new URLSearchParams(search).get("gps") === "kerb" ? "kerb" : "standard";
}

/**
 * Tries the requested file, then the shipped one. `fetchOptional` resolves
 * null for a missing file (404 or SPA HTML fallback), so a candidate that does
 * not exist for this race silently degrades to the shipped registration.
 */
export async function loadRegistrationVariant(
  fetchOptional: (url: string) => Promise<unknown>,
  raceBaseUrl: string,
  raceId: string,
  trackId: string,
  requested: GpsRegistrationVariant,
): Promise<RegistrationLoadResult> {
  const order: GpsRegistrationVariant[] = requested === "standard" ? ["standard"] : [requested, "standard"];
  for (const variant of order) {
    const file = parseGpsRegistration(await fetchOptional(`${raceBaseUrl}/${REGISTRATION_FILE_BY_VARIANT[variant]}`),
      raceId, trackId);
    if (file) return { file, variant, requested, fellBack: variant !== requested };
  }
  return { file: null, variant: null, requested, fellBack: requested !== "standard" };
}

/** HUD text, e.g. "GPS補正: 標準" / "GPS補正: 縁石接触候補". */
export function registrationVariantText(result: Pick<RegistrationLoadResult, "variant" | "fellBack"> | null): string | null {
  if (!result?.variant) return null;
  return `GPS補正: ${REGISTRATION_VARIANT_LABEL[result.variant]}${result.fellBack ? "（縁石候補なし）" : ""}`;
}
