"""inspect_tracktools.py — run AFTER buying Track Tools, once, to dump the
GeoNodes node-group input schema. The driver (build_track_blender.py) uses
name->identifier matching, but you still need to know the real input NAMES
(e.g. is the terrain input called "Terrain", "Reference", "Elevation"?).

Usage:
  blender -b -P inspect_tracktools.py -- --tracktools /path/TrackTools.blend
Writes tracktools_schema.json next to this script and prints a summary.
"""
import bpy, json, os, sys, argparse

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--tracktools", required=True)
ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "tracktools_schema.json"))
args = ap.parse_args(argv)

with bpy.data.libraries.load(args.tracktools, link=False) as (src, dst):
    dst.node_groups = list(src.node_groups)

schema = {}
for ng in bpy.data.node_groups:
    if not hasattr(ng, "interface"):
        continue
    inputs, outputs = [], []
    for item in ng.interface.items_tree:
        io = getattr(item, "in_out", None)
        if io is None:
            continue
        rec = {"name": item.name, "identifier": item.identifier,
               "socket_type": getattr(item, "socket_type", "")}
        (inputs if io == "INPUT" else outputs).append(rec)
    schema[ng.name] = {"inputs": inputs, "outputs": outputs}

json.dump(schema, open(args.out, "w"), indent=2, ensure_ascii=False)
print(f"[inspect] wrote {args.out} — {len(schema)} node groups")
for name in ("ROAD", "MARKINGS", "RACING_KERB", "EDGE", "BARRIER",
             "TERRAIN_GEOMETRY", "TERRAIN_SHAPE", "TREES"):
    ng = schema.get(name)
    if ng:
        print(f"  {name}: inputs = {[i['name'] for i in ng['inputs']]}")
