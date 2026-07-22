# restore_centerline_original.py — Blenderで実行 (Scripting > Open > ▶Run)
# centerline_ROAD_SPLINE を完全に元の状態へ戻します:
#   ・点の位置 = centerline.csv の測量由来の座標 (リセンターを取り消し)
#   ・radius   = 全点 1.0 (幅は一律 road width の 13m に戻る)
import bpy
import csv
import os

KIT = r"D:\00_Dev\ReplayViewer\tracktools-kit\fuji"

with open(os.path.join(KIT, "centerline.csv"), newline="") as f:
    rows = list(csv.DictReader(f))

ob = bpy.data.objects.get("centerline_ROAD_SPLINE")
assert ob and ob.type == 'CURVE', "centerline_ROAD_SPLINE が見つかりません"
pts = ob.data.splines[0].points
assert len(pts) == len(rows), f"点数不一致: curve={len(pts)} csv={len(rows)}"

for p, r in zip(pts, rows):
    x = float(r["x_east"]); z = float(r["z_north_neg"]); y = float(r["y_up"])
    p.co = (x, -z, y, 1.0)     # ENU(x,z,y_up) -> Blender (x, -z, y)
    p.radius = 1.0

ob.data.update_tag()
print(f"復元完了: {len(pts)}点の位置と radius(=1.0) を元に戻しました。幅は一律13mです。")
