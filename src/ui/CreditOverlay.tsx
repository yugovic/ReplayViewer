import { useEffect, useState } from "react";
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

/**
 * Small always-on data-attribution line in a screen corner (CC BY 4.0 /
 * OpenStreetMap requirement). Text is assembled from the track's
 * terrain_meta.json + features3d.json `source` fields (see buildCreditLine),
 * so it is correct for any track with no per-track code. Hover shows the full
 * multi-source text.
 */
export function CreditOverlay() {
  const trackId = useReplayStore((state) => state.track?.trackId ?? null);
  const [credit, setCredit] = useState<CreditLine | null>(null);

  useEffect(() => {
    if (!trackId) {
      setCredit(null);
      return undefined;
    }
    let cancelled = false;
    const dir = `/data/tracks/${trackId}/`;
    Promise.all([fetchSource(`${dir}terrain_meta.json`), fetchSource(`${dir}features3d.json`)])
      .then(([terrainSource, features3dSource]) => {
        if (cancelled) return;
        setCredit(buildCreditLine({ terrainSource, features3dSource }));
      })
      .catch(() => {
        if (!cancelled) setCredit(null);
      });
    return () => {
      cancelled = true;
    };
  }, [trackId]);

  if (!credit) return null;

  return (
    <div className="hud-credit" title={credit.full}>
      {credit.visible}
    </div>
  );
}
