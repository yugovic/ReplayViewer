# P2仕様: LiDAR地形由来の路面カンバー導入と車両接地の整合

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 背景と再スコープ(重要)

P2の当初構想は「テレメトリのアンサンブルから走行面とカンバーを推定」だったが、
P1a/P1b で両トラックの terrain.png が**1m LiDAR実測**(Barber: USGS 3DEP、
富士: VIRTUAL SHIZUOKA)になったため、路面の真の形状(カンバー含む)は
テレメトリ推定より高精度に**地形ラスタから直接取れる**。よってP2は
LiDAR直接サンプリング方式に再スコープする。テレメトリは検証の照合にのみ使う。

現状の問題(コード確認済み、2026-07-05):
- `TrackBuilder.ts` の `buildRoadMesh`(128行〜): 左右エッジ頂点が
  センターラインと同じy → **道路リボンは横断方向フラット(カンバーゼロ)**
- `interpolation.ts`: 車両yはセンターライン標高のみ(横位置無視)、
  ロールなし(216行の姿勢は heading/pitch のみ)
- `projectPointToCenterline`(51行〜)の `lateralDistance` は
  **符号なし**(左右の区別がない)

## 成果物

### 1. パイプライン: `pipeline/bake_road_camber.py`(新規)

barber・fuji両方の track.json に、各センターライン点の**道路左右エッジ標高**を
焼き込む。

方式(この定式化を守ること):
- センターライン点の局所XZ接線から水平法線を求め、±(track.width/2)の
  エッジ位置を計算 → ローカルXZ→lat/lng逆変換(build系スクリプトの
  等距円筒図法の逆。origin基準)→ terrain.png をバイリニアサンプル
  (`populate_terrain_elevation.py` のサンプリング関数と同じ規約)
- **クロススロープの差分として扱う**: 生の左右標高から
  `slope_i = (hLeft_i − hRight_i) / width` を計算し、slopeを弧長方向に
  移動平均(窓 約20m、周回ラップなので循環)で平滑化+クランプ
  (|slope| ≤ 0.08)してから、
  `altLeft_i = alt_i + slope_i * width/2`、`altRight_i = alt_i − slope_i * width/2`
  として保存する。センターラインの既存 alt/y プロファイル(平滑化済みの正)は
  **一切変更しない**。エッジは常に alt との差分で表現される
- track.json の points[] に `altLeft`/`altRight` を追加(絶対標高m、
  既存 alt と同じ基準)。既存フィールドの変更・削除は禁止
- 実行前バックアップ: `track.p2-pre.bak.json`(barber/fuji各々)
- ログ: slope分布(p50/p90/max)、クランプ発動点数、最大カンバー地点の座標

### 2. ビューア: 道路リボンと地面ドレープ

- `buildRoadMesh`: 左右エッジ頂点のyに `altLeft`/`altRight` 由来の値を使用
  (`(altLeft − origin.alt) * ELEVATION_SCALE` 等、既存の alt→y 変換規約に従う)。
  **フィールドが無い場合は現行どおりフラット**(後方互換必須)
- `buildKerbs`: 縁石の高さを隣接する側のエッジ標高に追従させる
- 地面ドレープ(`corridorBlendHeight` 経由): コリドー内の路面参照高さを、
  センターラインroadYから「グリッド点の横位置での左右エッジ線形補間」に
  置き換えられるなら置き換える。`computeDrapeInputs` の構造上複雑になり
  過ぎる場合は現行のままにして、路面エッジと地面の段差の実測値を
  レポートに記録(Fable 5が次の手を判断する)

### 3. リプレイ層: 車両の接地とロール

- `projectPointToCenterline` を拡張し**符号付き横距離**を返す
  (接線×オフセットの外積符号。既存の呼び出し元は符号なしを期待している
  可能性があるので、後方互換の形で追加すること。例: `signedLateralDistance`
  フィールド追加)
- 車両y: `surface(s, d) = lerp(altLeft(s), altRight(s), (d + width/2) / width)`
  で路面高さを計算(dは符号付き横距離、クランプ ±width/2)。
  altLeft/altRight欠如時は現行のセンターライン標高にフォールバック
- ロール: `roll = atan((altLeft − altRight) / width)` を車両進行方向の
  符号に合わせて適用。クランプ ±10°、時間方向ローパス(pitchに既存の
  平滑化があればそれと同等の方式)。ゴースト車両にも同じ経路で効くことを確認
- **データ層(src/replay/)にDOM依存を持ち込まない**(TerrainSamplerは
  canvas依存なので使用禁止。必ず焼き込み済みaltLeft/altRightを使う)

## 絶対に守る制約

- **キラリティ(左右の符号)**: このプロジェクトは2026-07-05に鏡映バグを
  修正した経緯があり(北=−Z規約)、左右を扱うコードには**符号の向きを固定する
  ユニットテストが必須**。「東向き直線で北側エッジが高い→ロールはどちら向きか」
  のような具体ケースで、パイプライン(altLeft/altRightの左右対応)と
  リプレイ(signedLateralDistance、rollの符号)の両方をテストすること
- track.json スキーマ追加は後方互換(旧track.jsonでも全機能が現行同等に動く)。
  Minimap等、track.jsonの他の消費者(x/z/dist読み)を壊さない
- バックアップ: 上書き前に `track.p2-pre.bak.json`(P1の教訓どおり新規名)
- terrain.png / terrain_meta.json / satellite系 / spike/ には触らない
- vitest追加分を含め `npm run test` 全通過、`npm run build` 成功

## 検証(全7項目)

1. **カンバー統計の妥当性**: 両トラックのslope分布(p50/p90/max)と最大地点。
   目安: 直線部は概ね1〜2%(排水勾配)、コーナーで2〜6%程度。
   富士の最終コーナー〜300R、Barberのターン群で常識的な値になっているか。
   8%クランプの発動が広範囲なら異常(サンプリング座標のバグを疑い報告)
2. **接地誤差**: 実ラップ1本の全フレームで「車両y −
   surface(s,d)」の統計を取り、|誤差|最大が0.05m以内(数値一致の確認)
3. **テレメトリ照合(参考値)**: ラップのGPS alt(あれば)と surface(s,d) の
   差の分布をレポート(GPS高さは数mノイズがあるので合否基準にはしない)
4. **キラリティテスト**: 上記の符号固定ユニットテスト(パイプライン側は
   Pythonの簡易テストかassert、TS側はvitest)
5. **ユニットテスト/ビルド**: vitest全通過(新規テスト含む)、build成功
6. **ビジュアル**: 両トラックでbefore/after(特にバンク・縁石乗り・
   コーナリング中の車両ロール)。`specs/reports/p2/` にスクショ保存。
   ポート5199占有時は5200で
7. **性能**: perf_fps.mjs before/after(±2FPS以内。追加計算は
   フレームあたり微小なはず)

## レポート

`specs/reports/p2/REPORT.md`(日本語): 実施内容、検証7項目、
slope分布、ドレープ置き換えの実施可否とその根拠、判断保留事項。
