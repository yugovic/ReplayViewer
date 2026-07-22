"""build_track_blender.py — headless Blender driver (run via `blender -b -P`).

Reads a prepared kit (terrain_lidar.obj/.npz + centerline.json + build.json,
plus the optional GUI-tuning overlay tuned.json), builds the track, and exports
scene.glb + the collision glb into the repo. Also saves a staging .blend
(<track>_staging.blend in the kit) with live modifiers, SCENE/COLLISION/REF
collections and an embedded EXPORT_FOR_VIEWER.py text block — open it, tune
width/kerbs in the GUI, press Run on the text block, and your tuning both
exports AND round-trips back into tuned.json for the next headless build.

Two road backends:
  * native      (default) — a road ribbon built directly on the LiDAR
                 elevation, carrying the real crossfall (per-point
                 yLeft/yRight edge heights) and per-point width when present.
                 Works with plain Blender (NO Track Tools).
  * tracktools  — appends the Track Tools ROAD (+kerb/markings) GeoNodes
                 modifiers, configured via introspected socket identifiers,
                 then re-applies tuned.json on top. Enable with
                 --tracktools <path-to-tracktools.blend>.

The collision glb is ALWAYS the native ribbon built from the effective
(tuned) widths, so the car's raycast surface tracks GUI width changes.

Axis convention: everything is the viewer's local ENU (x=east, y=up, z=−north).
The terrain is imported Y-up, which applies file(x,y,z) → Blender(x,−z,y);
we apply the same map (e2b) to geometry we build, and export glTF +Y-up so the
result returns to ENU for the viewer. No ICP / Z-flip anywhere.

Left/right: the viewer's signed lateral is the 2D cross product T×O, positive
to the driver's RIGHT; LEFT is the (tz,−tx) side of the planar tangent and is
the edge that yLeft/altLeft describes (see src/replay/interpolation.ts).

Usage (examples):
  blender -b -P build_track_blender.py -- --kit ../fuji --repo ../.. \
      --textures ../textures_cc0 [--tracktools /path/TrackTools.blend]
"""
import bpy, json, math, os, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import viewer_export as vx

MISSING_TEXTURES = []


def e2b(p):
    """ENU (x, y_up, z) -> Blender coords matching a Y-up OBJ import."""
    x, y, z = p
    return (x, -z, y)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, help="kit dir with build.json/centerline.json/terrain_lidar.*")
    ap.add_argument("--repo", required=True, help="repo root (for output placement)")
    ap.add_argument("--textures", default="", help="dir with CC0 asphalt/grass PBR maps")
    ap.add_argument("--tracktools", default="", help="path to TrackTools .blend (enables GeoNodes road)")
    ap.add_argument("--no-staging", action="store_true", help="skip saving the staging .blend")
    return ap.parse_args(argv)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _terrain_from_npz(path):
    """Fast mesh build from the prep-stage binary cache (foreach_set)."""
    import numpy as np
    d = np.load(path)
    enu = d["verts"].astype(np.float64)
    uvs = d["uvs"].astype(np.float64)
    n = int(d["grid"])
    bco = np.empty_like(enu)
    bco[:, 0] = enu[:, 0]
    bco[:, 1] = -enu[:, 2]
    bco[:, 2] = enu[:, 1]

    idx = np.arange(n * n).reshape(n, n)
    a = idx[:-1, :-1].ravel()
    quads = np.stack([a, a + n, a + n + 1, a + 1], axis=1)
    loops = quads.ravel()

    me = bpy.data.meshes.new("terrain_lidar")
    me.vertices.add(n * n)
    me.vertices.foreach_set("co", bco.ravel())
    me.loops.add(loops.size)
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(quads.shape[0])
    me.polygons.foreach_set("loop_start", np.arange(0, loops.size, 4))
    try:  # derived/read-only in newer Blender; harmless where writable
        me.polygons.foreach_set("loop_total", np.full(quads.shape[0], 4))
    except Exception:
        pass
    me.update(calc_edges=True)
    me.validate()
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", uvs[loops].ravel())
    ob = bpy.data.objects.new("terrain_lidar", me)
    bpy.context.collection.objects.link(ob)
    return ob


