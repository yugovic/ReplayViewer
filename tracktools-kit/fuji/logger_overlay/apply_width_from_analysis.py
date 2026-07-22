# apply_width_from_analysis.py — Blenderで実行 (Scripting > Open > ▶Run)
#
# 衛星画像解析の実測幅 (track-creator/tracks/fuji/track.json の road.edges) を
# centerline_ROAD_SPLINE の「点ごとの radius」に書き込みます。
# ROAD の実効幅 = road width(基準値) × 点のradius なので、
# ROADモディファイアの road width は基準値 13 のままにしてください。
#
# 何度実行してもOK(毎回上書き)。手で微調整した radius は上書きされるので、
# 手調整は このスクリプトを実行した後に行うこと。
import bpy
import csv
import json
import os

BASE_WIDTH = 13.0          # ROADモディファイアの road width と同じ値にする
SMOOTH_PASSES = 1          # 幅のガタつきを均す回数 (0で無効)
CLAMP = (0.6, 2.5)         # radius の安全範囲 (7.8m〜32.5m 相当)

KIT = r"D:\00_Dev\ReplayViewer\tracktools-kit\fuji"
TRACK_JSON = r"D:\00_Dev\ReplayViewer\track-creator\tracks\fuji\track.json"

# --- 入力読み込み -----------------------------------------------------------
with open(os.path.join(KIT, "centerline.csv"), newline="") as f:
    rows = list(csv.DictReader(f))
dists = [float(r["dist_m"]) for r in rows]

with open(TRACK_JSON) as f:
    tj = json.load(f)
left = tj["road"]["edges"]["left"]
right = tj["road"]["edges"]["right"]
n_st = len(left)
total = dists[-1] + (dists[-1] - dists[-2])   # 閉ループの全長 (~4563m)
print(f"centerline.csv: {len(rows)}点 / edges: {n_st}駅 / 全長 {total:.0f}m")

ob = bpy.data.objects.get("centerline_ROAD_SPLINE")
assert ob and ob.type == 'CURVE', "centerline_ROAD_SPLINE が見つかりません"
pts = ob.data.splines[0].points
assert len(pts) == len(rows), f"点数不一致: curve={len(pts)} csv={len(rows)}"

# --- 幅 → radius ------------------------------------------------------------
def width_at(dist_m):
    i = int(round(dist_m / total * n_st)) % n_st
    return left[i] + right[i]

radii = [width_at(d) / BASE_WIDTH for d in dists]

for _ in range(SMOOTH_PASSES):                 # 3点移動平均 (閉ループ)
    n = len(radii)
    radii = [(radii[(i - 1) % n] + radii[i] + radii[(i + 1) % n]) / 3.0
             for i in range(n)]

lo, hi = CLAMP
clamped = 0
for i, p in enumerate(pts):
    r = radii[i]
    if r < lo or r > hi:
        r = max(lo, min(hi, r)); clamped += 1
    p.radius = r

ob.data.update_tag()
ws = [r * BASE_WIDTH for r in radii]
print(f"適用完了: 幅 min {min(ws):.1f}m / 中央値 {sorted(ws)[len(ws)//2]:.1f}m / max {max(ws):.1f}m"
      f" (clamp {clamped}点)")
print("→ ROADモディファイアの road width が 13 のままか確認してください。")
