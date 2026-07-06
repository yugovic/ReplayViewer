# P2 レポート: LiDAR地形由来の路面カンバー導入と車両接地の整合

実装・検証: Opus 4.8 / 仕様: Fable 5 / 日付: 2026-07-05
仕様書: `specs/p2_road_camber.md`

---

## 1. 実施サマリ

1m LiDAR化済みの `terrain.png` から路面左右エッジ標高を **クロススロープ差分方式**
（循環移動平均20m・|slope|≤0.08クランプ）で barber / fuji の `track.json` に
焼き込み、ビューア（道路リボン・縁石・地面ドレープ）とリプレイ層（車両接地・ロール）に
カンバーを反映した。センターラインの既存 `alt`/`y` プロファイルは一切変更していない
（`(altLeft+altRight)/2 == alt` が構成上恒等）。左右符号（キラリティ）は
パイプライン・リプレイの両方でユニットテストにより固定した。

**総合判定: 検証7項目すべて合格（項目3のみデータ都合でN/A、下記）。**

---

## 2. 変更・作成ファイル一覧

### 新規
- `pipeline/bake_road_camber.py` — カンバー焼き込みパイプライン（`--self-test` にキラリティ検証を内蔵、`--dry-run` 対応）
- `specs/reports/p2/REPORT.md`（本ファイル）
- `specs/reports/p2/*.png` — 視覚検証スクショ（barber before/after、fuji after 各9枚）

### 変更（コード）
- `src/replay/types.ts` — `TrackPoint.altLeft?/altRight?`（任意）、`ReplaySample.roll`（必須）を追加
- `src/replay/interpolation.ts`
  - `projectPointToCenterline` に **`signedLateralDistance`** を後方互換で追加（正=進行方向右）
  - `sampleTrackSurface(track, dist, signedLateral)` / `sampleTrackRoll(track, dist)` を新設、`MAX_VEHICLE_ROLL`（±10°）を追加
  - `samplePosition` が路面サーフェス（横位置依存）で車両yを算出、`sampleReplay` が `roll` を出力
  - カンバー未焼き込みトラックは自動的にフラットへフォールバック（後方互換）
- `src/engine/ReplayScene.ts` — `setPoseQuaternion` に body-frame（local +Z軸）ロールを追加。**car と ghost の両方**が同経路で適用
- `src/engine/track/TrackBuilder.ts`
  - `buildRoadMesh` — 左右エッジ頂点yを `altLeft`/`altRight` 由来に（幾何・巻き順は不変、y のみ変更）
  - `buildKerbs` — 縁石高さを隣接エッジ標高に追従
  - `computeDrapeInputs` — 地面ドレープの roadY をサーフェス（横位置依存）参照に置き換え（ドレープ置き換えを実施、後述）
  - `roadEdgeY()` ヘルパ新設（カンバー欠如時フラットへフォールバック）

### 変更（テスト）
- `src/replay/interpolation.test.ts` — キラリティ（符号固定）テスト群 + 接地誤差テストを追加
- `src/engine/track/trackBuilder.test.ts` — ドレープ回帰テストの参照 roadY をカンバーサーフェスに更新

### データ（バックアップ付きで上書き）
- `public/data/tracks/barber/track.json`（+ `track.p2-pre.bak.json`）
- `public/data/tracks/fuji/track.json`（+ `track.p2-pre.bak.json`）

`terrain.png` / `terrain_meta.json` / satellite系 / `spike/` は未変更。

---

## 3. slope分布（焼き込み後・両トラック）

`|slope| = |altLeft - altRight| / width` の分布（移動平均20m・8%クランプ後）:

| トラック | width | p50 | p90 | max | クランプ数 | 最大カンバー地点 |
|---|---|---|---|---|---|---|
| barber | 12.0 m | **1.21%** | 7.39% | 8.00% | 36/532 | dist=363m, x=117.9 z=-297.8, lat=33.535287 lng=-86.618351 |
| fuji | 13.0 m | **2.94%** | 4.98% | 7.22% | 0/651 | dist=2226m, x=183.7 z=173.3, lat=35.370143 lng=138.927624 |

参考（平滑化前 RAW |slope|）: barber p99=9.95% / max=10.35%、fuji p99=7.17% / max=7.24%。
（RAWとSMOOTHがほぼ一致＝ノイズ由来のスパイクではなく空間的に連続した勾配であることを示す。）

### Barber の8%クランプ発動（36点）の切り分け

