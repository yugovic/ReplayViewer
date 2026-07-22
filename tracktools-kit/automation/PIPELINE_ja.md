# 自作トラック制作パイプライン手順書(fuji 実績ベース)

**ゴール: 人間はエッジの線を直すだけ、あとは全部自動。**

```
[人間] Webエディタでエッジ編集 → 保存
        ↓ track-creator/tracks/<track>/track.json (road.edges)
[自動] make_track.py ワンコマンド
        1.  prep_data.py        LiDAR→terrain_lidar.obj / centerline.json
        1b. edges_to_tuned.py   エッジ→tuned.json (widths + centers)   ← 自動実行
        2.  build_track_blender.py  headless Blender + Track Tools
        3.  verify_track.js     車 vs メッシュ高さの数値検証 (--verify 時)
        ↓ public/data/tracks/<track>/scene.glb + road_ac_local.glb
[確認] ビューアで M (Drive on AC) → 衛星写真と重なることを目視
```

## 1. ワンコマンド

```
python tracktools-kit/automation/make_track.py --track fuji ^
    --tracktools "D:\TrackTools\TrackTools_FULL\TT_NodeGroups.blend" --verify
```

- エディタで保存した `road.edges` があれば **自動で tuned.json に反映**される
  (幅 = 左+右、中心オフセット = (左−右)/2。非対称は捨てない — 捨てると写真と最大5mズレる)
- Blender が見つからない場合は prep だけ実行し、手動コマンドを表示して終了
- `--verify` は dev サーバ(5199)が無ければ自分で起動する

## 2. Webエディタ(track-creator)での編集

```
cd track-creator && npm run dev   # エディタ起動
```

1. 「解析」で下書きエッジを生成(白線優先モード、回廊制約は1行 `開始,終了,左|右,最大m`)
2. 手直し: ドラッグで移動 / 2クリックで区間選択 → 直線化・平滑化・基本幅 / Alt+クリックで点削除
3. **保存** → `track-creator/tracks/fuji/track.json` に road.edges が書かれる

## 3. ファイル命名規約(public/data/tracks/fuji/)

| ファイル | 中身 | 書き込み元 |
|---|---|---|
| `scene.glb` | **自作TT世界(正)** = LiDAR地形 + Track Tools道路 | make_track が上書き |
| `road_ac_local.glb` | 自作の衝突用リボン(車の接地) | make_track が上書き |
| `scene.mod.glb` | AC MOD世界のバックアップ(**ライセンス上、開発参照専用**。商用出力に含めない) | 手動でのみ更新 |
| `road_ac_local.mod.glb` | AC MOD衝突メッシュのバックアップ | 手動でのみ更新 |
| `ac_overlay.json` | ACコースのアウトライン(オレンジ線、比較用) | 手動でのみ更新 |
| `satellite*.jpg` | 衛星写真(Esri z18 / Bing z19。位相相関で位置合わせ済み) | 手動でのみ更新 |

**ルール: `*.mod.glb` と `ac_overlay.json` に make_track は絶対に触らない。**
AC MOD由来の変換(kn5)をやり直す場合も、出力先は必ず `*.mod.glb` にすること。

## 4. 見た目の設定(build_track_blender.py 内)

| 項目 | 設定 | 理由 |
|---|---|---|
| MARKINGS middle line | `none` | サーキットにセンター破線はない |
| MARKINGS border line | `full`(デフォルト) | コース端の白実線 |
| RACING_KERB | 有効 | 縁石(赤白) |
| ROAD Z offset | 0.08 | 地形とのZファイティング回避 |
| TERRAIN_SHAPE Z offset | −0.10 | 道路脇の地形を沈めて隙間を消す |

## 5. GUI微調整(任意)

ビルドごとに `tracktools-kit/fuji/fuji_staging.blend` が保存される。
Blenderで開いて幅・縁石をいじり、Scriptingタブの `EXPORT_FOR_VIEWER.py` を実行すると
エクスポート + tuned.json 書き戻しまで行われ、以後の headless リビルドにも反映される。

## 6. 検証

- 数値: `--verify`(t=8/40/90/150s で車とメッシュの高さ差 ±0.25m)
- 断面: `scratchpad/agent/xsection.py`(エッジ目標値とメッシュ実測の比較、cm精度)
- 目視: ビューアで Top カメラ + 衛星ON → 道路が写真に重なるか

## 7. 既知の残課題

- ビューアの手続き的な回廊/白線シェーダ(TrackBuilder)は対称幅13m前提のまま
  → track.json にエッジを書き出す改修で解消予定
- ランオフエリアはまだ無い(多角形レイヤーとして実装予定)
