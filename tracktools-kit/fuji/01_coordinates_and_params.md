# Fuji — coordinate convention & road parameters

Everything in this kit is in the **viewer's native local frame**, so the finished
glTF drops straight into `public/data/tracks/fuji/scene.glb` with **no ICP /
Z-flip** (unlike the AC-MOD path). Keep this frame end-to-end.

## Local frame (ENU, Y-up — same as the three.js viewer)
- `x` = **east** (metres)
- `y` = **up** (metres, relative to the origin altitude)
- `z` = **north negative** (north is `−z`)
- **Origin**: `lat 35.3717, lng 138.9256, alt 580.94 m ASL`
- Units: **metres**, 1:1.

## Axis round-trip through Blender (do exactly this)
Blender is Z-up; we go out and back through its **Y-up** presets so the numbers
survive unchanged:

| Step | Setting | Effect |
|---|---|---|
| Import `terrain_lidar.obj` | OBJ import **Forward `-Z`, Up `Y`** (Blender's "Y up" default) | ENU `(x, y_up, z)` → Blender `(x, −z, y_up)` |
| Build centerline | `make_centerline_blender.py` (already applies the same map) | lands on the terrain |
| Export finished track | glTF **+Y Up** (default) | Blender → back to ENU `(x, y_up, z)` |

Because import and export use the same Y-up convention, the exported glTF is in
the viewer's ENU and needs no post-transform.

## Road parameters (from the reconstruction)
- **Nominal width**: `13.0 m` (set the ROAD modifier width to this).
  - Note: the AC MOD's geometry measured ~15.8 m median; Fuji's main straight is
    genuinely wider than the technical section. Optional: widen the ROAD width on
    the start/finish straight via the curve-radius / tilt controls, or leave 13 m
    uniform for v1.
- **Camber / banking** (per-station, in `centerline.csv`, column `camber_deg`):
  - range **−4.1° … +3.5°**, mean +0.7°, |median| 1.7°, |p95| 3.6°.
  - Sign: **+ = road's LEFT edge higher** (car leans right), matching the viewer.
  - For v1 you can let the ROAD modifier read camber from the reference terrain
    (recommended — it's LiDAR-true) instead of hand-entering these; the column is
    provided for validation / manual tuning.
- **Elevation**: **source from the reference terrain** (`terrain_lidar.obj`) in
  the ROAD modifier. This is the whole point — it's LiDAR-true, so the road sits
  at the real height (no ±metre MOD error).
- **Total length**: 4556.4 m, 652 centerline points, closed loop.

## Files in this kit
| File | What | Use |
|---|---|---|
| `terrain_lidar.obj` | LiDAR terrain, ENU Y-up, 500×500 grid (~3.4 m), true elevation | Import as **reference terrain** |
| `make_centerline_blender.py` | Blender script → `fuji_centerline` POLY curve (closed) | Road spline input |
| `centerline.csv` | Per-station x/y/z, width, altLeft/Right, camber | Reference / validation |
| `02_textures_and_licensing.md` | CC0 texture sources + license status | Commercial-clean materials |
| `03_tracktools_workbook.md` | Step-by-step build in Blender | The actual workflow |