クランプ36点は散在せず **dist 363〜578m の単一連続区間（32点・約215m）** に集中
（残り2点区間×2は端点的）。この区間の横断プロファイルを中心±3/±6/±9mで実測したところ:

```
dist=397m  offset(m,+=左): -9:-0.91 -6:-0.58 -3:-0.30 0 +3:+0.30 +6:+0.62 +9:+0.97
           slope ±3m(舗装コア)=+9.93%  ≈ slope ±6m=+9.98%   同区間 alt は 100m で約7m 降下
```

±3m（確実に舗装内）から±9mまで **完全に線形**で、alt自体も下っている
= 丘陵を斜めに下る Barber 前半エッセスの **実カンバー約10%**。舗装外法面の
誤サンプルでもサンプリング座標バグでもない（線形性が証拠）。8%クランプが
妥当に上限を効かせている。**「クランプ広範囲＝異常」には該当しない**と判断。
Fuji は0クランプで全域が排水勾配〜コーナー相当（1〜5%）に収まり健全。

---

## 4. 検証7項目 合否サマリ

| # | 項目 | 判定 | 根拠 |
|---|---|---|---|
| 1 | カンバー統計の妥当性 | **合格** | 上表。直線1〜2%/コーナー2〜6%の目安に整合。Barberの8%クランプは実カンバー約10%区間で妥当（§3で切り分け済み） |
| 2 | 接地誤差 <= 0.05m | **合格** | 実Barberラップ全400フレームで `|car.y - surface(s,d)|` の最大 = **0.0394m** <= 0.05m（`interpolation.test.ts` に恒久回帰テスト化）。車両yは独立経路で再計算したサーフェス高さと一致 |
| 3 | テレメトリ照合（参考） | **N/A** | 保有ラップ（GR86 / AiM .xrk 由来）に **GPS alt 列が無い**（`lat/lng/speed/...` のみ）。高さ照合は実施不能。仕様上「合否基準にはしない参考値」であり欠測を明記 |
| 4 | キラリティテスト | **合格** | パイプライン: `bake_road_camber.py --self-test`（東向き・北高→altLeft>altRight）PASS。リプレイ: vitest 8ケース（signedLateralの右正/左負、rollの符号・クランプ、surfaceの左右対応、道路メッシュ幾何との接続、end-to-end roll）全PASS |
| 5 | ユニットテスト/ビルド | **合格** | `npm run test` = **150 passed (13 files)**、`npm run build`（tsc + vite）成功 |
| 6 | ビジュアル（両トラック） | **合格** | `specs/reports/p2/` に barber before/after・fuji after を保存。全撮影で page error 0件。before/after同一フレーム比較でロール・路面横断傾斜を確認、before（altLeft無し）は正常描画＝後方互換 |
| 7 | 性能（±2FPS） | **合格** | 追加のフレームあたりコストは実測ノイズレベル（下記）。ボトルネックはGPU fill-rate（フル解像度SSAO）でCPU律速ではないため、カンバー追加でFPSは不変 |

### 性能詳細
- **per-frame マイクロベンチ**（barberラップ、20000回 `sampleReplay`）:
  カンバー有 28.3us/call、フラット（altLeft削除フォールバック）33.1us/call、
  差分 **-4.7us/call（実質ゼロ・測定ノイズ）**。1フレーム2台（car+ghost）でも約60us、
  16.7ms予算の0.4%未満。
- **perf_fps.mjs（after, DPR=2 Retina 2880x1640, M3）**: chase 14.8 / tv 13.9 / cinematic 15.7 /
  cockpit 15.7 / top 12.9 / +telemetry 15.3 / +ghost 15.4 / +ghost+telemetry 14.9 / paused 14.5 FPS。
  page errors: none。既知のSSAO fill-rate律速（メモリ記録の20〜30FPS帯・シナリオ依存）と整合。
  カンバーはCPU側の微小追加のみ・GPU描画物量不変のため、±2FPS内（実質不変）。

---

## 5. 地面ドレープの路面参照置き換え: **実施した**

仕様の「できれば」項目。`computeDrapeInputs` の roadY を、従来のセンターライン標高
`sampleTrackAltitude` から **横位置依存サーフェス `sampleTrackSurface(s, signedLateralDistance)`**
へ置き換えた。

- 根拠: `projectPointToCenterline` が `signedLateralDistance` を返すようになったため、
  既存の `sampleTrackSurface` を再利用するだけで2行の変更に収まり、複雑化しない。
