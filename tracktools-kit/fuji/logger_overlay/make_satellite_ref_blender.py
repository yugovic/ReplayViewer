# make_satellite_ref_blender.py - texture the imported terrain with the satellite
# image as a TRACING REFERENCE for width (Esri/Bing = reference only, do NOT ship).
# terrain_lidar.obj already carries satellite UVs, so this just assigns the image.
import bpy, os
SAT = r"D:\00_Dev\ReplayViewer\public\data\tracks\fuji\satellite.jpg"  # or satellite_bing_sr.jpg
ob = bpy.data.objects.get("terrain_lidar")
assert ob, "import terrain_lidar.obj first"
mat = bpy.data.materials.new("satellite_REF"); mat.use_nodes = True
nt = mat.node_tree; bsdf = nt.nodes["Principled BSDF"]
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(SAT)
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
try: bsdf.inputs["Emission Strength"].default_value = 1.0
except Exception: pass
ob.data.materials.clear(); ob.data.materials.append(mat)
print("terrain textured with satellite (reference only). Switch back to grass before export.")
print("NOTE: photo textures NEVER show in Solid shading (Blender limitation) -")
print("      you MUST switch to Material Preview (Z key) to see the satellite image.")
