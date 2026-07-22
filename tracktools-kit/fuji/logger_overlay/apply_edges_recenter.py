# apply_edges_recenter.py — Blenderで実行 (Scripting > Open > ▶Run)
#
# 衛星画像解析の左右エッジ (track-creator/tracks/fuji/track.json road.edges) を
# フル活用する上位版。apply_width_from_analysis.py との違い:
#   ・カーブの各点を「左右エッジの中点」へ横方向に置き直す (左右非対称を再現)
#   ・幅 = left + right をそのまま radius に (実効幅 = 13 × radius)
#   ・エッジ数値列を平滑化してから適用 (画像解析ノイズによるガタつき除去)
#
# 何度実行してもOK: 位置も幅も毎回 centerline.csv + track.json から計算し直すので
# 実行のたびに同じ結果になります (手での Alt+S 微調整は上書きされる点だけ注意)。
#
# 左右の規約: +normal = ドライバーの右, n=(-tz,tx)
#   (track-creator/src/image-analysis/coordinates.ts / road.ts と同一)
import bpy
import csv
import json
import os

BASE_WIDTH = 13.0        # ROADモディファイアの road width と同じ値にする
EDGE_SMOOTH_PASSES = 4   # エッジ列(2m刻み2280駅)の移動平均回数。0で生データ
RECENTER = True          # False にすると幅だけ適用(旧スクリプト相当)
CLAMP = (0.6, 2.5)       # radius の安全範囲
SHIFT_MEDIAN_WIN = 9     # シフト量のメディアンフィルタ窓(駅数, 奇数) ≒ 18m
SHIFT_SMOOTH_PASSES = 6  # シフト量の移動平均回数
SHIFT_CLAMP = 1.5        # シフト量の上限[m]。本物の非対称は±1m程度、
                         # ピットレーン等への誤食いつき(数m級)はここで弾く

KIT = r"D:\00_Dev\ReplayViewer\tracktools-kit\fuji"
TRACK_JSON = r"D:\00_Dev\ReplayViewer\track-creator\tracks\fuji\track.json"

# --- 入力 --------------------------------------------------------------------
with open(os.path.join(KIT, "centerline.csv"), newline="") as f:
    rows = list(csv.DictReader(f))
xs = [float(r["x_east"]) for r in rows]
zs = [float(r["z_north_neg"]) for r in rows]
ys = [float(r["y_up"]) for r in rows]
dists = [float(r["dist_m"]) for r in rows]
n_pt = len(rows)
total = dists[-1] + (dists[-1] - dists[-2])

with open(TRACK_JSON) as f:
    tj = json.load(f)
left = list(map(float, tj["road"]["edges"]["left"]))
right = list(map(float, tj["road"]["edges"]["right"]))
n_st = len(left)

def smooth_closed(a, passes):
    n = len(a)
    for _ in range(passes):
        a = [(a[(i - 1) % n] + a[i] + a[(i + 1) % n]) / 3.0 for i in range(n)]
    return a

def median_closed(a, win):
    n = len(a); h = win // 2
    return [sorted(a[(i + k) % n] for k in range(-h, h + 1))[h] for i in range(n)]

# 幅プロファイル: 軽い平滑化のみ (幅の実データはなるべく残す)
width_st = smooth_closed([l + r for l, r in zip(left, right)], EDGE_SMOOTH_PASSES)
# シフトプロファイル: 外れ値に厳しく (メディアン → 平滑化 → クランプ)
shift_st = [(r - l) / 2.0 for l, r in zip(left, right)]
shift_st = median_closed(shift_st, SHIFT_MEDIAN_WIN)
shift_st = smooth_closed(shift_st, SHIFT_SMOOTH_PASSES)
shift_st = [max(-SHIFT_CLAMP, min(SHIFT_CLAMP, s)) for s in shift_st]

ob = bpy.data.objects.get("centerline_ROAD_SPLINE")
assert ob and ob.type == 'CURVE', "centerline_ROAD_SPLINE が見つかりません"
pts = ob.data.splines[0].points
assert len(pts) == n_pt, f"点数不一致: curve={len(pts)} csv={n_pt}"

# --- 各点: エッジ中点へ置き直し + 幅→radius ---------------------------------
def station(i_pt):
    return int(round(dists[i_pt] / total * n_st)) % n_st

lo, hi = CLAMP
shift_max = 0.0
clamped = 0
for i in range(n_pt):
    st = station(i)
    # 進行方向接線 (track座標 x,z / 閉ループ)
    tx = xs[(i + 1) % n_pt] - xs[(i - 1) % n_pt]
    tz = zs[(i + 1) % n_pt] - zs[(i - 1) % n_pt]
    tl = (tx * tx + tz * tz) ** 0.5 or 1.0
    tx, tz = tx / tl, tz / tl
    nx, nz = -tz, tx                      # +n = ドライバーの右
    if RECENTER:
        s = shift_st[st]                  # 中点へのシフト量 (+なら右へ)
        shift_max = max(shift_max, abs(s))
        px, pz = xs[i] + nx * s, zs[i] + nz * s
    else:
        px, pz = xs[i], zs[i]
    # ENU(x,z,y_up) -> Blender (x, -z, y)
    pts[i].co = (px, -pz, ys[i], 1.0)
    r = width_st[st] / BASE_WIDTH
    if r < lo or r > hi:
        r = max(lo, min(hi, r)); clamped += 1
    pts[i].radius = r

ob.data.update_tag()
ws = sorted(width_st[station(i)] for i in range(n_pt))
print(f"適用完了: 幅 min {ws[0]:.1f} / 中央値 {ws[n_pt//2]:.1f} / max {ws[-1]:.1f} m"
      f" | 最大シフト {shift_max:.2f} m | clamp {clamped}点 | 平滑化 {EDGE_SMOOTH_PASSES}回")
print("→ ROADの road width は 13 のまま。白線・縁石も同じカーブなので自動追従します。")