def import_terrain(kit):
    npz = os.path.join(kit, "terrain_lidar.npz")
    obj_path = os.path.join(kit, "terrain_lidar.obj")
    if os.path.exists(npz):
        try:
            ob = _terrain_from_npz(npz)
            print(f"[terrain] built from npz cache ({len(ob.data.vertices)} verts)")
            return ob
        except Exception as exc:
            print(f"[terrain] npz fast path failed ({exc}) -> OBJ import")
    bpy.ops.wm.obj_import(filepath=obj_path, up_axis="Y", forward_axis="NEGATIVE_Z")
    obj = bpy.context.selected_objects[0]
    obj.name = "terrain_lidar"
    return obj


def load_centerline(kit):
    d = json.load(open(os.path.join(kit, "centerline.json")))
    return d["points"], d["width"], d.get("closed", True)


def effective_widths(points, base_width, tuned):
    """Per-point width list. Priority: tuned per-station "widths" (dist-keyed,
    linearly interpolated) > per-point width in centerline.json > tuned global
    ROAD width > build.json width."""
    tuned_global = None
    stations = None
    if tuned:
        road = tuned.get("road", {})
        if isinstance(road.get("width_m"), (int, float)):
            tuned_global = float(road["width_m"])
        ws = tuned.get("widths")
        if isinstance(ws, list) and ws:
            stations = sorted((float(w["dist"]), float(w["width"])) for w in ws)

    def at_dist(dist):
        if not stations:
            return None
        if dist <= stations[0][0]:
            return stations[0][1]
        for (d0, w0), (d1, w1) in zip(stations, stations[1:]):
            if dist <= d1:
                t = (dist - d0) / (d1 - d0) if d1 > d0 else 0.0
                return w0 + (w1 - w0) * t
        return stations[-1][1]

    fallback = tuned_global if tuned_global is not None else base_width
    out = []
    for p in points:
        w = at_dist(p.get("dist", 0.0))
        if w is None:
            w = p.get("width") or fallback
        out.append(float(w))
    return out


def effective_centers(points, tuned):
    """Per-point signed midline displacement (m, + = driver's left) from
    tuned.json "centers" (dist-keyed, linearly interpolated). Zeros when the
    key is absent, so older tuned.json files keep building symmetrically."""
    stations = None
    if tuned:
        cs = tuned.get("centers")
        if isinstance(cs, list) and cs:
            stations = sorted((float(c["dist"]), float(c["center"])) for c in cs)
    if not stations:
        return [0.0] * len(points)

    def at_dist(dist):
        if dist <= stations[0][0]:
            return stations[0][1]
        for (d0, c0), (d1, c1) in zip(stations, stations[1:]):
            if dist <= d1:
                t = (dist - d0) / (d1 - d0) if d1 > d0 else 0.0
                return c0 + (c1 - c0) * t
        return stations[-1][1]

    return [float(at_dist(p.get("dist", 0.0))) for p in points]


def recenter_points(points, centers, closed):
    """Shift each ENU centerline point laterally by centers[i] along the
    driver-left normal. The editor's road.edges are asymmetric about the
    survey centerline (fuji: up to ~5 m on the pit straight); a symmetric
    extrusion around the unshifted line visibly misplaces the road against
    the satellite photo, so the midline must move to (left-right)/2 first.
    Convention: tangent t=(tx,tz) along increasing dist in ENU (x, z);
    driver's right normal = (-tz, tx), so driver's LEFT = (tz, -tx)."""
    n = len(points)
    if n < 3 or all(abs(c) < 1e-9 for c in centers):
        return points
    out = []
    for i, p in enumerate(points):
        ia = (i - 1) % n if closed else max(0, i - 1)
        ib = (i + 1) % n if closed else min(n - 1, i + 1)
        tx = points[ib]["x"] - points[ia]["x"]
        tz = points[ib]["z"] - points[ia]["z"]
        norm = math.hypot(tx, tz) or 1.0
        lx, lz = tz / norm, -tx / norm  # driver's left
        q = dict(p)
        q["x"] = p["x"] + lx * centers[i]
        q["z"] = p["z"] + lz * centers[i]
        out.append(q)
    return out


