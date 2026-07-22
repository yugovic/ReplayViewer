"""detect_from_photo.py — 国土地理院航空写真からコース幅と縁石を推定し、
track.json の edges / curbs に書き込む前処理スクリプト。

人間が修正・監修する前提で、あくまで「初期案」を生成する。

Usage:
  python scripts/detect_from_photo.py --track fuji
  python scripts/detect_from_photo.py --track fuji --edge-step 2 --zoom 18 --dry-run
"""
import argparse
import json
import math
import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import List, Tuple, Optional

import numpy as np
from PIL import Image

DEG = math.pi / 180
MPD = 111320.0  # metres per degree (approx)
GSI_PHOTO_URL = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"


# ──────────────────────────────────────────────────────────────────────────────
# coordinate conversions (mirror of editor/overlay.ts)
# ──────────────────────────────────────────────────────────────────────────────

def local_to_lat_lng(origin: dict, x: float, z: float) -> Tuple[float, float]:
    """Local ENU (x=east, z=-north) -> WGS84 lat/lng."""
    lat = origin["lat"] + -z / MPD
    lng = origin["lng"] + x / (MPD * math.cos(origin["lat"] * DEG))
    return lat, lng


def lat_lng_to_local(origin: dict, lat: float, lng: float) -> Tuple[float, float]:
    """WGS84 lat/lng -> local ENU (x=east, z=-north)."""
    x = (lng - origin["lng"]) * MPD * math.cos(origin["lat"] * DEG)
    z = -((lat - origin["lat"]) * MPD)
    return x, z


def tile_index(lat: float, lng: float, zoom: int) -> Tuple[int, int]:
    """Slippy-map tile x,y for a lat/lng."""
    n = 2 ** zoom
    lat_rad = lat * DEG
    tx = math.floor(((lng + 180) / 360) * n)
    ty = math.floor(((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2) * n)
    return int(tx), int(ty)


def tile_corner(tx: int, ty: int, zoom: int) -> Tuple[float, float]:
    """North-west corner of a tile in lat/lng."""
    n = 2 ** zoom
    lng = (tx / n) * 360 - 180
    lat = math.atan(math.sinh(math.pi * (1 - (2 * ty) / n))) / DEG
    return lat, lng


def tile_size_m(origin: dict, zoom: int, tx: int, ty: int) -> Tuple[float, float]:
    """Approximate tile width/height in local metres."""
    nw_lat, nw_lng = tile_corner(tx, ty, zoom)
    se_lat, se_lng = tile_corner(tx + 1, ty + 1, zoom)
    nw_x, nw_z = lat_lng_to_local(origin, nw_lat, nw_lng)
    se_x, se_z = lat_lng_to_local(origin, se_lat, se_lng)
    return abs(se_x - nw_x), abs(se_z - nw_z)


# ──────────────────────────────────────────────────────────────────────────────
# GSI aerial photo tile fetching
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PhotoTile:
    """One downloaded tile with its local ENU bounds."""
    tx: int
    ty: int
    zoom: int
    image: Image.Image
    nw_x: float
    nw_z: float
    se_x: float
    se_z: float
    width_m: float
    height_m: float


def fetch_tile(tx: int, ty: int, zoom: int, cache_dir: str) -> Image.Image:
    """Download a GSI seamlessphoto tile, using a local cache."""
    os.makedirs(cache_dir, exist_ok=True)
    fname = os.path.join(cache_dir, f"{zoom}_{tx}_{ty}.jpg")
    if os.path.exists(fname):
        return Image.open(fname).convert("RGB")
    url = GSI_PHOTO_URL.format(z=zoom, x=tx, y=ty)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (track-creator detect_from_photo; non-commercial research)"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(fname, "wb") as f:
            f.write(data)
        return Image.open(fname).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"failed to fetch tile {url}: {exc}")