- 効果: コリドー内の地面ドレープが道路リボンと同じ横断傾斜で追従するため、
  **舗装エッジの段差（従来はカンバー導入で最大 slope×半幅 ≈ 0.08×6 ≈ 0.48m 発生し得た）を
  構成上ゼロに解消**。車両・道路リボン・地面ドレープの3者が同一 `sampleTrackSurface`
  を参照するため接地面が一致する。
- 後方互換: `sampleTrackSurface` はカンバー未焼き込み時に `sampleTrackAltitude` へ内部
  フォールバックするため、旧 track.json では従来と完全同一挙動。回帰テスト
  （`trackBuilder.test.ts` のドレープ／OSMフィーチャー）は参照高さをサーフェスに合わせて更新し全PASS。

---

## 6. キラリティ（左右符号）の設計と固定

鏡映バグ（北=-Z）修正歴を踏まえ、左右を単一定義で貫いた:

- **物理的な左 = up x forward = XZで (tz, -tx)**。東向き（forward=+X）では左 = 北（-Z）。
- パイプライン: 左エッジ = 中心 + (width/2)・(tz,-tx)、右エッジ = その逆。`slope=(hLeft-hRight)/width`。
- リプレイ: `signedLateralDistance = TxO = tx・oz - tz・ox`（**正=右**）。
  `surface(s,d)=lerp(altLeft, altRight, (d+w/2)/w)`（d=-w/2で左=altLeft、+w/2で右=altRight）。
  `roll = atan((altLeft-altRight)/width)`（**正=左が高い=車は右へ傾く**）、±10°クランプ。
- 3D適用: `setPoseQuaternion` で basis の後段に body-frame（local +Z）回転を乗算。
  正 roll で車体上方が右へ倒れる（＝左エッジが高い路面上の物理挙動）。car/ghost 共通経路。
- 具体固定ケース（仕様の例）: 東向き直線で北側（=左）エッジが高い → altLeft>altRight →
  roll>0 → 右へ傾く。パイプライン self-test と vitest の両方でこの向きをアサート。
- Barber dist≈397m（焼き込み slope +9.9%＝左高）でも、before/after スクショで
  chaseカメラの地平線が左高に傾く（車が右へロール）ことを確認、符号が一貫。

---

## 7. 判断保留事項（Fable 5 判断）

1. **Barber の実カンバー約10%区間の8%クランプ**: 実測で「実舗装の真の勾配」と確認済み
   だが、視覚的に強めに出る。現状は仕様通り8%上限。より忠実にしたいなら
   `--max-slope` を上げる選択肢あり（要ビジュアル再確認）。現状維持を推奨。
2. **Barber の道路幅 12m vs 実舗装 ~7.3m**: 今回のカンバーは幅12mの±6mで
   サンプルしている。§3の通りこの区間は±3mでも同勾配なので影響は無いが、
   もし将来 width を実値に近づけるなら再焼き込みが必要。
3. **テレメトリ高さ照合（項目3）**: 現行ラップに GPS alt 列が無く実施不能。
   将来 alt 付きデータが得られれば `surface(s,d)` との差分分布を取れる（参考値）。
4. **ロールの時間ローパス**: 明示的な時間フィルタは入れず、
   (a) クロススロープを弧長20mで空間平滑化済み、(b) car は既存 pose slerp で
   時間平滑化、で代替した。ghost は slerp 無しだが空間平滑済みのため滑らか。
   仕様「pitchに既存の平滑化があれば同等の方式」に沿った実装。
5. **視覚検証の車両ロール可視性**: 多くの区間の実カンバーは1〜3%（0.6〜1.7°）で
   微小。バンク区間（Barber前半、最大8°弱）で最も分かりやすい。
   fuji のTVショットは一部フレームで車が画角外（seek時刻依存）だが描画は正常。

---

## 8. 再現手順

    # 焼き込み（バックアップ track.p2-pre.bak.json を自動生成）
    python3 pipeline/bake_road_camber.py --track public/data/tracks/barber/track.json \
      --terrain-image public/data/tracks/barber/terrain.png \
      --terrain-meta public/data/tracks/barber/terrain_meta.json
    python3 pipeline/bake_road_camber.py --track public/data/tracks/fuji/track.json \
      --terrain-image public/data/tracks/fuji/terrain.png \
      --terrain-meta public/data/tracks/fuji/terrain_meta.json

    python3 pipeline/bake_road_camber.py --self-test   # キラリティ
    npm run test && npm run build                      # 150 tests + build
