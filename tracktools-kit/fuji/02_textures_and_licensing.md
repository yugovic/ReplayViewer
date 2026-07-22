# Textures & licensing (commercial / use-case B)

Goal: a **commercially-clean** material set — no borrowed MOD/Esri/Bing assets.

## License status of what we already have
| Asset | Source | Commercial? | Action |
|---|---|---|---|
| **LiDAR terrain** (`terrain.png` → `terrain_lidar.obj`) | VIRTUAL SHIZUOKA 2019 LP Ground (静岡県) | ✅ **CC BY 4.0** — commercial OK | **Attribute** (see below). Confirmed in `terrain_meta.json`. |
| `satellite.jpg`, `satellite_bing*.jpg` | Esri / Bing tiles | ❌ **not for the product** | Use only as an on-screen **tracing reference** in Blender; do **not** bake into the exported track. Prefer open ortho. |
| `public/data/textures/asphalt_albedo.jpg`, `asphalt_normal.jpg`, `grass_albedo.jpg` | unknown | ⚠️ **verify or replace** | If provenance is unknown, replace with CC0 (below) before shipping commercially. |

**Required attribution string** (put in your product credits / the viewer's
credit overlay):
> Elevation: VIRTUAL SHIZUOKA 2019 LP Ground — © 静岡県, CC BY 4.0.

## CC0 texture sources (fully commercial, no attribution required)
Use these for asphalt, kerbs, grass, concrete, gravel, etc.:
- **ambientCG** — https://ambientcg.com — CC0 PBR. Grab: `Asphalt###`, `Ground###`
  (grass/dirt), `Concrete###`, `Gravel###`. Full PBR maps (albedo/normal/rough/AO).
- **Poly Haven** — https://polyhaven.com/textures — CC0. `asphalt`, `aerial_grass`,
  `concrete`, `rock`.
- Road **markings/curbs**: generate procedurally with Track Tools `MARKINGS` /
  `RACING_KERB` (no texture licensing needed), or CC0 decal atlases.

## Practical material plan for v1
| Surface | Material | Where |
|---|---|---|
| Asphalt | ambientCG `Asphalt` (tiling PBR) | ROAD material |
| Kerbs | red/white — Track Tools `RACING_KERB` + simple 2-colour material | kerb curves |
| Runoff / grass | ambientCG `Ground`/`Grass` | EDGE + terrain |
| Terrain (far) | grass/rock blend, tiled | reference terrain |
| Lane markings | Track Tools `MARKINGS` (procedural) | stacked on ROAD |

> Tiling PBR (not satellite) means the **satellite licensing problem disappears**
> from the product entirely — satellite is only a disposable tracing backdrop.

## What to confirm before commercial ship
1. ✅ LiDAR = CC BY 4.0 (done — in `terrain_meta.json`).
2. ⚠️ Replace/verify `asphalt_*`,`grass_*` in `public/data/textures/` → CC0.
3. ⚠️ Ensure no Esri/Bing pixels are baked into the exported track.
4. ⚠️ No real circuit name/logos/sponsor boards in the product (use generic).
