# make_materials_blender.py — 道路づくりに使うマテリアルを一括作成するスクリプト
# Blenderの Scripting タブで実行（1回だけでOK）
# 作られるもの:
#   asphalt      … アスファルト（テクスチャ付き）→ ROAD に指定
#   grass        … 芝（テクスチャ付き）→ 最後に地形へ割り当て
#   marking_white… 白線 → MARKINGS に指定
#   kerb_redwhite… 赤白の縁石 → RACING_KERB に指定
import bpy, os

TEX_DIR = r"D:\00_Dev\ReplayViewer\public\data\textures"


# 重要: Blenderのマテリアルは「ノード内の色」と「Solid表示モード用の色
# (diffuse_color)」が別物です。ノード側だけ設定すると、Solidモードでは
# 全マテリアルが同じ薄灰色に見えてしまいます（テクスチャや市松模様は
# そもそもSolidモードでは描画されない仕様のため、diffuse_colorに
# 近似色を入れて代用します）。下の各関数は両方をきちんと設定します。
# ★色や写真をちゃんと見るには、結局は表示モードを
#   マテリアルプレビュー（Zキー→Material Preview）にしてください。


def tex_material(name, albedo, diffuse_hint, normal=None):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*diffuse_hint, 1)   # Solidモード用の近似色
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.9
    path = os.path.join(TEX_DIR, albedo)
    if os.path.exists(path):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(path)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        print(f"  [警告] テクスチャが見つかりません: {path} （{name} は単色になります）")
        bsdf.inputs["Base Color"].default_value = (*diffuse_hint, 1)
    if normal:
        npath = os.path.join(TEX_DIR, normal)
        if os.path.exists(npath):
            ntex = nt.nodes.new("ShaderNodeTexImage")
            ntex.image = bpy.data.images.load(npath)
            ntex.image.colorspace_settings.name = "Non-Color"
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(ntex.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def solid_material(name, rgb):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*rgb, 1.0)          # Solidモード用の色
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.7
    return mat


def kerb_material():
    name = "kerb_redwhite"
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    # 市松模様(Checker)はSolidモードでは描画されない（マテリアルプレビュー/
    # レンダーのみ）ので、Solid用には赤白を混ぜた近似のピンクを入れておく。
    mat.diffuse_color = (0.83, 0.51, 0.51, 1)
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    checker = nt.nodes.new("ShaderNodeTexChecker")
    checker.inputs["Color1"].default_value = (0.75, 0.10, 0.10, 1)  # 赤
    checker.inputs["Color2"].default_value = (0.92, 0.92, 0.92, 1)  # 白
    checker.inputs["Scale"].default_value = 4.0  # 縞の細かさ（後で好みに調整）
    nt.links.new(checker.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    return mat


tex_material("asphalt", "asphalt_albedo.jpg", (0.09, 0.09, 0.10), "asphalt_normal.jpg")
tex_material("grass", "grass_albedo.jpg", (0.22, 0.35, 0.14))
solid_material("marking_white", (0.92, 0.92, 0.92))
kerb_material()
print("マテリアル作成OK: asphalt / grass / marking_white / kerb_redwhite")
print("※Solidモードは近似色のみ表示。写真やテクスチャ・市松模様は")
print("　マテリアルプレビュー(Zキー→Material Preview)でないと見えません。")
