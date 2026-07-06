# P3c: 3Dフィーチャのコース回廊クリアランス

## 背景 / 問題

ユーザー報告: コース上(および直近)に樹木が出現している。原因は
`pipeline/build_features3d.py` に**トラック回廊の除外処理が存在しない**こと。
樹木はOSM wood ポリゴン × nDSM>3m で置かれるため、
(a) woodポリゴンがコース際まで及ぶ箇所、(b) 看板/ガントリー/照明柱など
LiDARクラス1点の高さを樹高と誤認した箇所で、コース上に木が立つ。

## 要求

`build_features3d.py` に、track.json のセンターラインからの距離に基づく
除外(クリアランス)を追加し、両トラックの features3d.json を再生成する。

### 仕様

1. track.json の `points[].x/z`(ローカルXZ)を読み、センターライン
   ポリライン(閉ループ)への最短距離 `d(x,z)` を計算するヘルパーを追加。
   - numpyでセグメント一括ベクトル化(点数 ~2600 × 樹木 ~2万 は全組でも可、
     ただしメモリに注意: セグメント毎チャンク処理か、粗い格子で近傍セグメント
    のみ評価する等、実装は任せる。決定的であること)。
2. **樹木**: `d < track.width/2 + TREE_CLEARANCE_M + crownRadius` なら除外。
   `TREE_CLEARANCE_M` 既定 8.0、CLI `--tree-clearance` で調整可。
   (width=12m → センターラインから 6+8+crown ≈ 16〜20m 以内に木は立たない)
3. **建物**: フットプリント頂点のいずれかが `d < track.width/2 + 2.0` なら
   除外(誤検出のピットビル等を消さないよう控えめに)。件数変化をレポート。
4. **バリアは除外しない**(コース際にあるのが正しい)。
5. 除外件数を stdout に `trees removed by clearance: N` 形式で出力。
6. `--self-test` に追加: 合成センターライン(直線)+既知距離の点で
   除外判定の境界を検証。

### 再生成 & 検証

- Barber / 富士 両方の features3d.json を再生成(点群キャッシュは既存を使用、
  再ダウンロード不要のはず。EPT/zipキャッシュは pipeline/cache/)。
- 既存の .bak は上書きしない。features3d.json のバックアップは
  `features3d.p3c-pre.bak.json`。
- `npm run test`(176+)、`npm run build` 通過。
- ビューワで検証(ポート5200、`npm run dev -- --port 5200 --strictPort`):
  トップビュー(カメラ切替)でコース全周を目視できるスクショを両トラック分
  撮り、コース上に木が無いことを確認。
  スクショと結果は `specs/reports/p3c/REPORT.md` に。
- ラップ選択モーダルは `[aria-label="Lap selector"]` 内の `.ls-close` で閉じる
  (Escは効かない)。

### 制約

- Math.random 禁止(決定的)。座標系: 北=−Z、東=+X。
- 長時間の待ち状態でターンを終えない。
- メインdevサーバー(5199)には触らない。
