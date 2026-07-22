# Track Tools workbook — build the Fuji track (Blender 4.5+, FULL pack)

Goal: turn the LiDAR terrain + centerline into a textured, LiDAR-accurate track
and export a glTF that drops into the viewer at
`public/data/tracks/fuji/scene.glb`. No MOD, commercial-clean.

Prereqs: **Blender 4.5+**, **Track Tools v2.1 FULL** installed (+ ACExporter,
Asset Browser Link). CC0 textures downloaded (see `02_textures_and_licensing.md`).

---

## 0. Scene setup
1. New Blender file. Units: Metric, scale 1.0. Clip end ≥ 20000 m.
2. (Recommended) Save the file in a working folder before adding heavy geometry.

## 1. Import the reference terrain
3. `File > Import > Wavefront (.obj)` → `terrain_lidar.obj`.
   - In the import panel set **Up: Y, Forward: -Z** (Blender's Y-up default).
4. The object `terrain_lidar` appears at real elevation. This is your
   **reference terrain** for every "source elevation from terrain" option.
   *(Optional: assign a tiling grass/rock CC0 material now, or later.)*

## 2. Build the centerline curve
5. `Scripting` tab → `New` → paste `make_centerline_blender.py` → **Run**.
   - Creates closed POLY curve `fuji_centerline` on the terrain.
6. (Optional) `SUBD_CURVE` / `SHRINKWRAP_CURVE` (SNAP_TO group) to smooth the
   curve and shrinkwrap it exactly onto `terrain_lidar` if needed.

## 3. ROAD (the core)
7. Select `fuji_centerline`, add the **ROAD** GeoNodes modifier.
   - **Width** = 13 m (see params doc; widen main straight later if wanted).
   - **Resolution**: moderate while working, raise for final.
   - **★ Source elevation from reference terrain** = ON, target `terrain_lidar`.
     → road now follows the LiDAR height (accurate, no MOD error).
   - **Camber/banking**: either leave terrain-driven, or dial from `centerline.csv`.
   - Assign the **asphalt (CC0)** material, generate UVs.
   - Note the outputs: **middle** and **border** curves (reused below).
8. **MARKINGS** modifier stacked on ROAD → start/finish + lane lines (procedural).

## 4. Kerbs & edges
9. **RACING_KERB** on the border curves at corners (red/white 2-colour material).
10. **EDGE** around the road border → grass/runoff apron mesh (CC0 grass material).

## 5. Terrain integration
11. **TERRAIN_GEOMETRY** on `terrain_lidar`: adaptive subdivision near the road,
    delete far geometry, **shrinkwrap to reference terrain** (keeps it tidy/light).
12. **TERRAIN_SHAPE**: smooth terrain around the road; cut holes only if a
    bridge/tunnel is needed (Fuji GP: not required).

## 6. Trackside dressing (FULL)
13. **BARRIER** along border curves → guardrail / tyre wall + instance lamp/post
    objects (CC0 or your own assets).
14. **TREES**: scatter trees using a mask (road/paddock excluded). Use the
    satellite JPG *as a temporary backdrop only* to place tree zones; the trees
    themselves are your CC0 assets.
15. (Optional) **PARKING_LINES / ISLANDS** for the paddock.

## 7. Export for the viewer  ← primary target
16. Organise objects into collections: `road`, `kerbs`, `edges`, `terrain`,
    `barriers`, `trees`.
17. **Apply** the GeoNodes modifiers (or use **FILTER_PARTS** to separate) so the
    geometry is real mesh.
18. `File > Export > glTF 2.0 (.glb)`:
    - **+Y Up** (default) → returns to viewer ENU.
    - Include: Selected Objects (the collections above), Apply Modifiers,
      Materials + Images (embed), UVs, Normals.
    - Save as `scene.glb`.
19. Also export a **low-poly road+kerb-only** glb as the collision surface
    (name it `road_ac_local.glb` to match the current viewer wiring, or rename
    the loader). This is what the car raycasts for grounding.
20. Copy both into `public/data/tracks/fuji/`, replacing the MOD-derived files.

## 8. Verify in the viewer
21. `npm run dev` → open Fuji → press **M** (Drive on AC → will be renamed
    "MOD/AC scene"; it shows `scene.glb` and grounds on the road collision).
22. Confirm: road at LiDAR elevation, car on the road, no ±metre offset. Because
    everything stayed in ENU, **no ICP/Z-flip is involved** — alignment is exact.

---

## Notes / gotchas (from the product's known issues)
- Curve-to-mesh can glitch on **sharp angles**; increase curve resolution or
  soften tight kinks in `fuji_centerline`.
- Auto-smoothing can leave bumps on **steep + tight** combos — check the downhill
  esses.
- Large maps are heavy: work with low subdivisions, raise only for final export;
  disable unused collections.
- `SURFACE` modifier is **broken in this build** — don't rely on it for collision;
  export a separate low-res road mesh instead (step 19).

## Where AI speeds this up later (optional, your front-end)
- Auto-extract/clean the **centerline & road edges** from GPS/satellite → feeds
  step 2 automatically.
- Auto-place **barriers/trees** from satellite/OSM → feeds steps 13–14.
- Script GeoNodes params (headless) for batch/other tracks.
