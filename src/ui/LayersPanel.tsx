import { useReplayStore } from "../state/replayStore";
import { SATELLITE_VARIANTS } from "../replay/satelliteVariants";

export function LayersPanel() {
  const showRoad3d = useReplayStore((state) => state.showRoad3d);
  const showOsmFeatures = useReplayStore((state) => state.showOsmFeatures);
  const showFeatures3d = useReplayStore((state) => state.showFeatures3d);
  const showDetailTexture = useReplayStore((state) => state.showDetailTexture);
  const toggleRoad3d = useReplayStore((state) => state.toggleRoad3d);
  const toggleOsmFeatures = useReplayStore((state) => state.toggleOsmFeatures);
  const toggleFeatures3d = useReplayStore((state) => state.toggleFeatures3d);
  const toggleDetailTexture = useReplayStore((state) => state.toggleDetailTexture);

  const satelliteVariant = useReplayStore((state) => state.satelliteVariant);
  const availableSatelliteVariants = useReplayStore((state) => state.availableSatelliteVariants);
  const setSatelliteVariant = useReplayStore((state) => state.setSatelliteVariant);

  const LAYERS = [
    { label: "3D Road", key: "7", active: showRoad3d, toggle: toggleRoad3d },
    { label: "OSM Features", key: "8", active: showOsmFeatures, toggle: toggleOsmFeatures },
    { label: "3D Features", key: "9", active: showFeatures3d, toggle: toggleFeatures3d },
    { label: "Detail Texture", key: "D", active: showDetailTexture, toggle: toggleDetailTexture },
  ];

  // Only offer variants the availability probe found on the current track
  // (see satelliteVariants.probeSatelliteVariantsForTrack), in a fixed order.
  const satelliteOptions = SATELLITE_VARIANTS.filter((variant) =>
    availableSatelliteVariants.includes(variant.id),
  );

  return (
    <div className="hud-panel hud-layers" role="group" aria-label="Scene layers">
      {LAYERS.map(({ label, key, active, toggle }) => (
        <button
          key={key}
          type="button"
          className={`cam-btn${active ? " cam-btn--active" : ""}`}
          onClick={toggle}
          aria-pressed={active}
          title={`Toggle ${label} [${key}]`}
        >
          <span className="cam-key">{key}</span> {label}
        </button>
      ))}

      {satelliteOptions.length > 1 && (
        <div className="hud-layers-group" role="group" aria-label="Satellite imagery">
          <span className="hud-layers-group-title">
            Satellite <span className="cam-key">0</span>
          </span>
          {satelliteOptions.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              className={`cam-btn${satelliteVariant === id ? " cam-btn--active" : ""}`}
              onClick={() => setSatelliteVariant(id)}
              aria-pressed={satelliteVariant === id}
              title={`Satellite imagery: ${label}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
