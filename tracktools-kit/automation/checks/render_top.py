"""Render top-down views of a scene.glb: full track + crops at the
highest-curvature corners (computed from centerline.json). Cycles CPU,
low samples — enough to see gaps/z-fighting.

blender -b -P render_top.py -- --glb <scene.glb> --kit <kit> --out <dir>
"""
import bpy, json, math, os, sys, argparse

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--glb", required=True)
ap.add_argument("--kit", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args(argv)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.glb)

# world: plain white sun so gaps read as terrain-through-road
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", type="SUN"))
sun.data.energy = 4.0
sun.rotation_euler = (math.radians(15), 0, 0)
bpy.context.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.8, 0.8, 0.8, 1)
bpy.context.scene.world = world

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
bpy.context.collection.objects.link(cam)
bpy.context.scene.camera = cam

sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 8
sc.cycles.use_denoising = False
sc.render.resolution_x = sc.render.resolution_y = 1000

pts = json.load(open(os.path.join(a.kit, "centerline.json")))["points"]
n = len(pts)

def curvature(i):
    p0, p1, p2 = pts[(i - 4) % n], pts[i], pts[(i + 4) % n]
    v1 = (p1["x"] - p0["x"], p1["z"] - p0["z"])
    v2 = (p2["x"] - p1["x"], p2["z"] - p1["z"])
    a1 = math.atan2(v1[1], v1[0]); a2 = math.atan2(v2[1], v2[0])
    d = abs(a2 - a1)
    return min(d, 2 * math.pi - d)

# top corners, separated by >=60 idx
cand = sorted(range(n), key=curvature, reverse=True)
corners = []
for i in cand:
    if all(min(abs(i - j), n - abs(i - j)) > 60 for j in corners):
        corners.append(i)
    if len(corners) >= 4:
        break

def shoot(name, x, z, ext):
    # ENU (x,y,z) -> Blender (x,-z,y): camera above (x, -z), looking -Z
    cam.location = (x, -z, 900)
    cam.rotation_euler = (0, 0, 0)
    cam.data.ortho_scale = ext * 2
    cam.data.clip_end = 3000
    sc.render.filepath = os.path.join(a.out, name + ".png")
    bpy.ops.render.render(write_still=True)
    print("[render]", name)

xs = [p["x"] for p in pts]; zs = [p["z"] for p in pts]
shoot("full", (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2,
      max(max(xs) - min(xs), max(zs) - min(zs)) / 2 + 50)
for k, i in enumerate(corners):
    shoot(f"corner{k}_idx{i}", pts[i]["x"], pts[i]["z"], 130)
sys.exit(0)
