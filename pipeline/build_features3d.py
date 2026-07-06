#!/usr/bin/env python3
"""Extract 3-D features (trees / buildings / barriers) with measured
above-ground heights into public/data/tracks/{barber,fuji}/features3d.json.

This is the P3a data-generation task (spec: specs/p3a_features3d_extraction.md).
Rendering (P3b) is a separate task and only consumes the JSON produced here.

Pipeline
--------
1. Load the track's terrain.png (Terrarium raster, ~1 m/px) as a bare-earth
   elevation grid. This is the SAME node-registered EPSG:3857 grid the viewer
   samples (see populate_terrain_elevation.py / groundMath.ts).
2. Build a canopy/roof top-surface (nDSM) on the exact same grid:
     - Fuji  : cached VIRTUAL SHIZUOKA 2019 LiDAR zips, class-1 (above-ground)
               points, EPSG:6676 -> 3857, per-cell MAX Z.
     - Barber: USGS 3DEP EPT (AL_11County_2_B23, EPSG:3857, laszip). Only the
               octree nodes whose cube intersects the satellite bbox are
               downloaded (cached, retried). class-1 (unclassified/above-ground)
               points, per-cell MAX Z.
   nDSM = MAX point Z (per cell) - terrain elevation (same cell). Cells with
   nDSM < 0 or > 60 m, or with no points, are void.
3. Fetch OSM footprints/linework (Overpass, cached, retried; same idioms as
   fetch_osm_features.py) for buildings, barriers and tree regions, project to
   local scene XZ metres (equirectangular, origin = track.json origin, NORTH =
   -Z) using the exact projection of fetch_osm_features.py.
4. Trees: scan wood/forest polygons on an ~8 m jittered grid; place one tree per
   cell whose local nDSM (p75 of the covered 1 m cells) exceeds 3 m. tree_row
   ways are sampled every 5 m; tree nodes are placed as-is. Total capped at
   20 000 / track (grid auto-coarsened, deterministically).
   Buildings: height = p90 of footprint nDSM cells (>=10 cells), else OSM
   height / building:levels * 3.2, else 6.0 m.
   Barriers: stored as XZ polylines with per-type default heights.

Chirality: the projection is asserted at startup (north -> -Z, east -> +X,
round-trip) and the projected features are confirmed to fall inside the
satellite bbox rectangle. Footprint rings are normalised CCW when viewed from
+Y (operationally: shoelace sum(x_i*z_{i+1}-x_{i+1}*z_i) < 0, because
X_hat x Z_hat = -Y_hat).

The output is byte-deterministic: point binning is order-independent (max),
jitter uses a fixed per-cell seed, and every list is sorted and rounded before
serialisation.

Usage:
    python3 pipeline/build_features3d.py --track fuji
    python3 pipeline/build_features3d.py --track barber
    python3 pipeline/build_features3d.py --track barber --validate   # schema check only
    python3 pipeline/build_features3d.py --track fuji --self-test    # projection checks only
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path


def _pip_install(pkg: str) -> None:
    print(f"{pkg} not found, installing with 'pip install --user {pkg}'...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", pkg], check=True)


try:
    import numpy as np
except ImportError:
    _pip_install("numpy")
    import numpy as np

try:
    from PIL import Image
except ImportError:
    _pip_install("pillow")
    from PIL import Image

try:
    import laspy  # noqa: F401  (imported lazily where used, but validate presence early)
except ImportError:
    _pip_install("laspy[lazrs]")
    import laspy  # noqa: F401

try:
    from pyproj import Transformer
except ImportError:
    _pip_install("pyproj")
    from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "pipeline"
CACHE_DIR = PIPELINE_DIR / "cache"
TRACKS_DIR = PROJECT_ROOT / "public" / "data" / "tracks"

EARTH_METERS_PER_DEGREE = 111_320.0  # matches fetch_osm_features.py / projection.ts
R_MERC = 6378137.0  # EPSG:3857 sphere radius

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "replay-viewer-v2 features3d fetch/1.0"
RETRIES = 4

# --- extraction parameters (spec) ---
NDSM_MIN = 0.0
NDSM_MAX = 60.0
TREE_NDSM_MIN = 3.0
TREE_GRID_M = 8.0
TREE_ROW_STEP_M = 5.0
TREE_DEFAULT_H = 10.0
TREE_CAP = 20_000
CROWN_MIN, CROWN_MAX, CROWN_FACTOR = 2.0, 6.0, 0.35
BUILDING_MIN_CELLS = 10
BUILDING_DEFAULT_H = 6.0
LEVEL_HEIGHT_M = 3.2
BARRIER_DEFAULT_H = {
    "guard_rail": 0.75,
    "fence": 2.0,
    "wall": 1.5,
    "retaining_wall": 2.0,
}
JITTER_SEED = 0x3A11B0  # fixed seed -> deterministic jitter
OSM_MARGIN_M = 40.0  # query margin; output geometry is clipped to the satellite bbox

# --- track-corridor clearance (P3c) ---
TREE_CLEARANCE_M = 8.0   # extra gap beyond half-width + crown before a tree may stand
BUILDING_CLEARANCE_M = 2.0  # conservative: only drops footprints hugging the track edge

# Barber USGS 3DEP EPT (verified 2026-07-05).
EPT_BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/AL_11County_2_B23"
EPT_MAX_DEPTH = 11  # ~10 pts/m^2 over the bbox, ~88 MB (see REPORT). 5 GB guard below.
EPT_SIZE_GUARD_BYTES = 5 * 1024 ** 3

FUJI_SRC_CRS = "EPSG:6676"
DST_CRS = "EPSG:3857"
ABOVE_GROUND_CLASS = 1  # ASPRS "unclassified"; both datasets put canopy/roofs here
GROUND_CLASS = 2
POINT_CHUNK = 4_000_000


TRACK_CONFIG = {
    "fuji": {
        "height_source": "shizuoka",
        "source_label": "VIRTUAL SHIZUOKA 2019 LP (CC BY 4.0, 静岡県) class-1 nDSM + OSM",
        "lp_cache": CACHE_DIR / "shizuoka_lp",
    },
    "barber": {
        "height_source": "ept",
        "source_label": "USGS 3DEP EPT AL_11County_2_B23 class-1 nDSM + OSM",
        "ept_cache": CACHE_DIR / "usgs_ept",
    },
}


# ======================================================================
# Projection (identical to fetch_osm_features.py mainline; north -> -Z)
# ======================================================================

def project(lat: float, lng: float, origin: dict) -> tuple[float, float]:
    """ENU, Y-up, right-handed local projection: east -> +x, north -> -z."""
    lng_scale = EARTH_METERS_PER_DEGREE * math.cos(math.radians(origin["lat"]))
    return (lng - origin["lng"]) * lng_scale, -(lat - origin["lat"]) * EARTH_METERS_PER_DEGREE


def unproject(x: float, z: float, origin: dict) -> tuple[float, float]:
    lng_scale = EARTH_METERS_PER_DEGREE * math.cos(math.radians(origin["lat"]))
    lng = origin["lng"] + x / lng_scale
    lat = origin["lat"] - z / EARTH_METERS_PER_DEGREE
    return lat, lng


def lnglat_to_3857(lng: float, lat: float) -> tuple[float, float]:
    x = math.radians(lng) * R_MERC
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_MERC
    return x, y


def run_self_test(origin: dict, bbox: dict) -> None:
    """Assert the chirality/round-trip invariants every run (mirror-bug guard)."""
    print("Projection self-test (north=-Z, east=+X):")
    dn = 100.0 / EARTH_METERS_PER_DEGREE  # 100 m north
    xn, zn = project(origin["lat"] + dn, origin["lng"], origin)
    assert abs(xn) < 1e-6 and zn < 0, f"north must map to -Z (got x={xn}, z={zn})"
    de = 100.0 / (EARTH_METERS_PER_DEGREE * math.cos(math.radians(origin["lat"])))
    xe, ze = project(origin["lat"], origin["lng"] + de, origin)
    assert xe > 0 and abs(ze) < 1e-6, f"east must map to +X (got x={xe}, z={ze})"
    print(f"  +100m north -> (x={xn:+.2f}, z={zn:+.2f})  [z<0 OK]")
    print(f"  +100m east  -> (x={xe:+.2f}, z={ze:+.2f})  [x>0 OK]")
    # round-trip
    lat2, lng2 = unproject(xe, ze, origin)
    assert abs(lat2 - origin["lat"]) < 1e-9 and abs(lng2 - (origin["lng"] + de)) < 1e-9
    # bbox orientation: NE corner -> (+x, -z), SW corner -> (-x, +z) about origin
    x_ne, z_ne = project(bbox["maxLat"], bbox["maxLng"], origin)
    x_sw, z_sw = project(bbox["minLat"], bbox["minLng"], origin)
    assert x_ne > 0 > x_sw, "east/west parity broken vs satellite bbox"
    assert z_ne < 0 < z_sw, "north/south parity broken vs satellite bbox (mirror!)"
    print(f"  bbox NE -> (x={x_ne:+.1f}, z={z_ne:+.1f}); SW -> (x={x_sw:+.1f}, z={z_sw:+.1f})")
    print("  round-trip + bbox parity OK")


# ======================================================================
# Track-corridor clearance (P3c): distance to the centerline polyline
# ======================================================================

def load_centerline_xz(track_doc: dict) -> tuple[np.ndarray, np.ndarray]:
    """Local-scene XZ of the track centerline (closed loop). Uses the same
    x/z the viewer consumes (track.json centerline[].x/z)."""
    cl = track_doc.get("centerline") or track_doc.get("points") or []
    xs = np.asarray([float(p["x"]) for p in cl], dtype=np.float64)
    zs = np.asarray([float(p["z"]) for p in cl], dtype=np.float64)
    return xs, zs


def centerline_distance(px, pz, cxs: np.ndarray, czs: np.ndarray) -> np.ndarray:
    """Minimum distance from each query point (px, pz) to the closed centerline
    polyline. Vectorised over the query points; loops over the ~600 segments so
    peak memory stays O(N). Deterministic (pure numpy, no RNG)."""
    px = np.asarray(px, dtype=np.float64)
    pz = np.asarray(pz, dtype=np.float64)
    best = np.full(px.shape, np.inf, dtype=np.float64)
    n = len(cxs)
    if n == 0:
        return best
    for i in range(n):
        ax, az = cxs[i], czs[i]
        bx, bz = cxs[(i + 1) % n], czs[(i + 1) % n]
        dx, dz = bx - ax, bz - az
        l2 = dx * dx + dz * dz
        if l2 == 0.0:
            d = np.hypot(px - ax, pz - az)
        else:
            t = ((px - ax) * dx + (pz - az) * dz) / l2
            np.clip(t, 0.0, 1.0, out=t)
            d = np.hypot(px - (ax + t * dx), pz - (az + t * dz))
        np.minimum(best, d, out=best)
    return best


def run_clearance_self_test() -> None:
    """Verify the corridor exclusion boundary on a synthetic straight centerline."""
    print("Clearance self-test (straight centerline, known distances):")
    # centerline: straight segment along +X at z=0 (closed loop degenerates to
    # the same line back), so distance for x in [0,100] is |z|.
    cxs = np.array([0.0, 100.0], dtype=np.float64)
    czs = np.array([0.0, 0.0], dtype=np.float64)
    px = np.array([50.0, 50.0, 50.0, 50.0], dtype=np.float64)
    pz = np.array([5.0, 16.9, 17.1, 30.0], dtype=np.float64)
    d = centerline_distance(px, pz, cxs, czs)
    assert np.allclose(d, np.abs(pz)), f"distance to straight line wrong: {d}"
    # tree exclusion: keep iff d >= width/2 + clearance + crown
    width, clearance, crown = 12.0, 8.0, 3.0
    thr = width / 2.0 + clearance + crown  # = 17.0
    keep = d >= thr
    assert list(keep) == [False, False, True, True], f"tree boundary wrong: {keep.tolist()}"
    # building exclusion: drop iff any vertex d < width/2 + 2
    bthr = width / 2.0 + BUILDING_CLEARANCE_M  # = 8.0
    assert (7.9 < bthr) and (8.1 >= bthr), "building boundary threshold wrong"
    # endcap: point beyond the segment end clamps to the vertex distance
    d_end = centerline_distance(np.array([103.0]), np.array([4.0]), cxs, czs)
    assert abs(float(d_end[0]) - 5.0) < 1e-9, f"segment endcap wrong: {d_end}"
    print("  straight-line distances, tree/building boundaries, endcap OK")


# ======================================================================
# Terrain raster + nDSM grid (EPSG:3857, node-registered to the bbox)
# ======================================================================

def decode_terrarium_array(rgb: np.ndarray) -> np.ndarray:
    r = rgb[..., 0].astype(np.float64)
    g = rgb[..., 1].astype(np.float64)
    b = rgb[..., 2].astype(np.float64)
    return r * 256.0 + g + b / 256.0 - 32768.0


class HeightGrid:
    """Terrain elevations + a same-grid nDSM top-surface, node-registered to the
    satellite bbox in EPSG:3857 (col 0 center = minLng, row 0 center = maxLat)."""

    def __init__(self, terrain_png: Path, bbox: dict) -> None:
        with Image.open(terrain_png) as img:
            rgb = np.asarray(img.convert("RGB"))
        self.h, self.w = rgb.shape[0], rgb.shape[1]
        self.terrain = decode_terrarium_array(rgb)  # (H, W) float64, metres ASL
        self.bbox = bbox
        self.min_lng, self.max_lng = bbox["minLng"], bbox["maxLng"]
        self.max_lat, self.min_lat = bbox["maxLat"], bbox["minLat"]
        # EPSG:3857 extent of the pixel-centre grid.
        self.x_min, self.y_north = lnglat_to_3857(self.min_lng, self.max_lat)
        self.x_max, self.y_south = lnglat_to_3857(self.max_lng, self.min_lat)
        self.dx = (self.x_max - self.x_min) / (self.w - 1)
        self.dy = (self.y_north - self.y_south) / (self.h - 1)
        cos_lat = math.cos(math.radians((self.min_lat + self.max_lat) / 2))
        self.m_per_px = (self.x_max - self.x_min) * cos_lat / (self.w - 1)  # ground metres/px
        self.max_z = np.full((self.h, self.w), -np.inf, dtype=np.float64)
        self.point_count = 0

    def bin_points_3857(self, mx: np.ndarray, my: np.ndarray, mz: np.ndarray) -> int:
        """Accumulate per-cell MAX Z from EPSG:3857 point arrays."""
        col = np.rint((mx - self.x_min) / self.dx).astype(np.int64)
        row = np.rint((self.y_north - my) / self.dy).astype(np.int64)
        inside = (col >= 0) & (col < self.w) & (row >= 0) & (row < self.h)
        if not inside.any():
            return 0
        flat = (row[inside] * self.w + col[inside])
        z = mz[inside].astype(np.float64)
        np.maximum.at(self.max_z.reshape(-1), flat, z)
        n = int(inside.sum())
        self.point_count += n
        return n

    def finalize_ndsm(self) -> np.ndarray:
        """nDSM = MAX point Z - terrain; void where no points or out of [0,60]."""
        has_pt = np.isfinite(self.max_z)
        ndsm = np.where(has_pt, self.max_z - self.terrain, np.nan)
        with np.errstate(invalid="ignore"):
            bad = (~has_pt) | (ndsm < NDSM_MIN) | (ndsm > NDSM_MAX)
        ndsm = np.where(bad, np.nan, ndsm)
        self.ndsm = ndsm
        return ndsm

    # --- sampling in pixel space from local scene XZ ---
    def _xz_to_px(self, x: float, z: float, origin: dict) -> tuple[float, float]:
        lat, lng = unproject(x, z, origin)
        u = (lng - self.min_lng) / (self.max_lng - self.min_lng)

        def merc_y(deg: float) -> float:
            return math.asinh(math.tan(math.radians(deg)))

        yn, ysth = merc_y(self.max_lat), merc_y(self.min_lat)
        v = (yn - merc_y(lat)) / (yn - ysth)
        return u * (self.w - 1), v * (self.h - 1)

    def ndsm_window_percentile(self, x: float, z: float, origin: dict,
                               half_px: int, pct: float) -> float | None:
        px, py = self._xz_to_px(x, z, origin)
        c, r = int(round(px)), int(round(py))
        c0, c1 = max(0, c - half_px), min(self.w - 1, c + half_px)
        r0, r1 = max(0, r - half_px), min(self.h - 1, r + half_px)
        if c0 > c1 or r0 > r1:
            return None
        block = self.ndsm[r0:r1 + 1, c0:c1 + 1]
        vals = block[np.isfinite(block)]
        if vals.size == 0:
            return None
        return float(np.percentile(vals, pct))


# ======================================================================
# Barber: USGS 3DEP EPT download (bbox-intersecting nodes only)
# ======================================================================

def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def _http_bytes(url: str) -> bytes:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"download failed after {RETRIES} tries: {url} ({last})")


def ept_intersecting_nodes(bbox: dict, ept: dict, cache: Path,
                           max_depth: int) -> list[str]:
    """Octree walk: node keys (d-x-y-z) whose cube intersects bbox+margin, d<=max_depth."""
    b = ept["bounds"]
    cube_min = (b[0], b[1]); cube_size = b[3] - b[0]
    dlat = OSM_MARGIN_M / EARTH_METERS_PER_DEGREE
    midlat = (bbox["minLat"] + bbox["maxLat"]) / 2
    dlng = OSM_MARGIN_M / (EARTH_METERS_PER_DEGREE * math.cos(math.radians(midlat)))
    x0, y0 = lnglat_to_3857(bbox["minLng"] - dlng, bbox["minLat"] - dlat)
    x1, y1 = lnglat_to_3857(bbox["maxLng"] + dlng, bbox["maxLat"] + dlat)
    qxmin, qxmax = min(x0, x1), max(x0, x1)
    qymin, qymax = min(y0, y1), max(y0, y1)

    hier_dir = cache / "hierarchy"
    hier_dir.mkdir(parents=True, exist_ok=True)

    def load_hier(key: str) -> dict:
        f = hier_dir / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text())
        data = _http_json(EPT_BASE + f"/ept-hierarchy/{key}.json")
        f.write_text(json.dumps(data))
        return data

    hier: dict[str, int] = dict(load_hier("0-0-0-0"))
    loaded = {"0-0-0-0"}

    def hit(d, x, y, z) -> bool:
        step = cube_size / (2 ** d)
        nx = cube_min[0] + x * step
        ny = cube_min[1] + y * step
        return not (nx > qxmax or nx + step < qxmin or ny > qymax or ny + step < qymin)

    keys: list[str] = []
    stack = [(0, 0, 0, 0)]
    while stack:
        d, x, y, z = stack.pop()
        k = f"{d}-{x}-{y}-{z}"
        if k not in hier:
            continue
        if not hit(d, x, y, z):
            continue
        if hier[k] < 0:  # nested hierarchy subtree root
            if k not in loaded:
                hier.update(load_hier(k))
                loaded.add(k)
            if hier.get(k, 0) < 0:
                continue
        keys.append(k)
        if d < max_depth:
            for dx in (0, 1):
                for dy in (0, 1):
                    for dz in (0, 1):
                        stack.append((d + 1, x * 2 + dx, y * 2 + dy, z * 2 + dz))
    keys.sort()
    return keys


def fill_grid_from_ept(grid: HeightGrid, bbox: dict, cache: Path,
                       max_depth: int) -> dict:
    ept_meta_f = cache / "ept.json"
    cache.mkdir(parents=True, exist_ok=True)
    if ept_meta_f.exists():
        ept = json.loads(ept_meta_f.read_text())
    else:
        ept = _http_json(EPT_BASE + "/ept.json")
        ept_meta_f.write_text(json.dumps(ept))
    assert ept["srs"]["horizontal"] == "3857", "EPT SRS is not EPSG:3857"

    keys = ept_intersecting_nodes(bbox, ept, cache, max_depth)
    print(f"EPT: {len(keys)} bbox-intersecting nodes to depth {max_depth}")
    data_dir = cache / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    class_hist: Counter[int] = Counter()
    dl_bytes = 0
    n_downloaded = 0
    for i, k in enumerate(keys, 1):
        f = data_dir / f"{k}.laz"
        if f.exists():
            raw = f.read_bytes()
        else:
            raw = _http_bytes(EPT_BASE + f"/ept-data/{k}.laz")
            f.write_bytes(raw)
            n_downloaded += 1
        dl_bytes += len(raw)
        if dl_bytes > EPT_SIZE_GUARD_BYTES:
            raise SystemExit(
                f"EPT download exceeded 5 GB guard at node {k}; lower --ept-depth."
            )
        las = laspy.read(io.BytesIO(raw))
        cls = np.asarray(las.classification)
        for v, c in zip(*np.unique(cls, return_counts=True)):
            class_hist[int(v)] += int(c)
        keep = cls == ABOVE_GROUND_CLASS
        if keep.any():
            grid.bin_points_3857(
                np.asarray(las.x)[keep], np.asarray(las.y)[keep], np.asarray(las.z)[keep]
            )
        if i % 50 == 0 or i == len(keys):
            print(f"  [{i}/{len(keys)}] cumulative binned {grid.point_count:,} class-1 pts, "
                  f"{dl_bytes/1e6:.1f} MB read ({n_downloaded} newly downloaded)")
    return {
        "class_hist": dict(sorted(class_hist.items())),
        "nodes": len(keys),
        "dl_bytes": dl_bytes,
        "downloaded_nodes": n_downloaded,
        "max_depth": max_depth,
    }


# ======================================================================
# Fuji: cached VIRTUAL SHIZUOKA zips
# ======================================================================

def read_las_from_zip(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if n.lower().endswith((".las", ".laz")):
                return laspy.read(io.BytesIO(zf.read(n)))
    raise RuntimeError(f"no .las/.laz in {zip_path.name}")


def fill_grid_from_shizuoka(grid: HeightGrid, lp_cache: Path) -> dict:
    zips = sorted(lp_cache.glob("*.zip"))
    if not zips:
        raise SystemExit(f"no cached Shizuoka zips in {lp_cache} (run fetch_shizuoka_dtm.py first)")
    print(f"Shizuoka: reading {len(zips)} cached mesh zips (class-1 above-ground points)")
    transformer = Transformer.from_crs(FUJI_SRC_CRS, DST_CRS, always_xy=True)
    class_hist: Counter[int] = Counter()
    total = 0
    for i, zp in enumerate(zips, 1):
        las = read_las_from_zip(zp)
        n = las.header.point_count
        total += n
        cls = np.asarray(las.classification)
        for v, c in zip(*np.unique(cls, return_counts=True)):
            class_hist[int(v)] += int(c)
        x_all, y_all, z_all = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        for start in range(0, n, POINT_CHUNK):
            end = min(start + POINT_CHUNK, n)
            keep = cls[start:end] == ABOVE_GROUND_CLASS
            if not keep.any():
                continue
            sx, sy, sz = x_all[start:end][keep], y_all[start:end][keep], z_all[start:end][keep]
            mx, my = transformer.transform(sx, sy)
            grid.bin_points_3857(mx, my, sz)
        del las, x_all, y_all, z_all, cls
        if i % 10 == 0 or i == len(zips):
            print(f"  [{i}/{len(zips)}] {zp.name}: read {total:,} pts, "
                  f"binned {grid.point_count:,} class-1 in-bbox")
    return {
        "class_hist": dict(sorted(class_hist.items())),
        "total_read": total,
    }


# ======================================================================
# OSM (Overpass) fetch + projection
# ======================================================================

def build_overpass_query(bbox: dict, margin: float) -> tuple[str, dict]:
    mid_lat = (bbox["minLat"] + bbox["maxLat"]) / 2.0
    dlat = margin / EARTH_METERS_PER_DEGREE
    dlng = margin / (EARTH_METERS_PER_DEGREE * math.cos(math.radians(mid_lat)))
    q = {
        "south": bbox["minLat"] - dlat, "north": bbox["maxLat"] + dlat,
        "west": bbox["minLng"] - dlng, "east": bbox["maxLng"] + dlng,
    }
    bb = f"({q['south']:.6f},{q['west']:.6f},{q['north']:.6f},{q['east']:.6f})"
    selectors = [
        f'way["building"]{bb}',
        f'relation["building"]{bb}',
        f'way["barrier"~"^(guard_rail|fence|wall|retaining_wall)$"]{bb}',
        f'way["natural"~"^(wood|tree_row)$"]{bb}',
        f'node["natural"="tree"]{bb}',
        f'way["landuse"="forest"]{bb}',
        f'relation["natural"="wood"]{bb}',
        f'relation["landuse"="forest"]{bb}',
    ]
    body = "".join(f"  {s};\n" for s in selectors)
    return f"[out:json][timeout:90];\n(\n{body});\nout geom;\n", q


def fetch_overpass(query: str, cache: Path, refresh: bool) -> dict:
    if cache.exists() and not refresh:
        print(f"Using cached Overpass response: {cache.name}")
        return json.loads(cache.read_text())
    last = ""
    for attempt in range(RETRIES):
        res = subprocess.run(
            ["curl", "-sf", "-A", USER_AGENT, OVERPASS_URL, "--data-urlencode", f"data={query}"],
            capture_output=True, text=True, timeout=180,
        )
        if res.returncode == 0:
            try:
                raw = json.loads(res.stdout)
            except json.JSONDecodeError:
                last = f"non-JSON (server busy): {res.stdout[:120]!r}"
            else:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(raw, indent=2) + "\n")
                print(f"Fetched {len(raw.get('elements', []))} OSM elements, cached {cache.name}")
                return raw
        else:
            last = f"curl exit {res.returncode}: {res.stderr[:200]}"
        if attempt < RETRIES - 1:
            time.sleep(2.0 * (attempt + 1))
    raise SystemExit(f"Overpass unreachable after {RETRIES} attempts ({last}); nothing written.")


def polygon_signed_area_xz(xz: list) -> float:
    s = 0.0
    n = len(xz)
    for i in range(n):
        x1, z1 = xz[i]
        x2, z2 = xz[(i + 1) % n]
        s += x1 * z2 - x2 * z1
    return s / 2.0


def normalize_ccw(xz: list) -> list:
    """CCW viewed from +Y => shoelace sum(x_i*z_{i+1}-x_{i+1}*z_i) < 0
    (X_hat x Z_hat = -Y_hat). Also rotate to start at the min (x,z) vertex so the
    ring is canonical/deterministic regardless of OSM start vertex."""
    if len(xz) < 3:
        return xz
    if polygon_signed_area_xz(xz) > 0:
        xz = xz[::-1]
    start = min(range(len(xz)), key=lambda i: (xz[i][0], xz[i][1]))
    return xz[start:] + xz[:start]


def assemble_outer_rings(members: list) -> list:
    segments = []
    for m in members:
        if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry"):
            segments.append([(g["lat"], g["lon"]) for g in m["geometry"]])
    rings = []
    while segments:
        ring = segments.pop(0)
        progress = True
        while progress and ring[0] != ring[-1]:
            progress = False
            for i, seg in enumerate(segments):
                if seg[0] == ring[-1]:
                    ring = ring + seg[1:]
                elif seg[-1] == ring[-1]:
                    ring = ring + seg[-2::-1]
                elif seg[-1] == ring[0]:
                    ring = seg[:-1] + ring
                elif seg[0] == ring[0]:
                    ring = seg[::-1][:-1] + ring
                else:
                    continue
                segments.pop(i)
                progress = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring[:-1])
    return rings


def clip_to_rect(poly, x_min, z_min, x_max, z_max):
    """Sutherland-Hodgman clip to the satellite bbox rect (same as fetch_osm_features)."""
    def x_cross(a, b, x):
        t = (x - a[0]) / (b[0] - a[0]); return (x, a[1] + (b[1] - a[1]) * t)

    def z_cross(a, b, z):
        t = (z - a[1]) / (b[1] - a[1]); return (a[0] + (b[0] - a[0]) * t, z)

    pts = poly
    for inside, intersect in (
        (lambda p: p[0] >= x_min, lambda a, b: x_cross(a, b, x_min)),
        (lambda p: p[0] <= x_max, lambda a, b: x_cross(a, b, x_max)),
        (lambda p: p[1] >= z_min, lambda a, b: z_cross(a, b, z_min)),
        (lambda p: p[1] <= z_max, lambda a, b: z_cross(a, b, z_max)),
    ):
        if not pts:
            return []
        out = []
        for i in range(len(pts)):
            cur, nxt = pts[i], pts[(i + 1) % len(pts)]
            if inside(cur):
                out.append(cur)
                if not inside(nxt):
                    out.append(intersect(cur, nxt))
            elif inside(nxt):
                out.append(intersect(cur, nxt))
        pts = out
    return pts


def clip_polyline_to_rect(pts, x_min, z_min, x_max, z_max):
    """Liang-Barsky clip of an open polyline; returns list of surviving segments."""
    segs = []
    cur = []
    for i in range(len(pts) - 1):
        seg = _clip_segment(pts[i], pts[i + 1], x_min, z_min, x_max, z_max)
        if seg is None:
            if len(cur) >= 2:
                segs.append(cur)
            cur = []
            continue
        a, b = seg
        if not cur:
            cur = [a, b]
        elif cur[-1] == a:
            cur.append(b)
        else:
            if len(cur) >= 2:
                segs.append(cur)
            cur = [a, b]
    if len(cur) >= 2:
        segs.append(cur)
    return segs


def _clip_segment(a, b, x_min, z_min, x_max, z_max):
    x0, z0 = a
    x1, z1 = b
    dx, dz = x1 - x0, z1 - z0
    p = [-dx, dx, -dz, dz]
    q = [x0 - x_min, x_max - x0, z0 - z_min, z_max - z0]
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return None
        else:
            t = qi / pi
            if pi < 0:
                if t > t1:
                    return None
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    return None
                if t < t1:
                    t1 = t
    return ((x0 + t0 * dx, z0 + t0 * dz), (x0 + t1 * dx, z0 + t1 * dz))


def parse_height_tag(tags: dict) -> float | None:
    raw = tags.get("height")
    if raw:
        try:
            return float(str(raw).split()[0].replace("m", "").strip())
        except ValueError:
            pass
    lv = tags.get("building:levels")
    if lv:
        try:
            return float(str(lv).split(";")[0]) * LEVEL_HEIGHT_M
        except ValueError:
            pass
    return None


# ======================================================================
# Feature builders
# ======================================================================

def _cell_jitter(cx: int, cz: int) -> tuple[float, float]:
    """Deterministic per-cell jitter in [-0.5,0.5] cell fractions."""
    h = hashlib.md5(f"{JITTER_SEED}:{cx}:{cz}".encode()).digest()
    jx = int.from_bytes(h[0:4], "little") / 2**32 - 0.5
    jz = int.from_bytes(h[4:8], "little") / 2**32 - 0.5
    return jx, jz


def point_in_polygon(x: float, z: float, poly: list) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if ((zi > z) != (zj > z)) and (x < (xj - xi) * (z - zi) / (zj - zi) + xi):
            inside = not inside
        j = i
    return inside


def build_trees(elements, origin, bbox, grid: HeightGrid, clip_rect) -> tuple[list, dict]:
    x_min, z_min, x_max, z_max = clip_rect
    wood_polys = []      # local-XZ rings from natural=wood / landuse=forest
    tree_rows = []       # local-XZ polylines from natural=tree_row
    tree_nodes = []      # (x,z) from natural=tree

    for el in elements:
        tags = el.get("tags") or {}
        et = el.get("type")
        if et == "node" and tags.get("natural") == "tree":
            x, z = project(el["lat"], el["lon"], origin)
            if x_min <= x <= x_max and z_min <= z <= z_max:
                tree_nodes.append((x, z))
        elif et == "way":
            geom = el.get("geometry") or []
            coords = [(g["lat"], g["lon"]) for g in geom]
            if tags.get("natural") == "tree_row" and len(coords) >= 2:
                xz = [project(lat, lng, origin) for lat, lng in coords]
                for seg in clip_polyline_to_rect(xz, x_min, z_min, x_max, z_max):
                    tree_rows.append(seg)
            elif (tags.get("natural") == "wood" or tags.get("landuse") == "forest") \
                    and len(coords) >= 4 and coords[0] == coords[-1]:
                xz = [project(lat, lng, origin) for lat, lng in coords[:-1]]
                clipped = clip_to_rect(xz, x_min, z_min, x_max, z_max)
                if len(clipped) >= 3:
                    wood_polys.append(clipped)
        elif et == "relation" and (tags.get("natural") == "wood" or tags.get("landuse") == "forest"):
            for ring in assemble_outer_rings(el.get("members") or []):
                xz = [project(lat, lng, origin) for lat, lng in ring]
                clipped = clip_to_rect(xz, x_min, z_min, x_max, z_max)
                if len(clipped) >= 3:
                    wood_polys.append(clipped)

    def scan_wood(step_m: float) -> list:
        trees = []
        half_px = max(1, int(round(step_m / 2 / grid.m_per_px)))  # ~step/2 ground metres in px
        for poly in wood_polys:
            xs = [p[0] for p in poly]; zs = [p[1] for p in poly]
            gx0 = math.floor(min(xs) / step_m); gx1 = math.ceil(max(xs) / step_m)
            gz0 = math.floor(min(zs) / step_m); gz1 = math.ceil(max(zs) / step_m)
            for cx in range(gx0, gx1 + 1):
                for cz in range(gz0, gz1 + 1):
                    jx, jz = _cell_jitter(cx, cz)
                    x = (cx + 0.5 + jx) * step_m
                    z = (cz + 0.5 + jz) * step_m
                    if not (x_min <= x <= x_max and z_min <= z <= z_max):
                        continue
                    if not point_in_polygon(x, z, poly):
                        continue
                    h = grid.ndsm_window_percentile(x, z, origin, half_px, 75.0)
                    if h is None or h <= TREE_NDSM_MIN:
                        continue
                    trees.append((x, z, h))
        return trees

    # adaptive grid to honour the 20 000 cap (deterministic; coarsen step).
    step = TREE_GRID_M
    wood_trees = scan_wood(step)
    row_half_px = max(1, int(round(2.0 / grid.m_per_px)))
    tree_row_trees = []
    for seg in tree_rows:
        # continuous arc-length walk: one tree every TREE_ROW_STEP_M along the polyline
        leftover = 0.0
        for i in range(len(seg) - 1):
            ax, az = seg[i]; bx, bz = seg[i + 1]
            seglen = math.hypot(bx - ax, bz - az)
            if seglen == 0:
                continue
            d = leftover
            while d <= seglen:
                t = d / seglen
                x = ax + (bx - ax) * t; z = az + (bz - az) * t
                h = grid.ndsm_window_percentile(x, z, origin, row_half_px, 75.0)
                tree_row_trees.append((x, z, h if (h and h > TREE_NDSM_MIN) else TREE_DEFAULT_H))
                d += TREE_ROW_STEP_M
            leftover = d - seglen
    node_trees = []
    for x, z in tree_nodes:
        h = grid.ndsm_window_percentile(x, z, origin, row_half_px, 75.0)
        node_trees.append((x, z, h if (h and h > TREE_NDSM_MIN) else TREE_DEFAULT_H))

    all_trees = wood_trees + tree_row_trees + node_trees
    coarsen_steps = 0
    while len(all_trees) > TREE_CAP:
        step *= math.sqrt(len(wood_trees) / max(1, TREE_CAP - len(tree_row_trees) - len(node_trees)))
        step = max(step, TREE_GRID_M + coarsen_steps + 1)
        coarsen_steps += 1
        wood_trees = scan_wood(step)
        all_trees = wood_trees + tree_row_trees + node_trees
        if coarsen_steps > 12:
            break
    if len(all_trees) > TREE_CAP:
        # final deterministic seeded subsample to the cap
        rng = np.random.default_rng(JITTER_SEED)
        idx = np.sort(rng.choice(len(all_trees), TREE_CAP, replace=False))
        all_trees = [all_trees[i] for i in idx.tolist()]

    trees = []
    for x, z, h in all_trees:
        crown = min(CROWN_MAX, max(CROWN_MIN, h * CROWN_FACTOR))
        trees.append({
            "x": round(x, 2), "z": round(z, 2),
            "height": round(float(h), 2), "crownRadius": round(crown, 2),
        })
    trees.sort(key=lambda t: (t["x"], t["z"], t["height"]))
    info = {
        "wood_polys": len(wood_polys), "tree_rows": len(tree_rows),
        "tree_nodes": len(tree_nodes), "grid_step_m": round(step, 2),
        "wood_trees": len(wood_trees), "tree_row_trees": len(tree_row_trees),
        "node_trees": len(node_trees), "coarsen_steps": coarsen_steps,
    }
    return trees, info


def build_buildings(elements, origin, grid: HeightGrid, clip_rect) -> tuple[list, dict]:
    x_min, z_min, x_max, z_max = clip_rect
    buildings = []
    tagged = 0
    measured = 0
    default = 0

    def footprint_height(ring_xz) -> tuple[float, str, int]:
        # gather nDSM cells inside footprint bbox that are inside-polygon
        xs = [p[0] for p in ring_xz]; zs = [p[1] for p in ring_xz]
        # sample on a ~1m lattice covering the footprint bbox
        vals = []
        step = 1.0
        gx = np.arange(min(xs), max(xs) + step, step)
        gz = np.arange(min(zs), max(zs) + step, step)
        for x in gx:
            for z in gz:
                if point_in_polygon(float(x), float(z), ring_xz):
                    ppx, ppy = grid._xz_to_px(float(x), float(z), origin)
                    c, r = int(round(ppx)), int(round(ppy))
                    if 0 <= c < grid.w and 0 <= r < grid.h:
                        v = grid.ndsm[r, c]
                        if np.isfinite(v):
                            vals.append(float(v))
        return (float(np.percentile(vals, 90)) if len(vals) >= BUILDING_MIN_CELLS else None,
                len(vals))

    def add_building(fid, ring_xz, tags):
        nonlocal tagged, measured, default
        clipped = clip_to_rect(ring_xz, x_min, z_min, x_max, z_max)
        if len(clipped) < 3:
            return
        h90, ncells = footprint_height(clipped)
        if h90 is not None and h90 > 1.0:
            height = h90; measured += 1
        else:
            ht = parse_height_tag(tags)
            if ht is not None:
                height = ht; tagged += 1
            else:
                height = BUILDING_DEFAULT_H; default += 1
        ring = normalize_ccw([(round(x, 2), round(z, 2)) for x, z in clipped])
        out_tags = {}
        for key in ("building", "name", "name:en", "building:levels", "height"):
            if key in tags:
                out_tags[key] = tags[key]
        buildings.append({
            "footprint": [[x, z] for x, z in ring],
            "height": round(height, 2),
            "tags": out_tags,
            "_id": fid,
            "_cells": ncells,
        })

    for el in elements:
        tags = el.get("tags") or {}
        if not tags.get("building"):
            continue
        if el.get("type") == "way":
            geom = el.get("geometry") or []
            coords = [(g["lat"], g["lon"]) for g in geom]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                xz = [project(lat, lng, origin) for lat, lng in coords[:-1]]
                add_building(f"way/{el['id']}", xz, tags)
        elif el.get("type") == "relation":
            for k, ring in enumerate(assemble_outer_rings(el.get("members") or [])):
                xz = [project(lat, lng, origin) for lat, lng in ring]
                add_building(f"relation/{el['id']}#{k}", xz, tags)

    buildings.sort(key=lambda b: b["_id"])
    named = [(b["tags"].get("name") or b["tags"].get("name:en"), b["height"])
             for b in buildings if b["tags"].get("name") or b["tags"].get("name:en")]
    for b in buildings:
        b.pop("_id", None); b.pop("_cells", None)
    info = {"count": len(buildings), "measured": measured, "tagged": tagged,
            "default": default, "named": sorted(named, key=lambda n: -n[1])}
    return buildings, info


def build_barriers(elements, origin, clip_rect) -> tuple[list, dict]:
    x_min, z_min, x_max, z_max = clip_rect
    barriers = []
    counts = Counter()
    for el in elements:
        tags = el.get("tags") or {}
        btype = tags.get("barrier")
        if el.get("type") != "way" or btype not in BARRIER_DEFAULT_H:
            continue
        geom = el.get("geometry") or []
        coords = [(g["lat"], g["lon"]) for g in geom]
        if len(coords) < 2:
            continue
        xz = [project(lat, lng, origin) for lat, lng in coords]
        h_tag = parse_height_tag(tags)
        height = h_tag if h_tag is not None else BARRIER_DEFAULT_H[btype]
        for seg in clip_polyline_to_rect(xz, x_min, z_min, x_max, z_max):
            pts = [[round(x, 2), round(z, 2)] for x, z in seg]
            barriers.append({
                "type": btype, "points": pts, "height": round(height, 2),
                "_id": f"way/{el['id']}", "_k": len(barriers),
            })
            counts[btype] += 1
    barriers.sort(key=lambda b: (b["_id"], b["points"][0][0], b["points"][0][1]))
    for b in barriers:
        b.pop("_id", None); b.pop("_k", None)
    return barriers, {"count": len(barriers), "by_type": dict(counts)}


# ======================================================================
# Schema validation
# ======================================================================

def validate_schema(doc: dict) -> list:
    errs = []
    if doc.get("version") != 1:
        errs.append("version must be 1")
    for key in ("origin", "source", "trees", "buildings", "barriers"):
        if key not in doc:
            errs.append(f"missing top-level key '{key}'")
    o = doc.get("origin", {})
    for k in ("lat", "lng", "alt"):
        if k not in o:
            errs.append(f"origin missing '{k}'")
    for t in doc.get("trees", []):
        if not all(k in t for k in ("x", "z", "height", "crownRadius")):
            errs.append(f"tree missing keys: {t}"); break
    for b in doc.get("buildings", []):
        if "footprint" not in b or "height" not in b or "tags" not in b:
            errs.append(f"building missing keys: {b.get('tags')}"); break
        if len(b["footprint"]) < 3 or any(len(p) != 2 for p in b["footprint"]):
            errs.append("building footprint must be >=3 [x,z] pairs"); break
    for br in doc.get("barriers", []):
        if not all(k in br for k in ("type", "points", "height")):
            errs.append(f"barrier missing keys: {br}"); break
        if len(br["points"]) < 2:
            errs.append("barrier needs >=2 points"); break
    return errs


# ======================================================================
# Main
# ======================================================================

def track_paths(track: str) -> dict:
    d = TRACKS_DIR / track
    return {
        "dir": d,
        "track_json": d / "track.json",
        "sat_meta": d / "satellite_meta.json",
        "terrain_png": d / "terrain.png",
        "terrain_meta": d / "terrain_meta.json",
        "out": d / "features3d.json",
        "osm_cache": CACHE_DIR / f"osm_features3d_{track}.json",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build features3d.json (trees/buildings/barriers).")
    ap.add_argument("--track", choices=("barber", "fuji"), required=True)
    ap.add_argument("--refresh-osm", action="store_true", help="re-query Overpass (ignore cache)")
    ap.add_argument("--ept-depth", type=int, default=EPT_MAX_DEPTH, help="Barber EPT max octree depth")
    ap.add_argument("--tree-clearance", type=float, default=TREE_CLEARANCE_M,
                    help="extra gap (m) beyond half-width+crown before a tree may stand near the track")
    ap.add_argument("--validate", action="store_true", help="validate an existing features3d.json and exit")
    ap.add_argument("--self-test", action="store_true", help="run projection self-test and exit")
    args = ap.parse_args()

    cfg = TRACK_CONFIG[args.track]
    paths = track_paths(args.track)
    track_doc = json.loads(paths["track_json"].read_text())
    origin = track_doc["origin"]
    track_width = float(track_doc.get("width", 12.0))
    bbox = json.loads(paths["sat_meta"].read_text())["bbox"]

    if args.validate:
        doc = json.loads(paths["out"].read_text())
        errs = validate_schema(doc)
        if errs:
            print("SCHEMA INVALID:")
            for e in errs:
                print("  -", e)
            return 1
        print(f"Schema OK: {len(doc['trees'])} trees, {len(doc['buildings'])} buildings, "
              f"{len(doc['barriers'])} barriers")
        return 0

    run_self_test(origin, bbox)
    run_clearance_self_test()
    if args.self_test:
        return 0

    # --- clip rect in local XZ (north -> -z swaps which corner feeds z_min/z_max) ---
    x_min, z_max_c = project(bbox["minLat"], bbox["minLng"], origin)   # SW
    x_max, z_min_c = project(bbox["maxLat"], bbox["maxLng"], origin)   # NE
    clip_rect = (x_min, z_min_c, x_max, z_max_c)
    print(f"Clip rect (satellite bbox, local XZ): x[{x_min:.1f},{x_max:.1f}] z[{z_min_c:.1f},{z_max_c:.1f}]")

    # --- nDSM grid ---
    grid = HeightGrid(paths["terrain_png"], bbox)
    print(f"Height grid: {grid.w} x {grid.h} px "
          f"(~{(grid.x_max-grid.x_min)*math.cos(math.radians((bbox['minLat']+bbox['maxLat'])/2))/(grid.w-1):.2f} m/px)")
    if cfg["height_source"] == "ept":
        src_info = fill_grid_from_ept(grid, bbox, cfg["ept_cache"], args.ept_depth)
    else:
        src_info = fill_grid_from_shizuoka(grid, cfg["lp_cache"])
    print(f"Classification histogram (all read points): {src_info['class_hist']}")
    ndsm = grid.finalize_ndsm()
    valid = np.isfinite(ndsm)
    n_valid = int(valid.sum())
    print(f"nDSM valid cells: {n_valid:,}/{grid.w*grid.h:,} ({n_valid/(grid.w*grid.h)*100:.1f}%); "
          f"nDSM p50={np.nanpercentile(ndsm,50):.2f} p90={np.nanpercentile(ndsm,90):.2f} "
          f"max={np.nanmax(ndsm):.2f} m")

    # --- OSM ---
    query, _q = build_overpass_query(bbox, OSM_MARGIN_M)
    raw = fetch_overpass(query, paths["osm_cache"], args.refresh_osm)
    elements = raw.get("elements", [])

    trees, tinfo = build_trees(elements, origin, bbox, grid, clip_rect)
    buildings, binfo = build_buildings(elements, origin, grid, clip_rect)
    barriers, brinfo = build_barriers(elements, origin, clip_rect)

    # --- P3c: track-corridor clearance (barriers are intentionally NOT clipped) ---
    cxs, czs = load_centerline_xz(track_doc)
    half_w = track_width / 2.0
    trees_before = len(trees)
    if trees:
        td = centerline_distance([t["x"] for t in trees], [t["z"] for t in trees], cxs, czs)
        thr = half_w + args.tree_clearance + np.asarray([t["crownRadius"] for t in trees])
        keep = td >= thr
        trees = [t for t, k in zip(trees, keep.tolist()) if k]
    trees_removed = trees_before - len(trees)
    print(f"trees removed by clearance: {trees_removed}")

    buildings_before = len(buildings)
    bthr = half_w + BUILDING_CLEARANCE_M
    kept_buildings = []
    for b in buildings:
        fp = b["footprint"]
        bd = centerline_distance([p[0] for p in fp], [p[1] for p in fp], cxs, czs)
        if float(bd.min()) >= bthr:
            kept_buildings.append(b)
    buildings = kept_buildings
    buildings_removed = buildings_before - len(buildings)
    print(f"buildings removed by clearance: {buildings_removed}")
    tinfo["trees_before_clearance"] = trees_before
    tinfo["trees_removed_by_clearance"] = trees_removed
    binfo["buildings_before_clearance"] = buildings_before
    binfo["buildings_removed_by_clearance"] = buildings_removed

    doc = {
        "version": 1,
        "origin": origin,
        "source": cfg["source_label"],
        "trees": trees,
        "buildings": buildings,
        "barriers": barriers,
    }
    errs = validate_schema(doc)
    if errs:
        print("SCHEMA INVALID after build:")
        for e in errs:
            print("  -", e)
        return 1

    paths["out"].parent.mkdir(parents=True, exist_ok=True)
    paths["out"].write_text(json.dumps(doc, separators=(",", ":"), sort_keys=False) + "\n")
    size_kb = paths["out"].stat().st_size / 1024
    sha = hashlib.sha256(paths["out"].read_bytes()).hexdigest()

    print("\n===== SUMMARY =====")
    print(f"track: {args.track}")
    print(f"output: {paths['out']} ({size_kb:.1f} KB)  sha256={sha[:16]}")
    print(f"trees: {len(trees)}  buildings: {len(buildings)}  barriers: {len(barriers)}")
    print(f"tree scan: {tinfo}")
    if trees:
        hs = np.array([t["height"] for t in trees])
        print(f"tree height p10/p50/p90 = {np.percentile(hs,10):.1f}/{np.percentile(hs,50):.1f}/"
              f"{np.percentile(hs,90):.1f} m (min {hs.min():.1f}, max {hs.max():.1f})")
    print(f"buildings: measured={binfo['measured']} tagged={binfo['tagged']} default={binfo['default']}")
    for name, h in binfo["named"][:15]:
        print(f"  named building: {name!r} -> {h:.1f} m")
    print(f"barriers by type: {brinfo['by_type']}")
    if cfg["height_source"] == "ept":
        print(f"EPT: {src_info['nodes']} nodes, {src_info['downloaded_nodes']} newly downloaded, "
              f"{src_info['dl_bytes']/1e6:.1f} MB, depth {src_info['max_depth']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