def build_photo_mosaic(origin: dict, bounds: Tuple[float, float, float, float], zoom: int,
                       cache_dir: str) -> List[PhotoTile]:
    """Download and index all GSI photo tiles covering the local bounds.

    bounds: (min_x, min_z, max_x, max_z) in local metres.
    Returns a list of PhotoTile objects.
    """
    min_x, min_z, max_x, max_z = bounds
    # pad a bit
    min_x -= 50; min_z -= 50; max_x += 50; max_z += 50
    nw_lat, nw_lng = local_to_lat_lng(origin, min_x, min_z)
    se_lat, se_lng = local_to_lat_lng(origin, max_x, max_z)
    tx0, ty0 = tile_index(nw_lat, nw_lng, zoom)
    tx1, ty1 = tile_index(se_lat, se_lng, zoom)

    tiles: List[PhotoTile] = []
    for tx in range(min(tx0, tx1), max(tx0, tx1) + 1):
        for ty in range(min(ty0, ty1), max(ty0, ty1) + 1):
            im = fetch_tile(tx, ty, zoom, cache_dir)
            nw_lat, nw_lng = tile_corner(tx, ty, zoom)
            se_lat, se_lng = tile_corner(tx + 1, ty + 1, zoom)
            nw_x, nw_z = lat_lng_to_local(origin, nw_lat, nw_lng)
            se_x, se_z = lat_lng_to_local(origin, se_lat, se_lng)
            tiles.append(PhotoTile(
                tx=tx, ty=ty, zoom=zoom, image=im,
                nw_x=min(nw_x, se_x), nw_z=min(nw_z, se_z),
                se_x=max(nw_x, se_x), se_z=max(nw_z, se_z),
                width_m=abs(se_x - nw_x), height_m=abs(se_z - nw_z),
            ))
    return tiles


# ──────────────────────────────────────────────────────────────────────────────
# Catmull-Rom centerline resampling (mirror of src/centerline.ts)
# ──────────────────────────────────────────────────────────────────────────────

def catmull_rom(p0, p1, p2, p3, t: float):
    t2 = t * t
    t3 = t2 * t
    def f(a, b, c, d):
        return 0.5 * (2*b + (-a+c)*t + (2*a-5*b+4*c-d)*t2 + (-a+3*b-3*c+d)*t3)
    return np.array([f(p0[0], p1[0], p2[0], p3[0]),
                     f(p0[1], p1[1], p2[1], p3[1])])


def dense_polyline(control: List[Tuple[float, float]], closed: bool, subdiv: int = 64) -> np.ndarray:
    n = len(control)
    if n < 2:
        raise ValueError("centerline needs at least 2 control points")
    pts = []
    seg_count = n if closed else n - 1
    for i in range(seg_count):
        p0 = np.array(control[(i - 1 + n) % n if closed else max(i - 1, 0)])
        p1 = np.array(control[i])
        p2 = np.array(control[(i + 1) % n])
        p3 = np.array(control[(i + 2) % n if closed else min(i + 2, n - 1)])
        for s in range(subdiv):
            pts.append(catmull_rom(p0, p1, p2, p3, s / subdiv))
    if not closed:
        pts.append(np.array(control[-1]))
    return np.array(pts)