def make_centerline_curve(points, closed, radii=None):
    """POLY curve through the ENU points. Per-point `radii` drive Track Tools'
    variable width: the ROAD group multiplies its "road width" input by each
    curve point's radius (verified empirically: width_eff = width x radius),
    so radii = width_i / base_width reproduces the per-station widths."""
    cu = bpy.data.curves.new("centerline", type="CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for i, p in enumerate(points):
        bx, by, bz = e2b((p["x"], p["y"], p["z"]))
        sp.points[i].co = (bx, by, bz, 1.0)
        if radii is not None:
            sp.points[i].radius = float(radii[i])
    sp.use_cyclic_u = closed
    ob = bpy.data.objects.new("centerline", cu)
    bpy.context.collection.objects.link(ob)
    return ob


def build_road_native(points, widths, closed, name="road"):
    """Road ribbon on the LiDAR elevation with real crossfall: edge heights
    come from per-point yLeft/yRight (LiDAR/reconstruction edge elevations)
    when present, falling back to the flat centerline height. Width may vary
    per station (widths[i]). UVs tile along width/length."""
    n = len(points)
    P = [(p["x"], p["y"], p["z"]) for p in points]

    def tangent(i):
        a = P[(i - 1) % n]; b = P[(i + 1) % n]
        tx, tz = b[0] - a[0], b[2] - a[2]
        L = math.hypot(tx, tz) or 1.0
        return tx / L, tz / L

    verts, uvs, faces = [], [], []
    cum = 0.0
    for i in range(n):
        x, y, z = P[i]
        tx, tz = tangent(i)
        hw = widths[i] / 2.0
        # RIGHT = (−tz, tx) side (positive signed lateral), LEFT = (tz, −tx).
        yR = points[i].get("yRight", y)
        yL = points[i].get("yLeft", y)
        rx, rz = x - hw * tz, z + hw * tx
        lx, lz = x + hw * tz, z - hw * tx
        verts.append(e2b((rx, yR, rz)))
        verts.append(e2b((lx, yL, lz)))
        if i > 0:
            cum += math.dist(P[i][::2], P[i - 1][::2])
        v = cum / 10.0
        uvs.append((0.0, v)); uvs.append((1.0, v))
    rng = n if closed else n - 1
    for i in range(rng):
        a = (i * 2) % (n * 2); b = a + 1
        c = ((i + 1) % n) * 2; d = c + 1
        faces.append((a, c, d, b))
    me = bpy.data.meshes.new(name)
    me.from_pydata([v for v in verts], [], faces)
    me.update()
    uvl = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            vi = me.loops[li].vertex_index
            uvl.data[li].uv = uvs[vi]
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    return ob


# --------------------------------------------------------------------- kerbs
# Corner-only racing kerbs: real tracks have kerbs at corners, not down the
# straights, and never on the road's centre line. We find high-curvature
# stations from the recentered midline, build a POLY curve offset to each road
# edge over those runs (with lead-in/out), and stack RACING_KERB on it with a
# red/white striped image material (image textures survive glTF export; raw
# procedural node graphs do not).

KERB_LIFT = 0.09          # m above the sampled ground (road sits at terrain+0.08)
KERB_WIDTH = 1.15         # m lateral width of the kerb band
KERB_DENSIFY = 2.0        # m resample step for kerb curves (predictable UVs)
# Which side's spline to reverse so the kerb band extends OUTWARD (away from the
# road) on both edges. RACING_KERB builds the band to one handedness side of the
# curve; reversing the point order on one edge flips it. Tuned by screenshot.
KERB_REVERSE_LEFT = False
KERB_REVERSE_RIGHT = True


def _left_normal(points, i, closed):
    """Unit driver-left normal (tz,-tx) of the ENU tangent at point i."""
    n = len(points)
    ia = (i - 1) % n if closed else max(0, i - 1)
    ib = (i + 1) % n if closed else min(n - 1, i + 1)
    tx = points[ib]["x"] - points[ia]["x"]
    tz = points[ib]["z"] - points[ia]["z"]
    nrm = math.hypot(tx, tz) or 1.0
    return tz / nrm, -tx / nrm


def _seg_len(points, i, j):
    return math.hypot(points[j]["x"] - points[i]["x"],
                      points[j]["z"] - points[i]["z"])


def kerb_mask(points, closed, window=24.0, radius_thresh=170.0):
    """Boolean per-station mask: True where the local turn radius is tighter
    than `radius_thresh` m. Curvature is the heading change over a ~`window` m
    arc centred on each station (delta = arc/radius), which is robust to the
    ~7 m point spacing and small survey noise."""
    n = len(points)
    X = [p["x"] for p in points]
    Z = [p["z"] for p in points]

    def advance(i, direction):
        acc = 0.0
        j = i
        for _ in range(n):
            j2 = (j + direction) % n if closed else max(0, min(n - 1, j + direction))
            if j2 == j:
                break
            acc += math.hypot(X[j2] - X[j], Z[j2] - Z[j])
            j = j2
            if acc >= window / 2.0:
                break
        return j

    thr = window / radius_thresh   # heading-change threshold (rad) over the arc
    mask = [False] * n
    for i in range(n):
        a = advance(i, 1)
        b = advance(i, -1)
        hin = math.atan2(Z[i] - Z[b], X[i] - X[b])
        hout = math.atan2(Z[a] - Z[i], X[a] - X[i])
        d = hout - hin
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        mask[i] = abs(d) > thr
    return mask


def _dilate(points, mask, closed, radius):
    """Grow each True region by `radius` m on both sides (lead-in/out); also
    merges corner runs separated by less than 2*radius of straight."""
    n = len(points)
    X = [p["x"] for p in points]
    Z = [p["z"] for p in points]
    out = [False] * n
    for i in range(n):
        if not mask[i]:
            continue
        out[i] = True
        for direction in (1, -1):
            acc = 0.0
            j = i
            while acc < radius:
                j2 = (j + direction) % n if closed else max(0, min(n - 1, j + direction))
                if j2 == j:
                    break
                acc += math.hypot(X[j2] - X[j], Z[j2] - Z[j])
                j = j2
                out[j] = True
    return out


def _runs_from_mask(mask, closed):
    """Contiguous True index runs (ordered), respecting the closed-loop wrap."""
    n = len(mask)
    if not any(mask):
        return []
    if all(mask):
        return [list(range(n))]
    start = 0
    if closed:
        while mask[start]:
            start += 1
    order = [(start + k) % n for k in range(n)]
    runs, cur = [], []
    for idx in order:
        if mask[idx]:
            cur.append(idx)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def kerb_runs(points, closed, lead=12.0, min_len=16.0,
              window=24.0, radius_thresh=170.0):
    """List of (run_indices, length_m). Corner stations, dilated by `lead`,
    with runs shorter than `min_len` dropped."""
    mask = kerb_mask(points, closed, window, radius_thresh)
    mask = _dilate(points, mask, closed, lead)
    out = []
    for run in _runs_from_mask(mask, closed):
        length = sum(_seg_len(points, run[k], run[k + 1])
                     for k in range(len(run) - 1))
        if length >= min_len:
            out.append((run, length))
    return out


def _densify(pts, step):
    """Resample a polyline of (x,y,z) ENU points to ~`step` m spacing."""
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.dist((a[0], a[2]), (b[0], b[2]))
        k = max(1, int(round(d / step)))
        for s in range(1, k + 1):
            t = s / k
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                        a[2] + (b[2] - a[2]) * t))
    return out


