# Fuji Track Tools kit

Everything needed to build a **commercial-clean, LiDAR-accurate Fuji track** with
**Track Tools v2.1 (FULL)** in Blender and drop it into the viewer — replacing the
AC-MOD-derived `scene.glb`. Use-case **B** (analysis / tool; not branded content).

## Why this is the right path
- **Accuracy**: road elevation comes from LiDAR (VIRTUAL SHIZUOKA, CC BY 4.0).
  Validated: terrain vs reconstruction road match to **±0.16 m**. No ±metre MOD error.
- **Commercial-clean**: no MOD, no Esri/Bing baked in, CC0 textures. Only obligation
  is LiDAR attribution.
- **No coordinate hacks**: stays in the viewer's ENU end-to-end → **no ICP / Z-flip**,
  alignment is exact by construction. (The MOD path needed both.)

## Files (all generated, ready to use)
| File | What |
|---|---|
| `terrain_lidar.obj` | LiDAR terrain, ENU Y-up, 500×500 (~3.4 m), true elevation — **reference terrain** |
| `make_centerline_blender.py` | Blender script → closed `fuji_centerline` road curve |
| `centerline.csv` | Per-station x/y/z, width 13 m, altLeft/Right, camber (−4.1°…+3.5°) |
| `01_coordinates_and_params.md` | Frame, ENU↔Blender round-trip, road params |
| `02_textures_and_licensing.md` | CC0 texture sources, license status, attribution string |
| `03_tracktools_workbook.md` | **Step-by-step Blender build** (ROAD→dress→glTF→viewer) |

## Task list

### ✅ Done (my side, no Track Tools needed)
- [x] Confirm LiDAR license = **CC BY 4.0** (commercial OK) — in `terrain_meta.json`
- [x] Export LiDAR terrain mesh → `terrain_lidar.obj` (validated ±0.16 m)
- [x] Export centerline → `make_centerline_blender.py` + `centerline.csv`
- [x] Coordinate convention + ENU↔Blender round-trip spec (no ICP/Z-flip)
- [x] Road params extracted (width 13 m, camber profile)
- [x] CC0 texture sourcing list + licensing/attribution notes
- [x] Step-by-step Track Tools workbook

### ⬜ After you buy Track Tools FULL ($35)
- [ ] Install Track Tools v2.1 FULL + ACExporter + Asset Browser Link (Blender 4.5+)
- [ ] Download CC0 textures (ambientCG/Poly Haven: asphalt, grass, concrete)
- [ ] Follow `03_tracktools_workbook.md` steps 0–8:
  - [ ] Import terrain (Y-up), run centerline script
  - [ ] ROAD (width 13, **source elevation from reference terrain**) + MARKINGS
  - [ ] RACING_KERB + EDGE (kerbs, runoff)
  - [ ] TERRAIN_GEOMETRY / TERRAIN_SHAPE (integrate terrain)
  - [ ] BARRIER + TREES (dressing)
  - [ ] Export `scene.glb` (+Y up) + a low-poly `road` collision glb
  - [ ] Drop into `public/data/tracks/fuji/`, verify in viewer (press M)

### ⬜ Cleanup for commercial (before shipping)
- [ ] Replace/verify `public/data/textures/asphalt_*`,`grass_*` → CC0
- [ ] Ensure no Esri/Bing pixels baked into the exported track
- [ ] Generic naming/signage (no "Fuji Speedway" logos / sponsor boards)
- [ ] Add LiDAR attribution to the viewer credits:
      *"Elevation: VIRTUAL SHIZUOKA 2019 LP Ground — © 静岡県, CC BY 4.0."*

### ⬜ Later (optional, AI front-end for speed)
- [ ] Auto-extract centerline/edges from GPS/satellite → feed the road curve
- [ ] Auto-place barriers/trees from satellite/OSM
- [ ] Script GeoNodes params for batch/other tracks

## Quick start
1. Buy **Track Tools FULL**, install in Blender 4.5+.
2. Open `03_tracktools_workbook.md`, follow from step 0.
3. First milestone: road on LiDAR terrain, exported glb running in the viewer with
   the car on it — that's the whole (B) stack proven, commercial-clean.