def resample_centerline(control: List[Tuple[float, float]], step: float, closed: bool) -> 'Centerline':
    """Resample a Catmull-Rom spline at uniform arc-length spacing."""
    dense = dense_polyline(control, closed)
    n = len(dense)
    cum = np.zeros(n)
    for i in range(1, n):
        cum[i] = cum[i - 1] + np.linalg.norm(dense[i] - dense[i - 1])
    close_len = np.linalg.norm(dense[0] - dense[-1]) if closed else 0.0
    total_length = cum[-1] + close_len

    count = max(3 if closed else 2, round(total_length / step) + (0 if closed else 1))
    actual_step = total_length / count if closed else total_length / (count - 1)

    points = []
    seg = 0
    for k in range(count):
        d = k * actual_step
        while seg < n - 1 and cum[seg + 1] < d:
            seg += 1
        if seg >= n - 1:
            t = close_len / total_length if close_len > 0 else 0.0
            pt = dense[-1] + (dense[0] - dense[-1]) * ((d - cum[-1]) / close_len if close_len > 0 else 0)
        else:
            seg_len = cum[seg + 1] - cum[seg]
            tt = (d - cum[seg]) / seg_len if seg_len > 0 else 0
            pt = dense[seg] + (dense[seg + 1] - dense[seg]) * tt
        points.append((pt[0], pt[1], d))

    m = len(points)
    tangents = []
    for i in range(m):
        prev = points[(i - 1 + m) % m if closed else max(i - 1, 0)]
        nxt = points[(i + 1) % m if closed else min(i + 1, m - 1)]
        dx = nxt[0] - prev[0]
        dz = nxt[1] - prev[1]
        length = math.hypot(dx, dz) or 1.0
        tangents.append((dx / length, dz / length))

    return Centerline(points=[{
        "x": p[0], "z": p[1], "dist": p[2],
        "tx": t[0], "tz": t[1]
    } for p, t in zip(points, tangents)], total_length=total_length, closed=closed)


@dataclass
class Centerline:
    points: List[dict]
    total_length: float
    closed: bool


def wrap_dist(d: float, total: float) -> float:
    return ((d % total) + total) % total


# ──────────────────────────────────────────────────────────────────────────────
# sample aerial photo at local ENU coordinates
# ──────────────────────────────────────────────────────────────────────────────

def sample_photo(tiles: List[PhotoTile], x: float, z: float) -> Optional[Tuple[int, int, int]]:
    """Return RGB at a local ENU point, or None if out of tile coverage."""
    for tile in tiles:
        if not (tile.nw_x <= x <= tile.se_x and tile.nw_z <= z <= tile.se_z):
            continue
        u = (x - tile.nw_x) / tile.width_m
        v = (z - tile.nw_z) / tile.height_m
        # image v axis: top (north) = 0, bottom (south) = 1
        px = int(u * (tile.image.width - 1))
        py = int((1 - v) * (tile.image.height - 1))
        px = max(0, min(tile.image.width - 1, px))
        py = max(0, min(tile.image.height - 1, py))
        return tile.image.getpixel((px, py))
    return None


def sample_cross_section(tiles: List[PhotoTile], center: dict, normal: Tuple[float, float],
                         half_width: float, step_m: float = 0.25) -> Tuple[np.ndarray, np.ndarray]:
    """Sample RGB along the lateral normal from -half_width to +half_width.

    normal points to the RIGHT (positive signed lateral).  LEFT is the opposite.
    Returns (offsets, rgb_array) where offsets are signed metres.
    """
    nx, nz = normal
    n = max(3, int(2 * half_width / step_m) + 1)
    offsets = np.linspace(-half_width, half_width, n)
    rgb = np.empty((n, 3), dtype=np.float32)
    for i, off in enumerate(offsets):
        x = center["x"] + nx * off
        z = center["z"] + nz * off
        px = sample_photo(tiles, x, z)
        if px is None:
            rgb[i] = [0, 0, 0]
        else:
            rgb[i] = px
    return offsets, rgb


# ──────────────────────────────────────────────────────────────────────────────
# edge width estimation
# ──────────────────────────────────────────────────────────────────────────────

def rgb_to_luminance(rgb: np.ndarray) -> np.ndarray:
    """RGB shape (n,3) -> luminance (n,)."""
    return 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]


