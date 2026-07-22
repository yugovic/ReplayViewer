# Automated track pipeline — design & status

Turn workbook steps 0–8 (manual Blender clicking) into **one command**:

```
data (LiDAR + GPS)  →  headless Blender (bpy + Track Tools GeoNodes)  →  glTF  →  viewer
```

`python make_track.py --track fuji [--tracktools TT.blend] [--verify]`

## Why this is feasible
- **Track Tools = GeoNodes node-group modifiers.** GeoNodes modifiers are fully
  scriptable: append the node groups from the purchased `.blend`, add them to an
  object, set inputs by socket identifier, apply, export — all via `bpy` in
  `blender --background`. No UI needed.
- **Everything stays in the viewer's ENU** → the exported glTF drops into
  `public/data/tracks/<id>/scene.glb` with **no ICP / Z-flip** (the MOD path's two
  headaches are gone by construction).
- **Data prep is already headless** (`prep_data.py`, Python only) and validated
  (terrain vs road ±0.16 m).

## Pipeline stages
| Stage | Script | Needs | Notes |
|---|---|---|---|
| 1. Prep | `prep_data.py` | Python+numpy+Pillow | skips itself when outputs are fresh (`--force` to override); emits `.npz` terrain cache next to the OBJ |
| 2. Build | `build_track_blender.py` (via `blender -b -P`) | Blender 4.2+ (Track Tools optional) | applies `tuned.json`, saves the staging `.blend` for GUI tuning |
| 3. Verify | `verify_track.js` (Playwright) | Node | numeric car-vs-mesh height assertions; auto-starts the dev server |
| — Orchestrate | `make_track.py` | Python | prints the Blender cmd if Blender is absent |
| — Introspect | `inspect_tracktools.py` | Blender + Track Tools | done once → `tracktools_schema.json` |
| — Textures | `fetch_textures.py` | Python (network) | explicit opt-in CC0 download (ambientCG), cached |

## The GUI round-trip (how tuning survives rebuilds)
The headless build saves `<kit>/<track>_staging.blend`: live modifiers, the
collections `SCENE` / `COLLISION` / `REF`, and an embedded
`EXPORT_FOR_VIEWER.py` text block (`viewer_export.py`, the single source of
truth for export settings). The interactive loop is:

1. open the staging `.blend`, tune road width / kerbs in the GUI
   (keep drivable meshes in `SCENE`; put drivable kerbs in `COLLISION` too),
2. press **Run** on `EXPORT_FOR_VIEWER.py` — it refuses to export while a
   satellite/licensed reference texture is assigned, exports `scene.glb` +
   the collision glb with the exact headless settings, and dumps every
   GeoNodes modifier input to `<kit>/tuned.json`,
3. any later `make_track.py` rebuild re-applies `tuned.json` (matched by node
   group + socket identifier), and the collision ribbon is rebuilt at the
   tuned width — GUI tuning is never lost.

`prep_data.py` never writes `tuned.json`, so re-running prep can't clobber it.
Scene edits that don't serialize (added objects, curve edits) stay
authoritative in the staging `.blend`.

## Road backends (phased quality)
The driver has two backends so the pipeline is provable **before** any purchase:

1. **`native`** (default, no Track Tools): builds a road ribbon directly on the
   LiDAR elevation (left/right edges from the centerline normal, tiling UVs).
   Already **more accurate than the MOD** (true elevation), just visually plain.
   → proves *headless Blender → glTF → viewer with the car on it* works.
2. **`tracktools`** (`--tracktools TT.blend`): appends the ROAD (+MARKINGS/
   RACING_KERB/EDGE) node groups and configures them via **name→socket-identifier
   introspection** (robust to version changes). Adds kerbs, banking, markings,
   dressing = game quality.

## Phased rollout
- **Phase 0 — now (done):** prep + orchestrator + driver scaffolding + introspection helper. Prep validated.
- **Phase 1 — install Blender (no purchase):** `python make_track.py --track fuji`
  runs native backend end-to-end → accurate self-made Fuji in the viewer. **This
  is the cheapest proof the whole (B) stack works.**
- **Phase 2 — buy Track Tools:** run `inspect_tracktools.py` once (dumps real input
  names), tweak the name matches in `build_road_tracktools()`, then
  `--tracktools TT.blend` for full quality (+ BARRIER/TREES as you extend the driver).
- **Phase 3 — AI front-end:** auto-produce the centerline/edges + asset placement
  → feeds `centerline.json` / config (see below).
- **Phase 4 — batch / SaaS:** loop `make_track.py` over many tracks; later wrap as
  a service (headless Blender on a worker).

## Known risks / gotchas (call these out early)
- **Socket identifiers are per-node-group and unknown until purchase** → mitigated
  by `inspect_tracktools.py` + name-based matching. The one input to verify by
  hand: which name the ROAD "reference terrain / source elevation" uses.
- **GeoNodes instances (BARRIER/TREES)**: glTF export must *realize* instances.
  Either `export_apply=True` (already set) realizes them, or convert to mesh
  first. For trees, prefer exporting **positions** and instancing a CC0 tree in
  three.js (`InstancedMesh`) for perf — a viewer-side optimization.
- **Track Tools is interactive-first**; headless use is unofficial. Some modifiers
  may assume UI/asset-browser context → test each in `-b` mode; keep the native
  backend as a guaranteed fallback.
- **Collision mesh**: the driver exports a separate low-poly `road_collision.glb`
  (the road ribbon) for the car's `heightAt` raycast — don't raycast the full scene.
- Pin **Blender 4.5+**; `SURFACE` modifier is broken in TT v2.1 (don't use it).

## Where AI plugs in (the "speedy" part)
The Blender driver is the deterministic back-end. AI accelerates the **front-end**,
all of which just writes `centerline.json` / config:
- **Centerline & road-edge extraction** from GPS/satellite (segmentation / line
  fitting) → replaces the reconstruction-derived centerline, or cleans it.
- **Asset placement** (barriers, buildings, trees) from satellite/OSM → config
  lists of positions for BARRIER/TREES.
- **Material assignment** (which CC0 texture per surface) from imagery.
- **Param search**: since GeoNodes is parametric, an LLM/optimizer can tune
  width/camber/kerb settings against a target.

## Files
```
automation/
  make_track.py            orchestrator CLI (prep → blender → verify)
  prep_data.py             terrain OBJ + centerline.json + build.json (runs now)
  build_track_blender.py   headless bpy driver (native + tracktools backends)
  inspect_tracktools.py    dump TT node-group input schema (post-purchase, once)
  README_automation.md     this file
../<track>/                per-track kit (terrain_lidar.obj, centerline.json, build.json, docs)
../textures_cc0/           put CC0 asphalt/grass PBR maps here (asphalt_albedo.jpg, ...)
```

## Run it
```bash
# one-time (optional): CC0 textures — otherwise surfaces export flat-colour
python fetch_textures.py

# native backend (no Track Tools needed; Blender found automatically):
python make_track.py --track fuji

# full quality with Track Tools (re-applies any tuned.json):
python make_track.py --track fuji --tracktools "C:\path\TrackTools.blend" \
    --verify --race fuji_aim_01

# numeric verification alone (starts the dev server itself):
node verify_track.js --track fuji --race fuji_aim_01
```
