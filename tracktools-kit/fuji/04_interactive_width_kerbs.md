# Interactive workbook — logger-overlay → tune width + kerbs (Blender + Track Tools)

Your scenario, made concrete. The **accurate/data-driven parts are automated**
(path from the logger, height from LiDAR); **you** do only the judgment parts
(width, kerbs, extras) **interactively** — which is where Track Tools works
reliably (the headless material/break issues do NOT happen in the GUI).

**Height is already correct (LiDAR) — never touch Z. You only set width + kerbs.**

## Inputs in this folder
- `../terrain_lidar.obj` — LiDAR terrain (true elevation), reference terrain.
- `../make_centerline_blender.py` — the road spline (`centerline_ROAD_SPLINE`).
- `logger_overlay/make_logger_overlay_blender.py` — builds on the terrain:
  - `centerline_ROAD_SPLINE` (white) — the road spline.
  - `lap1` / `lap2` / `lap3_FASTEST` — the **actual driven lines** (path proof).
  - `racingline_env_L/R` (orange) — envelope of the driven lines. **NB: this is
    ~1.7 m wide = the racing line, NOT the track width** (the driver repeats the
    same line). Use it to see the path, not to set width.
- `logger_overlay/make_satellite_ref_blender.py` — temporarily paints the terrain
  with the satellite image so you can **see the real asphalt edges** to set width.
  (Esri/Bing = reference only; switch back to grass before export.)

## Where width actually comes from
The logger shows the **path**; the **real track width** you read off the
**satellite** (the visible asphalt). Nominal is ~13 m but Fuji's main straight is
wider (~15 m). You widen the ROAD until its edges meet the asphalt in the
satellite. The height stays correct automatically (ROAD shrinkwraps to terrain).

## Steps
1. **Open `fuji_staging.blend`** (saved by `make_track.py` next to this file).
   Terrain, centerline and — with `--tracktools` — the ROAD/MARKINGS/KERB
   modifiers are already set up, in collections `SCENE` / `COLLISION` / `REF`.
   *(Fallback if you have no staging blend: import `terrain_lidar.obj`
   (Up: Y, Forward: −Z) and run `make_centerline_blender.py` by hand.)*
2. (Optional) Scripting → run `logger_overlay/make_logger_overlay_blender.py`
   → driven lines + racing-line envelope on the terrain (path proof).
3. (Optional, for width) run `make_satellite_ref_blender.py` to paint the terrain
   with the satellite so the real edges are visible. Top view (Numpad 7).
4. Select the centerline curve → the **ROAD** modifier:
   - **Shrinkwrap = ON**, **reference terrain = `terrain_lidar`** → height correct, done.
   - **road width**: drag until the road edges match the asphalt in the satellite
     (watch the driven lines sit inside — they should, since drivers stay off the
     edges). Vary per section if needed (Fuji straight wider than the esses).
   - **UVs = ON**, assign an **asphalt (CC0)** material (in the GUI the material
     shows immediately and exports fine).
5. Stack **MARKINGS** (start/finish + lines) and **RACING_KERB** on the same spline
   at the corners. Assign white / red-white materials in the GUI. Keep
   everything that ships in `SCENE`; put **drivable** kerbs in `COLLISION` too.
6. (Optional α) **EDGE** (grass/runoff apron), **BARRIER** (guardrail), **TREES**.
7. **Switch the terrain material back to grass** (CC0) — remove the satellite ref.
   (The export script refuses to run while a satellite texture is assigned.)
8. Export: Scripting tab → run the embedded **`EXPORT_FOR_VIEWER.py`** text
   block. It exports `scene.glb` + the collision glb with the exact headless
   settings **and writes `tuned.json`** — your width/kerb tuning survives any
   later `make_track.py` rebuild (the collision ribbon is rebuilt at the tuned
   width automatically). No manual glTF export dialogs, no settings to get wrong.
9. Verify: `node ../automation/verify_track.js --track fuji --race fuji_aim_01`
   (starts the dev server itself; asserts car-vs-road height numerically and
   drops screenshots in `verify/`). Or eyeball it: `npm run dev`, press **M**.

## Why this is the robust division
| Part | Who | Reliability |
|---|---|---|
| Path (centerline from logger) | automated | ✅ data-driven |
| **Height** | automated (LiDAR shrinkwrap) | ✅ **never revisit** |
| Width | you, from satellite | ✅ human judgment, fast |
| Kerbs / markings / dressing | you, Track Tools GUI | ✅ TT's intended use (no headless bugs) |
| glTF → viewer + collision | automated | ✅ |

Everything stays in ENU → the export drops into the viewer with **no ICP/Z-flip**.
```