def _first_edge_index(diff: np.ndarray, grad: np.ndarray, valid: np.ndarray,
                      offsets: np.ndarray, side: int, diff_threshold: float,
                      grad_threshold: float) -> Optional[int]:
    """Return the first valid index from the center outward on the given side.

    side is -1 for left (negative offsets) or +1 for right (positive offsets).
    """
    n = len(offsets)
    mid = n // 2
    indices = range(mid - 1, -1, -1) if side == -1 else range(mid, n)
    for i in indices:
        if not valid[i]:
            continue
        if diff[i] >= diff_threshold and grad[i] >= grad_threshold:
            return i
    return None


def edge_offsets_from_profile(offsets: np.ndarray, lum: np.ndarray,
                              base_half_width: float, max_search_width: float,
                              smooth_px: int = 3) -> Tuple[Optional[float], Optional[float]]:
    """Find left and right paved edges from a 1D luminance profile.

    Road and surroundings may differ in luminance or colour.  We look for the
    first strong outward transition (absolute change) outside the base road
    width.  Edges near the search boundary are treated as not found.
    Returns (left_edge_offset, right_edge_offset) as signed metres from the
    centerline (negative = left, positive = right).
    """
    if len(lum) < 5:
        return None, None

    # smooth luminance
    if smooth_px > 1 and len(lum) > smooth_px * 2 + 1:
        kernel = np.ones(smooth_px) / smooth_px
        lum = np.convolve(lum, kernel, mode='same')

    # central region is assumed to be road; build a reference luminance
    center_mask = np.abs(offsets) < base_half_width * 0.7
    if center_mask.sum() > 0:
        road_lum = np.median(lum[center_mask])
    else:
        road_lum = np.median(lum)

    # absolute difference from the road surface and edge gradient
    diff = np.abs(lum - road_lum)
    grad = np.abs(np.gradient(lum))

    # search a plausible region outside the base road width
    max_edge_width = min(max_search_width, base_half_width + 10.0)
    valid = ~((np.abs(offsets) < base_half_width * 0.3) |
              (np.abs(offsets) > max_edge_width - 1.5))

    # dynamic thresholds based on the inner edge region, not the far boundary
    inner_mask = (np.abs(offsets) >= base_half_width * 0.5) & \
                 (np.abs(offsets) <= base_half_width + 5.0)
    if inner_mask.sum() > 0:
        max_diff = float(np.percentile(diff[inner_mask], 95))
        max_grad = float(np.percentile(grad[inner_mask], 95))
    else:
        max_diff = max_grad = 0.0
    diff_threshold = max(8.0, 0.15 * max_diff)
    grad_threshold = max(2.0, 0.15 * max_grad)

    # find the first strong edge outward from the base road width on each side
    left_idx = _first_edge_index(diff, grad, valid, offsets, -1, diff_threshold, grad_threshold)
    right_idx = _first_edge_index(diff, grad, valid, offsets, 1, diff_threshold, grad_threshold)

    left_edge = offsets[left_idx] if left_idx is not None else None
    right_edge = offsets[right_idx] if right_idx is not None else None

    # sanity: edges must be outside the base road width
    if left_edge is not None and left_edge > -base_half_width * 0.5:
        left_edge = None
    if right_edge is not None and right_edge < base_half_width * 0.5:
        right_edge = None

    return left_edge, right_edge


# ──────────────────────────────────────────────────────────────────────────────
# curb detection
# ──────────────────────────────────────────────────────────────────────────────

def is_curb_color(rgb: np.ndarray) -> np.ndarray:
    """Return a 1D score for how likely each pixel is part of a red/white curb.

    High score for strong red channel or bright white.
    """
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    # red signal: red much stronger than green/blue
    red_score = np.clip((r - 0.5 * (g + b)) / 60.0, 0, 1)
    # white signal: all channels high and similar
    avg = (r + g + b) / 3.0
    white_score = np.clip(avg - 120, 0, 1) / 135.0
    white_uniformity = 1.0 - np.abs(r - g) / 60.0 - np.abs(g - b) / 60.0
    white_score = white_score * np.clip(white_uniformity, 0, 1)
    return np.clip(red_score + white_score, 0, 1)