def make_terrain_sampler(terrain_obj):
    """Return f(x_enu, z_enu) -> ground y (ENU up), raycasting the EVALUATED
    terrain (TERRAIN_SHAPE applied) downward along Blender +Z. Kerbs then sit on
    the same surface the road shrinkwraps to."""
    from mathutils.bvhtree import BVHTree
    from mathutils import Vector
    deps = bpy.context.evaluated_depsgraph_get()
    ev = terrain_obj.evaluated_get(deps)
    me = ev.to_mesh()
    bvh = BVHTree.FromPolygons([v.co.copy() for v in me.vertices],
                               [tuple(p.vertices) for p in me.polygons])
    ev.to_mesh_clear()
    down = Vector((0.0, 0.0, -1.0))

    def height_at(x_enu, z_enu):
        origin = Vector((x_enu, -z_enu, 5000.0))   # e2b: by = -z_enu, up = +Z
        loc, _n, _i, _d = bvh.ray_cast(origin, down)
        return loc.z if loc is not None else None   # Blender Z == ENU up

    return height_at


def make_kerb_stripe_material(name="kerb_stripes"):
    """Red/white striped kerb material via a small tiling image texture (the
    only kind of stripe that survives glTF export). Stripes run along the kerb
    length (image X axis maps to the kerb U coordinate)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.85
    W = H = 16
    img = bpy.data.images.new(name + "_img", W, H, alpha=False)
    red, white = (0.72, 0.10, 0.10), (0.90, 0.90, 0.90)
    px = [0.0] * (W * H * 4)
    for y in range(H):
        for x in range(W):
            c = red if x < W // 2 else white   # varies along image X (= U/length)
            o = (y * W + x) * 4
            px[o], px[o + 1], px[o + 2], px[o + 3] = c[0], c[1], c[2], 1.0
    img.pixels = px
    img.pack()
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    tex.extension = "REPEAT"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def build_kerbs(points, widths, closed, terrain_obj, kerb_mat,
                lead=12.0, min_len=16.0, radius_thresh=170.0):
    """Build corner-only racing kerbs on both road edges. Returns the list of
    kerb objects (placed in the SCENE collection by the caller)."""
    kb_ng = bpy.data.node_groups.get("RACING_KERB")
    if kb_ng is None:
        print("[kerb] RACING_KERB node group not found — skipping kerbs")
        return []
    ground = make_terrain_sampler(terrain_obj)
    runs = kerb_runs(points, closed, lead=lead, min_len=min_len,
                     radius_thresh=radius_thresh)
    print(f"[kerb] {len(runs)} corner run(s) detected "
          f"(radius<{radius_thresh:.0f} m, lead {lead:.0f} m):")
    objs = []
    for r, (run, length) in enumerate(runs):
        d0 = points[run[0]].get("dist", 0.0)
        d1 = points[run[-1]].get("dist", 0.0)
        if r < 8:
            print(f"   run {r:2d}: dist {d0:7.1f}..{d1:7.1f} m  ({length:5.1f} m)")
        for side, rev in ((1, KERB_REVERSE_LEFT), (-1, KERB_REVERSE_RIGHT)):
            pts = []
            for i in run:
                lx, lz = _left_normal(points, i, closed)
                hw = widths[i] / 2.0
                ex = points[i]["x"] + side * lx * hw
                ez = points[i]["z"] + side * lz * hw
                gy = ground(ex, ez)
                ey = (gy if gy is not None else points[i]["y"]) + KERB_LIFT
                pts.append((ex, ey, ez))
            pts = _densify(pts, KERB_DENSIFY)
            if rev:
                pts = list(reversed(pts))
            cu = bpy.data.curves.new(f"kerb_{r}_{'L' if side > 0 else 'R'}",
                                     type="CURVE")
            cu.dimensions = "3D"
            sp = cu.splines.new("POLY")
            sp.points.add(len(pts) - 1)
            for k, (ex, ey, ez) in enumerate(pts):
                bx, by, bz = e2b((ex, ey, ez))
                sp.points[k].co = (bx, by, bz, 1.0)
            ob = bpy.data.objects.new(cu.name, cu)
            bpy.context.collection.objects.link(ob)
            km = ob.modifiers.new("RACING_KERB", "NODES")
            km.node_group = kb_ng
            _set(km, kb_ng, "width", KERB_WIDTH)
            _set(km, kb_ng, "kerb type", 1)        # stepped
            _set(km, kb_ng, "kerb height", 0.05)
            _set(km, kb_ng, "step height", 0.02)
            _set(km, kb_ng, "end length", 3.0)
            _set(km, kb_ng, "scale ends", True)
            _set(km, kb_ng, "UVs", True)
            if kerb_mat:
                _set(km, kb_ng, "Material", kerb_mat)
            objs.append(ob)
    print(f"[kerb] built {len(objs)} kerb object(s)")
    return objs


def _ident(ng, exact):
    for item in ng.interface.items_tree:
        if getattr(item, "in_out", None) == "INPUT" and item.name == exact:
            return item.identifier
    return None


def _set(mod, ng, exact, value):
    ident = _ident(ng, exact)
    if ident is None:
        print("   [tt] input not found:", exact)
        return False
    try:
        mod[ident] = value
        return True
    except Exception as e:
        print("   [tt] set failed:", exact, e)
        return False


def build_road_tracktools(curve_obj, terrain_obj, width, resolution, tt_blend,
                          asphalt_mat, white_mat=None, kerb_mat=None,
                          road_z_offset=0.08):
    """Append Track Tools node groups (verified names from tracktools_schema.json)
    and stack ROAD (+MARKINGS +RACING_KERB) on the centerline curve. Returns the
    curve object carrying the visual road, or None to fall back to native.

    Also stacks TERRAIN_SHAPE on the terrain, targeting the road curve: without
    it the shrinkwrapped road dips below the terrain triangulation in places
    (measured up to 0.63 m on fuji) and renders with apparent gaps. A small
    ROAD "Z offset" lifts the surface clear of residual z-fighting."""
    wanted = ["ROAD", "MARKINGS", "RACING_KERB", "TERRAIN_SHAPE"]
    with bpy.data.libraries.load(tt_blend, link=False) as (src, dst):
        dst.node_groups = [n for n in src.node_groups if n in wanted]

    road_ng = bpy.data.node_groups.get("ROAD")
    if road_ng is None:
        print("[tt] ROAD node group not found -> native fallback")
        return None
    m = curve_obj.modifiers.new("ROAD", "NODES")
    m.node_group = road_ng
    _set(m, road_ng, "road width", float(width))
    _set(m, road_ng, "resolution", float(resolution))
    _set(m, road_ng, "reference terrain", terrain_obj)   # source elevation from LiDAR
    _set(m, road_ng, "Shrinkwrap", True)
    _set(m, road_ng, "UVs", True)
    _set(m, road_ng, "Z offset", float(road_z_offset))
    if asphalt_mat:
        _set(m, road_ng, "material", asphalt_mat)
    print("[tt] ROAD applied (width", width, ", res", resolution,
          ", shrinkwrap terrain ON, z offset", road_z_offset, ")")

    mk_ng = bpy.data.node_groups.get("MARKINGS")
    if mk_ng is not None:
        mm = curve_obj.modifiers.new("MARKINGS", "NODES")
        mm.node_group = mk_ng
        # Menu sockets store the enum's int value: none=5 (full=0, dashed=1).
        # Race tracks have no centre dashed line; keep the border lines (full).
        _set(mm, mk_ng, "middle line type", 5)
        if white_mat:
            _set(mm, mk_ng, "material", white_mat)
        print("[tt] MARKINGS stacked (middle line: none, border: full)")

    # RACING_KERB is intentionally NOT stacked on the road curve here. Stacking
    # it on the ROAD-output curves rendered a solid red band down BOTH road
    # edges for the entire lap PLUS a red line on the middle curve. Kerbs are
    # instead built as separate offset curves at CORNERS only (see build_kerbs
    # / kerb_runs in main); RACING_KERB is applied per-run to those curves.

    ts_ng = bpy.data.node_groups.get("TERRAIN_SHAPE")
    if ts_ng is not None:
        tm = terrain_obj.modifiers.new("TERRAIN_SHAPE", "NODES")
        tm.node_group = ts_ng
        _set(tm, ts_ng, "target object", curve_obj)
        _set(tm, ts_ng, "Z offset", -0.10)   # sink conformed terrain below verge
        print("[tt] TERRAIN_SHAPE stacked on terrain (target = road curve, z -0.10)")
    else:
        print("[tt] TERRAIN_SHAPE not found — terrain left as-is")
    return curve_obj


def make_pbr_material(name, tex_dir, albedo, normal=None, rough=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")

    def img(fname, non_color=False):
        path = os.path.join(tex_dir, fname)
        if not (tex_dir and os.path.exists(path)):
            if fname:
                MISSING_TEXTURES.append(f"{name}: {fname}")
            return None
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path)
        if non_color:
            node.image.colorspace_settings.name = "Non-Color"
        return node

    a = img(albedo)
    if a:
        nt.links.new(a.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (0.35, 0.35, 0.36, 1)
    if rough:
        r = img(rough, non_color=True)
        if r:
            nt.links.new(r.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.9
    if normal:
        nmap = img(normal, non_color=True)
        if nmap:
            nn = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(nmap.outputs["Color"], nn.inputs["Color"])
            nt.links.new(nn.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def make_solid(name, rgb):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.85
    return mat


def assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def ensure_collection(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def place_in(obj, *coll_names):
    for uc in list(obj.users_collection):
        uc.objects.unlink(obj)
    for name in coll_names:
        ensure_collection(name).objects.link(obj)


def save_staging_blend(kit, repo, track):
    """Save the live scene as the GUI tuning file: modifiers unapplied,
    SCENE/COLLISION/REF collections, kit/repo paths as scene properties and
    viewer_export.py embedded as a runnable text block."""
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer_export.py")
    txt = bpy.data.texts.get("EXPORT_FOR_VIEWER.py") or bpy.data.texts.new("EXPORT_FOR_VIEWER.py")
    txt.from_string(open(src_path, encoding="utf-8").read())
    scene = bpy.context.scene
    scene["tt_kit"] = kit
    scene["tt_repo"] = repo
    scene["tt_track"] = track
    path = os.path.join(kit, f"{track}_staging.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
    print(f"[staging] saved {path}")
    print("          open it, tune width/kerbs, then run the EXPORT_FOR_VIEWER "
          "text block (Scripting tab) to export + write tuned.json")


def main():
    args = parse_args()
    kit = os.path.abspath(args.kit)
    repo = os.path.abspath(args.repo)
    build = json.load(open(os.path.join(kit, "build.json")))
    track = build["track"]
    resolution = build.get("road", {}).get("resolution", 2.0)

    reset_scene()
    terrain = import_terrain(kit)
    points, base_width, closed = load_centerline(kit)

    tuned = vx.load_tuned(kit)
    widths = effective_widths(points, base_width, tuned)
    centers = effective_centers(points, tuned)
    if any(abs(c) > 1e-9 for c in centers):
        cmin, cmax = min(centers), max(centers)
        print(f"[recenter] midline displaced by tuned centers: {cmin:+.2f}..{cmax:+.2f} m"
              " (+ = driver's left; road midline now matches the editor's edges)")
        points = recenter_points(points, centers, closed)
    tt_width = vx.tuned_road_width(kit) or base_width

    # --- materials (CC0) ---
    asphalt = make_pbr_material("asphalt", args.textures, "asphalt_albedo.jpg",
                                normal="asphalt_normal.jpg", rough="asphalt_rough.jpg")
    grass = make_pbr_material("grass", args.textures, "grass_albedo.jpg")
    white = make_solid("markings", (0.9, 0.9, 0.9))
    kerb = make_solid("kerb", (0.75, 0.12, 0.12))
    assign(terrain, grass)

    # --- visual road (Track Tools if available, else native ribbon) ---
    visual = None
    curve = None
    if args.tracktools:
        # Per-point radii carry the tuned per-station widths into the Track
        # Tools road (radius multiplies the ROAD "road width" input).
        radii = [w / tt_width for w in widths]
        rmin, rmax = min(radii), max(radii)
        if abs(rmax - rmin) > 1e-6:
            print(f"[tt] variable width via curve radius: {min(widths):.1f}-{max(widths):.1f} m"
                  f" (radius {rmin:.3f}-{rmax:.3f} @ base {tt_width} m)")
        curve = make_centerline_curve(points, closed, radii)
        visual = build_road_tracktools(curve, terrain, tt_width, resolution,
                                       args.tracktools, asphalt, white, kerb)
        if visual is not None and tuned:
            vx.apply_tuned(kit)   # GUI tuning wins over the defaults above
    native_visual = visual is None

    # --- corner-only racing kerbs (Track Tools backend only) ---
    kerbs = []
    if not native_visual:
        kerb_stripes = make_kerb_stripe_material()
        kerbs = build_kerbs(points, widths, closed, terrain, kerb_stripes)
    if native_visual:
        visual = build_road_native(points, widths, closed)
        assign(visual, asphalt)

    # --- collision: native ribbon at the EFFECTIVE widths (car raycast) ---
    if native_visual:
        collision = visual                      # same low-poly mesh
    else:
        collision = build_road_native(points, widths, closed, name="road_collision")

    # --- collections (contract shared with viewer_export.gui_export) ---
    place_in(terrain, vx.SCENE_COLL)
    if native_visual:
        place_in(visual, vx.SCENE_COLL, vx.COLLISION_COLL)
    else:
        place_in(visual, vx.SCENE_COLL)
        place_in(collision, vx.COLLISION_COLL)
    if curve is not None and curve is not visual:
        place_in(curve, vx.REF_COLL)
    for kb in kerbs:
        place_in(kb, vx.SCENE_COLL)   # visual only — NOT in COLLISION

    # --- exports ---
    scene_glb = os.path.join(repo, build["output"]["scene_glb"].replace("/", os.sep))
    collision_glb = os.path.join(repo, build["output"]["collision_glb"].replace("/", os.sep))
    vx.export_glb([terrain, visual] + kerbs, scene_glb)
    vx.export_glb([collision], collision_glb)

    lo, hi = vx.enu_bounds([terrain, visual])
    print(f"[sanity] scene ENU bounds  x [{lo[0]:.0f}, {hi[0]:.0f}]"
          f"  y [{lo[1]:.0f}, {hi[1]:.0f}]  z [{lo[2]:.0f}, {hi[2]:.0f}]"
          "  — compare with track.json bounds")

    # --- staging .blend for GUI tuning ---
    if not args.no_staging:
        save_staging_blend(kit, repo, track)

    if MISSING_TEXTURES:
        print("\n" + "!" * 66)
        print("[textures] MISSING — these surfaces exported as flat colour:")
        for m in MISSING_TEXTURES:
            print("   -", m)
        print("[textures] put CC0 maps in", args.textures or "(no --textures dir given)",
              "or run: python fetch_textures.py")
        print("!" * 66)

    wmin, wmax = min(widths), max(widths)
    wtxt = f"{wmin:.1f}" if abs(wmax - wmin) < 0.05 else f"{wmin:.1f}-{wmax:.1f}"
    print(f"[done] {track}: scene + collision exported "
          f"(backend={'native' if native_visual else 'tracktools'}, width {wtxt} m"
          f"{', tuned.json applied' if tuned else ''})")


if __name__ == "__main__":
    main()
