"""prep_data.py — data-prep stage of the automated track pipeline.

Reads a track's reconstruction data (public/data/tracks/<id>/) and emits the
Blender inputs into tracktools-kit/<id>/:
  - terrain_lidar.obj   LiDAR terrain, viewer local ENU (Y-up), true elevation
  - terrain_lidar.npz   same verts/uvs as a binary cache (fast bpy mesh build)
  - centerline.json     road centerline (ENU x/y/z + per-point yLeft/yRight
                        edge heights, dist, width, camber)
  - build.json          resolved params for the Blender driver

tuned.json in the same kit dir is the GUI-tuning overlay written from Blender
(viewer_export.py) — prep NEVER touches it, so re-running prep can't clobber
your tuning.

Outputs are skipped when they are newer than every input (track.json, terrain
image/meta, this script); pass --force to regenerate.

Runs WITHOUT Blender (Python + numpy + Pillow). Reuses the viewer's exact
terrarium/mercator/ENU decode so the terrain matches the reconstruction 1:1.

Usage:  python prep_data.py --track fuji [--repo D:\\00_Dev\\ReplayViewer] [--force]
"""
import argparse, json, math, os
import numpy as np
from PIL import Image

MPD = 111320.0


def merc(latdeg: float) -> float:
    return math.asinh(math.tan(math.radians(latdeg)))


def outputs_fresh(inputs, outputs):
    """True when every output exists and is newer than every input (mtime)."""
    if not all(os.path.exists(o) for o in outputs):
        return False
    newest_in = max(os.path.getmtime(i) for i in inputs if os.path.exists(i))
    return min(os.path.getmtime(o) for o in outputs) > newest_in


def build_terrain_obj(repo, track_id, origin, out_obj, grid=500, margin=250):
    tdir = os.path.join(repo, "public", "data", "tracks", track_id)
    meta = json.load(open(os.path.join(tdir, "terrain_meta.json")))
    bb = meta["bbox"]
    im = Image.open(os.path.join(tdir, meta["imageFile"])).convert("RGB")
    W, H = im.size
    px = np.asarray(im, dtype=np.float64)
    heights = px[:, :, 0] * 256 + px[:, :, 1] + px[:, :, 2] / 256 - 32768
    yN, yS = merc(bb["maxLat"]), merc(bb["minLat"])
    lngScale = MPD * math.cos(math.radians(origin["lat"]))

    def sample(lat, lng):
        u = min(1, max(0, (lng - bb["minLng"]) / (bb["maxLng"] - bb["minLng"])))
        v = min(1, max(0, (yN - merc(lat)) / (yN - yS)))
        fx, fy = u * (W - 1), v * (H - 1)
        x0, y0 = int(fx), int(fy)
        x1, y1 = min(x0 + 1, W - 1), min(y0 + 1, H - 1)
        tx, ty = fx - x0, fy - y0
        return (heights[y0, x0] * (1 - tx) * (1 - ty) + heights[y0, x1] * tx * (1 - ty)
                + heights[y1, x0] * (1 - tx) * ty + heights[y1, x1] * tx * ty)

    trk = json.load(open(os.path.join(tdir, "track.json")))
    xb, zb = trk["bounds"]["x"], trk["bounds"]["z"]
    x0, x1 = xb[0] - margin, xb[1] + margin
    z0, z1 = zb[0] - margin, zb[1] + margin

    verts, uvs = [], []
    for j in range(grid):
        z = z0 + (z1 - z0) * j / (grid - 1)
        for i in range(grid):
            x = x0 + (x1 - x0) * i / (grid - 1)
            lat = origin["lat"] - z / MPD
            lng = origin["lng"] + x / lngScale
            y = sample(lat, lng) - origin["alt"]
            verts.append((x, y, z))
            u = (lng - bb["minLng"]) / (bb["maxLng"] - bb["minLng"])
            v = (yN - merc(lat)) / (yN - yS)
            uvs.append((u, 1.0 - v))
    os.makedirs(os.path.dirname(out_obj), exist_ok=True)
    with open(out_obj, "w") as f:
        f.write(f"# {track_id} LiDAR terrain (source: {meta.get('source','?')})\n")
        f.write(f"# local ENU metres, Y-up. origin {origin}\n")
        f.write("o terrain_lidar\n")
        for (x, y, z) in verts:
            f.write(f"v {x:.3f} {y:.3f} {z:.3f}\n")
        for (u, v) in uvs:
            f.write(f"vt {u:.5f} {v:.5f}\n")
        for j in range(grid - 1):
            for i in range(grid - 1):
                a = j * grid + i + 1; b = a + 1; c = a + grid; d = c + 1
                f.write(f"f {a}/{a} {c}/{c} {b}/{b}\nf {b}/{b} {c}/{c} {d}/{d}\n")
    # Binary cache alongside the OBJ: the Blender driver builds the mesh from
    # this via foreach_set (much faster than parsing 500k OBJ faces); the OBJ
    # stays as the human-inspectable/debug artifact.
    np.savez_compressed(os.path.splitext(out_obj)[0] + ".npz",
                        verts=np.asarray(verts, dtype=np.float32),
                        uvs=np.asarray(uvs, dtype=np.float32),
                        grid=np.int32(grid))
    return meta.get("source", ""), grid, len(verts)


