"""viewer_export.py — shared Blender-side export + tuned-parameter round-trip.

Single source of truth for how a track leaves Blender, used two ways:
  * imported by build_track_blender.py (headless builds), and
  * embedded as the "EXPORT_FOR_VIEWER.py" text block in the staging .blend
    that the headless build saves — in the GUI you tune width/kerbs, then just
    press Run on that text block. It exports scene.glb + the collision glb with
    exactly the headless settings AND dumps every Track Tools modifier input to
    <kit>/tuned.json so the next headless rebuild reproduces your tuning.

tuned.json is the "parameter overlay": prep_data.py never writes it, headless
builds only read it. Scene edits that don't serialize (added objects, curve
edits, masks) stay authoritative in the .blend itself.

Collections contract (created by the headless build, kept by the GUI user):
  SCENE      everything that ships in scene.glb (terrain, road, kerbs, ...)
  COLLISION  the drivable surface exported as the car-grounding glb
             (road ribbon; move drivable kerbs here too if you add them)
  REF        reference-only stuff (centerline curve, logger overlays,
             satellite backdrop) — never exported
"""
import bpy
import json
import os

TUNED_FILE = "tuned.json"
SCENE_COLL = "SCENE"
COLLISION_COLL = "COLLISION"
REF_COLL = "REF"

# Image-name fragments that mean "licensed reference imagery" — exporting a
# material that samples one of these would bake Esri/Bing pixels into the glb.
SATELLITE_MARKERS = ("sat", "esri", "bing", "aerial")


# ---------------------------------------------------------------- coordinates

def blender_to_enu(v):
    """Blender (x, y, z) -> viewer ENU (x, y_up, z); inverse of e2b."""
    return (v[0], v[2], -v[1])


def enu_bounds(objects):
    """World-space bounds of `objects` expressed in viewer ENU, for the
    round-trip sanity print (compare against track.json bounds)."""
    from mathutils import Vector
    deps = bpy.context.evaluated_depsgraph_get()
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for obj in objects:
        ev = obj.evaluated_get(deps)
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            e = blender_to_enu((w.x, w.y, w.z))
            for k in range(3):
                lo[k] = min(lo[k], e[k])
                hi[k] = max(hi[k], e[k])
    return lo, hi


# ---------------------------------------------------------------- glTF export

def _realized_copy(obj):
    """Temporary MESH copy of a non-mesh object with modifiers/GeoNodes
    realized. The glTF exporter's own export_apply drops the material slots
    when it realizes CURVE objects (Blender 5.1: road/markings/kerb came out
    with no material even though the evaluated mesh carries all slots), so we
    realize via new_from_object ourselves and export the mesh copy."""
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True,
                                         depsgraph=deps)
    dup = bpy.data.objects.new(obj.name, me)
    dup.matrix_world = obj.matrix_world.copy()
    bpy.context.collection.objects.link(dup)
    return dup