def curb_periodicity_score(rgb: np.ndarray, expected_stripe_m: float = 4.0,
                            sample_step_m: float = 0.25) -> float:
    """Score how strongly the 1D RGB samples show a red-white-red-white pattern.

    Returns a value in [0, 1].  High when the dominant frequency matches the
    expected curb stripe length.
    """
    if len(rgb) < 8:
        return 0.0
    score = is_curb_color(rgb)
    # detrend
    score = score - np.mean(score)
    if np.std(score) < 1e-6:
        return 0.0
    # look for the peak frequency around the expected stripe length
    n = len(score)
    freqs = np.fft.rfftfreq(n, d=sample_step_m)
    amps = np.abs(np.fft.rfft(score))
    # expected spatial frequency: 1 / (2 * stripe_length) because red+white = 2 stripes
    target_freq = 1.0 / (2.0 * expected_stripe_m)
    # Gaussian weight around target frequency
    sigma = 0.05
    weights = np.exp(-0.5 * ((freqs - target_freq) / sigma) ** 2)
    weighted = amps * weights
    # normalize by total energy
    total = np.sum(amps) + 1e-9
    return min(1.0, float(np.sum(weighted) / total) * 5.0)


def find_curb_spans(stations: List[dict], tiles: List[PhotoTile],
                    base_half_width: float, curb_width: float = 1.2,
                    stripe_length: float = 4.0, min_length: float = 10.0) -> List[dict]:
    """Detect red/white curb candidates along the centerline.

    For each station, sample a short cross-section just outside the road edge
    and look for red/white curb signatures.  Then cluster consecutive hits into
    spans with startDist/endDist/side.
    """
    curb_hits = []  # (dist, side, score)
    for st in stations:
        tx, tz = st["tx"], st["tz"]
        # normal to the right
        nx, nz = -tz, tx
        for side, sign in [("left", -1), ("right", 1)]:
            edge_x = st["x"] + nx * (sign * base_half_width)
            edge_z = st["z"] + nz * (sign * base_half_width)
            # sample a line just outside the edge (0.3 to 1.5m outward) along the road
            sample_points = []
            for off in np.linspace(0.3, 1.5, 7):
                x = edge_x + nx * (sign * off)
                z = edge_z + nz * (sign * off)
                sample_points.append((x, z))
            # also sample a few points along the tangent to see the red/white stripes
            tangent_samples = []
            for dt in np.linspace(-stripe_length * 1.5, stripe_length * 1.5, 13):
                sx = edge_x + tx * dt
                sz = edge_z + tz * dt
                px = sample_photo(tiles, sx, sz)
                if px:
                    tangent_samples.append(px)
            if not tangent_samples:
                continue
            arr = np.array(tangent_samples, dtype=np.float32)
            color_score = float(np.mean(is_curb_color(arr)))
            period_score = curb_periodicity_score(arr, expected_stripe_m=stripe_length,
                                                  sample_step_m=(2 * stripe_length * 1.5) / 6)
            # combine: require both color and periodic signal
            score = color_score * 0.6 + period_score * 0.4
            if score > 0.3:
                curb_hits.append((st["dist"], side, score))

    if not curb_hits:
        return []

    # cluster hits into spans per side
    spans = []
    for side in ("left", "right"):
        side_hits = sorted([h for h in curb_hits if h[1] == side], key=lambda h: h[0])
        if not side_hits:
            continue
        run_start = side_hits[0][0]
        run_end = side_hits[0][0]
        run_scores = [side_hits[0][2]]
        for i in range(1, len(side_hits)):
            d = side_hits[i][0]
            if d - side_hits[i - 1][0] > 12.0:  # gap > 12m -> new span
                length = run_end - run_start
                if length >= min_length and np.mean(run_scores) > 0.35:
                    spans.append({
                        "startDist": round(run_start, 1),
                        "endDist": round(run_end, 1),
                        "side": side,
                        "width": curb_width,
                        "stripeLength": stripe_length,
                    })
                run_start = d
                run_end = d
                run_scores = [side_hits[i][2]]
            else:
                run_end = d
                run_scores.append(side_hits[i][2])
        length = run_end - run_start
        if length >= min_length and np.mean(run_scores) > 0.35:
            spans.append({
                "startDist": round(run_start, 1),
                "endDist": round(run_end, 1),
                "side": side,
                "width": curb_width,
                "stripeLength": stripe_length,
            })

    # resolve overlapping left/right spans: keep the higher-scoring side
    resolved = []
    for side in ("left", "right"):
        side_spans = sorted([s for s in spans if s["side"] == side], key=lambda s: s["startDist"])
        other_side = "right" if side == "left" else "left"
        other_spans = sorted([s for s in spans if s["side"] == other_side], key=lambda s: s["startDist"])
        for s in side_spans:
            overlap = False
            for o in other_spans:
                if s["endDist"] >= o["startDist"] and s["startDist"] <= o["endDist"]:
                    # keep the longer one as a rough quality proxy
                    if (s["endDist"] - s["startDist"]) >= (o["endDist"] - o["startDist"]):
                        resolved.append(s)
                    overlap = True
                    break
            if not overlap:
                resolved.append(s)
    return sorted(resolved, key=lambda s: s["startDist"])