def build_centerline(trk, origin, out_json):
    width = trk["width"]
    pts = []
    for p in trk["centerline"]:
        aL, aR = p.get("altLeft"), p.get("altRight")
        camber = math.degrees(math.atan2((aL - aR), width)) if (aL is not None and aR is not None) else 0.0
        pt = {"x": round(p["x"], 3), "y": round(p["y"], 3), "z": round(p["z"], 3),
              "dist": round(p["dist"], 2), "camber": round(camber, 3)}
        # Local edge heights so the road ribbon carries the real crossfall.
        # Viewer chirality (interpolation.ts): LEFT = signed lateral -width/2
        # = the (tz, -tx) side of the tangent; altLeft belongs to that edge.
        if aL is not None and aR is not None:
            pt["yLeft"] = round(aL - origin["alt"], 3)
            pt["yRight"] = round(aR - origin["alt"], 3)
        if p.get("width") is not None:  # per-station width, when a track has it
            pt["width"] = round(p["width"], 2)
        pts.append(pt)
    json.dump({"width": width, "totalLength": trk["totalLength"], "closed": True, "points": pts},
              open(out_json, "w"))
    return len(pts), width


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True)
    ap.add_argument("--repo", default=r"D:\00_Dev\ReplayViewer")
    ap.add_argument("--grid", type=int, default=500)
    ap.add_argument("--force", action="store_true", help="regenerate even when outputs are fresh")
    args = ap.parse_args()

    tdir = os.path.join(args.repo, "public", "data", "tracks", args.track)
    trk = json.load(open(os.path.join(tdir, "track.json")))
    origin = trk["origin"]
    kit = os.path.join(args.repo, "tracktools-kit", args.track)
    os.makedirs(kit, exist_ok=True)

    meta = json.load(open(os.path.join(tdir, "terrain_meta.json")))
    inputs = [os.path.join(tdir, "track.json"), os.path.join(tdir, "terrain_meta.json"),
              os.path.join(tdir, meta["imageFile"]), os.path.abspath(__file__)]
    outputs = [os.path.join(kit, n) for n in
               ("terrain_lidar.obj", "terrain_lidar.npz", "centerline.json", "build.json")]
    if not args.force and outputs_fresh(inputs, outputs):
        print(f"[prep] {args.track}: outputs up to date (use --force to regenerate)")
        return

    src, grid, nv = build_terrain_obj(args.repo, args.track, origin,
                                      os.path.join(kit, "terrain_lidar.obj"), grid=args.grid)
    npts, width = build_centerline(trk, origin, os.path.join(kit, "centerline.json"))

    build = {
        "track": args.track,
        "origin": origin,
        "lidar_source": src,
        "terrain_obj": "terrain_lidar.obj",
        "centerline": "centerline.json",
        "road": {"width_m": width, "resolution": 2.0, "source_elevation_from_terrain": True},
        "output": {
            "scene_glb": f"public/data/tracks/{args.track}/scene.glb",
            "collision_glb": f"public/data/tracks/{args.track}/road_ac_local.glb",
        },
        "notes": "ENU Y-up throughout; no ICP/Z-flip. Blender import OBJ Y-up, export glTF +Y up.",
    }
    json.dump(build, open(os.path.join(kit, "build.json"), "w"), indent=2)
    print(f"[prep] {args.track}: terrain {nv} verts (grid {grid}), centerline {npts} pts, width {width} m")
    print(f"[prep] wrote {kit}\\{{terrain_lidar.obj, centerline.json, build.json}}")


if __name__ == "__main__":
    main()