def export_glb(objects, path):
    """The one true glTF export: +Y up, apply modifiers, embed materials."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temps = []
    export_objs = []
    for o in objects:
        if o.type == "MESH":
            export_objs.append(o)
        else:
            dup = _realized_copy(o)
            temps.append(dup)
            export_objs.append(dup)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for o in export_objs:
            o.hide_set(False)
            o.hide_viewport = False
            o.select_set(True)
        bpy.context.view_layer.objects.active = export_objs[0]
        bpy.ops.export_scene.gltf(
            filepath=path, export_format="GLB", use_selection=True,
            export_apply=True, export_yup=True, export_normals=True,
            export_texcoords=True, export_materials="EXPORT",
        )
    finally:
        for dup in temps:
            me = dup.data
            bpy.data.objects.remove(dup)
            bpy.data.meshes.remove(me)
    size_mb = os.path.getsize(path) / 1e6
    print(f"[export] {path}  ({size_mb:.1f} MB)")
    return size_mb


def check_no_satellite(objects):
    """Return material names that sample licensed reference imagery."""
    bad = []
    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            mat = slot.material
            if not (mat and mat.use_nodes):
                continue
            for node in mat.node_tree.nodes:
                img = getattr(node, "image", None)
                if img is None:
                    continue
                name = (img.name + " " + (img.filepath or "")).lower()
                if any(m in name for m in SATELLITE_MARKERS):
                    bad.append(f"{obj.name}/{mat.name} -> {img.name}")
    return bad


# ------------------------------------------------------- tuned.json round-trip

def _serialize_value(value):
    if isinstance(value, bpy.types.Object):
        return {"__object__": value.name}
    if isinstance(value, bpy.types.Material):
        return {"__material__": value.name}
    if isinstance(value, (bool, int, float, str)):
        return value
    try:  # colors / vectors come back as bpy_prop_array
        return [float(x) for x in value]
    except TypeError:
        return None


def _deserialize_value(raw):
    if isinstance(raw, dict):
        if "__object__" in raw:
            return bpy.data.objects.get(raw["__object__"])
        if "__material__" in raw:
            return bpy.data.materials.get(raw["__material__"])
    return raw


def _input_items(node_group):
    for item in node_group.interface.items_tree:
        if getattr(item, "in_out", None) != "INPUT":
            continue
        if item.socket_type == "NodeSocketGeometry":
            continue
        yield item


def dump_tuned(kit_dir):
    """Serialize every GeoNodes modifier's inputs, keyed by node group +
    socket identifier (display names in Track Tools have duplicates/typos —
    identifiers are the stable key). Returns the path written."""
    entries = []
    road_width = None
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type != "NODES" or mod.node_group is None:
                continue
            ng = mod.node_group
            inputs = {}
            for item in _input_items(ng):
                try:
                    raw = mod[item.identifier]
                except KeyError:
                    continue
                val = _serialize_value(raw)
                if val is None:
                    continue
                inputs[item.identifier] = {"name": item.name, "value": val}
                if ng.name == "ROAD" and item.name == "road width":
                    road_width = val
            entries.append({
                "object": obj.name, "modifier": mod.name,
                "node_group": ng.name, "inputs": inputs,
            })
    doc = {"version": 1, "modifiers": entries}
    if isinstance(road_width, (int, float)):
        doc["road"] = {"width_m": float(road_width)}
    path = os.path.join(kit_dir, TUNED_FILE)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"[tuned] dumped {len(entries)} modifier(s) -> {path}")
    return path


def load_tuned(kit_dir):
    path = os.path.join(kit_dir, TUNED_FILE)
    if not os.path.exists(path):
        return None
    return json.load(open(path))


def apply_tuned(kit_dir):
    """Re-apply a tuned.json dump to the modifiers in the current scene.
    Matches by node-group name (object names differ between GUI and headless
    builds); socket identifiers are validated against the live node group.
    Entries whose node group isn't instantiated are reported, not fatal."""
    doc = load_tuned(kit_dir)
    if not doc:
        return 0
    live = {}  # node_group name -> [modifier, ...]
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group is not None:
                live.setdefault(mod.node_group.name, []).append(mod)
    applied = 0
    for entry in doc.get("modifiers", []):
        mods = live.get(entry["node_group"])
        if not mods:
            print(f"[tuned] no live modifier for node group '{entry['node_group']}'"
                  " — that tuning lives in the staging .blend only")
            continue
        mod = next((m for m in mods if m.id_data.name == entry["object"]), mods[0])
        valid = {i.identifier for i in _input_items(mod.node_group)}
        for ident, slot in entry["inputs"].items():
            if ident not in valid:
                print(f"[tuned] {entry['node_group']}: unknown socket {ident}"
                      f" ('{slot['name']}') — Track Tools version changed?")
                continue
            value = _deserialize_value(slot["value"])
            if value is None and isinstance(slot["value"], dict):
                print(f"[tuned] {entry['node_group']}.{slot['name']}: referenced"
                      f" datablock {slot['value']} not in this file — skipped")
                continue
            try:
                mod[ident] = value
                applied += 1
            except Exception as exc:  # socket type drift — warn, keep going
                print(f"[tuned] set failed {entry['node_group']}.{slot['name']}: {exc}")
    print(f"[tuned] applied {applied} socket value(s) from {TUNED_FILE}")
    return applied


def tuned_road_width(kit_dir):
    """Convenience for the headless build: the GUI-tuned global road width."""
    doc = load_tuned(kit_dir)
    if doc and isinstance(doc.get("road", {}).get("width_m"), (int, float)):
        return float(doc["road"]["width_m"])
    return None


# ---------------------------------------------------------------- GUI entry

def _collection_objects(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        return None
    return [o for o in coll.all_objects if o.type in {"MESH", "CURVE"}]


def gui_export():
    """Run from the EXPORT_FOR_VIEWER text block inside the staging .blend."""
    scene = bpy.context.scene
    kit = scene.get("tt_kit")
    repo = scene.get("tt_repo")
    if not (kit and repo):
        raise RuntimeError(
            "This file has no tt_kit/tt_repo scene properties. Open the staging "
            ".blend produced by make_track.py (or set the properties by hand).")

    build = json.load(open(os.path.join(kit, "build.json")))
    scene_objs = _collection_objects(SCENE_COLL)
    coll_objs = _collection_objects(COLLISION_COLL)
    if not scene_objs or not coll_objs:
        raise RuntimeError(
            f"Collections '{SCENE_COLL}' and '{COLLISION_COLL}' must exist and "
            "contain the exportable objects (the staging .blend sets them up).")

    bad = check_no_satellite(scene_objs)
    if bad:
        raise RuntimeError(
            "Satellite/licensed reference imagery is still assigned — switch the "
            "terrain back to the CC0 material before exporting:\n  " + "\n  ".join(bad))

    dump_tuned(kit)

    scene_glb = os.path.join(repo, build["output"]["scene_glb"].replace("/", os.sep))
    collision_glb = os.path.join(repo, build["output"]["collision_glb"].replace("/", os.sep))
    export_glb(scene_objs, scene_glb)
    export_glb(coll_objs, collision_glb)

    lo, hi = enu_bounds(scene_objs)
    print(f"[sanity] scene ENU bounds  x [{lo[0]:.0f}, {hi[0]:.0f}]"
          f"  y [{lo[1]:.0f}, {hi[1]:.0f}]  z [{lo[2]:.0f}, {hi[2]:.0f}]"
          "  — compare with track.json bounds")
    print("[done] exported scene + collision; tuned.json updated. "
          "Rebuilds via make_track.py now reproduce this tuning.")


if __name__ == "__main__":
    # Running as a text block in the GUI (never fires on `import viewer_export`
    # from the headless driver, and headless -P runs are the driver's job).
    if not bpy.app.background:
        gui_export()
