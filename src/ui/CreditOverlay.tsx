import { useEffect, useState } from "react";
import { satelliteMetadataFilename } from "../replay/satelliteVariants";
import { useReplayStore } from "../state/replayStore";
import { buildCreditLine, type CreditLine } from "./credits";

/** Fetch a JSON file's `source` field; resolves null on any failure. */
async function fetchSource(url: string): Promise<string | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    const data = (await response.json()) as { source?: string };
    return typeof data.source === "string" ? data.source : null;
  } catch {
    return null;
  }
}

/** Always-on attribution for the active imagery, terrain, and feature data. */
export function CreditOverlay() {
  const trackId = useReplayStore((state) => state.track?.trackId ?? null);
  const satelliteVariant = useReplayStore((state) => state.satelliteVariant);
  const showFeatures3d = useReplayStore((state) => state.showFeatures3d);
  const [credit, setCredit] = useState<CreditLine | null>(null);

  useEffect(() => {
    if (!trackId) {
      setCredit(null);
      return undefined;
    }
    let cancelled = false;
    const dir = `/data/tracks/${trackId}/`;
    const imageryMeta = satelliteMetadataFilename(satelliteVariant);
    // The 3D-feature attribution lives inside features3d.json (~1 MB), so only
    // pull it when that layer is actually displayed — a user-mode startup with
    // the 3D features off must not download the whole file just for a credit
    // string. The credit re-resolves (adding "Features: …") the moment the
    // layer is toggled on.
    Promise.all([
      fetchSource(`${dir}${imageryMeta}`),
      fetchSource(`${dir}terrain_meta.json`),
      showFeatures3d ? fetchSource(`${dir}features3d.json`) : Promise.resolve(null),
    ])
      .then(([imagerySource, terrainSource, features3dSource]) => {
        if (!cancelled) setCredit(buildCreditLine({ imagerySource, terrainSource, features3dSource }));
      })
      .catch(() => {
        if (!cancelled) setCredit(null);
      });
    return () => {
      cancelled = true;
    };
  }, [trackId, satelliteVariant, showFeatures3d]);

  if (!credit) return null;
  return <div className="hud-credit" title={credit.full}>{credit.visible}</div>;
}