# ──────────────────────────────────────────────────────────────────────────────
# main pipeline
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Estimate road width and curbs from GSI aerial photos")
    ap.add_argument("--track", required=True, help="track name (under track-creator/tracks/)")
    ap.add_argument("--track-dir", default="", help="tracks directory (default: ../tracks)")
    ap.add_argument("--zoom", type=int, default=18, help="GSI tile zoom (default 18)")
    ap.add_argument("--edge-step", type=float, default=2.0, help="edge sample spacing in metres (default 2)")
    ap.add_argument("--max-search-width", type=float, default=25.0, help="how far to search each side (default 25m)")
    ap.add_argument("--curb-width", type=float, default=1.2, help="default curb width in metres")
    ap.add_argument("--curb-min-length", type=float, default=10.0, help="minimum detected curb span length")
    ap.add_argument("--curb-stripe-length", type=float, default=4.0, help="curb stripe length")
    ap.add_argument("--dry-run", action="store_true", help="print results but do not write track.json")
    ap.add_argument("--output", default="", help="output JSON path (default: overwrite track.json)")
    args = ap.parse_args()

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    track_dir = args.track_dir or os.path.join(repo_dir, "tracks", args.track)
    track_path = os.path.join(track_dir, "track.json")
    cache_dir = os.path.join(track_dir, "cache", "photo")

    if not os.path.exists(track_path):
        print(f"ERROR: {track_path} not found", file=sys.stderr)
        sys.exit(1)

    trk = json.load(open(track_path, encoding="utf-8"))
    origin = trk.get("origin")
    if not origin:
        print("ERROR: track.json has no 'origin' (lat/lng required for GSI photo tiles)", file=sys.stderr)
        sys.exit(1)

    control = [(p["x"], p["z"]) for p in trk["controlPoints"]]
    closed = trk.get("closed", True)
    base_width = trk["road"]["width"]
    base_half = base_width / 2.0

    print(f"[{args.track}] resampling centerline (step={args.edge_step}m)...")
    line = resample_centerline(control, args.edge_step, closed)
    stations = line.points
    print(f"[{args.track}] centerline: {len(stations)} stations, {line.total_length:.1f}m")

    # bounds
    xs = [p["x"] for p in stations]
    zs = [p["z"] for p in stations]
    bounds = (min(xs) - args.max_search_width, min(zs) - args.max_search_width,
              max(xs) + args.max_search_width, max(zs) + args.max_search_width)

    print(f"[{args.track}] fetching GSI aerial photo tiles (zoom={args.zoom})...")
    tiles = build_photo_mosaic(origin, bounds, args.zoom, cache_dir)
    print(f"[{args.track}] downloaded {len(tiles)} tile(s)")

    print(f"[{args.track}] estimating road edges (max search {args.max_search_width}m)...")
    left_edges = []
    right_edges = []
    for i, st in enumerate(stations):
        tx, tz = st["tx"], st["tz"]
        # right normal = (-tz, tx); left is the opposite
        nx, nz = -tz, tx
        offsets, rgb = sample_cross_section(tiles, st, (nx, nz), args.max_search_width)
        lum = rgb_to_luminance(rgb)
        left_off, right_off = edge_offsets_from_profile(offsets, lum, base_half, args.max_search_width)
        # convert to positive offsets (distance from centerline outward)
        left_edges.append(abs(left_off) if left_off is not None else base_half)
        right_edges.append(right_off if right_off is not None else base_half)
        if (i + 1) % 100 == 0 or i == len(stations) - 1:
            print(f"  ... {i+1}/{len(stations)} stations")

    # smooth the edges a bit (small moving average)
    def smooth_edges(arr: List[float]) -> List[float]:
        a = np.array(arr)
        kernel = np.array([0.1, 0.2, 0.4, 0.2, 0.1])
        pad = np.pad(a, (2, 2), mode='edge')
        out = np.convolve(pad, kernel, mode='valid')
        return [round(float(v), 2) for v in out]

    left_edges = smooth_edges(left_edges)
    right_edges = smooth_edges(right_edges)

    print(f"[{args.track}] detecting curbs...")
    curbs = find_curb_spans(stations, tiles, base_half,
                            curb_width=args.curb_width,
                            stripe_length=args.curb_stripe_length,
                            min_length=args.curb_min_length)
    print(f"[{args.track}] detected {len(curbs)} curb span(s)")

    out_def = dict(trk)
    out_def["road"]["edges"] = {
        "step": args.edge_step,
        "left": left_edges,
        "right": right_edges,
    }
    if out_def["road"].get("widthProfile"):
        del out_def["road"]["widthProfile"]
    out_def["curbs"] = curbs

    out_path = args.output or track_path
    if args.dry_run:
        print(f"[dry-run] would write {out_path}")
        print(f"[dry-run] edges: {len(left_edges)} pts, left {min(left_edges):.1f}-{max(left_edges):.1f}m, right {min(right_edges):.1f}-{max(right_edges):.1f}m")
        print(f"[dry-run] curbs: {len(curbs)} span(s)")
        for c in curbs[:30]:
            print(f"  {c['side']:5s} {c['startDist']:8.1f} - {c['endDist']:8.1f} m")
        if len(curbs) > 30:
            print(f"  ... and {len(curbs) - 30} more")
        # write preview JSON to a separate file for inspection
        preview = os.path.join(track_dir, "detect_preview.json")
        with open(preview, "w", encoding="utf-8") as f:
            json.dump({"road": {"edges": out_def["road"]["edges"]}, "curbs": curbs}, f, indent=2, ensure_ascii=False)
        print(f"[dry-run] full preview written to {preview}")
    else:
        # backup existing track.json
        backup = out_path + ".bak"
        os.replace(out_path, backup)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_def, f, indent=1, ensure_ascii=False)
        print(f"[{args.track}] wrote {out_path} (backup: {backup})")
        print(f"[{args.track}] edges: {len(left_edges)} points, left {min(left_edges):.1f}-{max(left_edges):.1f}m, right {min(right_edges):.1f}-{max(right_edges):.1f}m")
        print(f"[{args.track}] curbs: {len(curbs)} span(s)")
        print(f"[{args.track}] next: open the editor and verify/adjust the generated edges and curbs")


if __name__ == "__main__":
    main()
