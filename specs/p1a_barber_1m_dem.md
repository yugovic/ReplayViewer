# P1a仕様: Barber標高をUSGS 3DEP 1m DEMに置き換える

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 目的

Barberの`terrain.png`(現状: AWS Terrarium z15、304×272px ≒ 実効4m/px)を、
USGS 3DEP 1m DEM(2023年LiDAR計測)由来の高解像度版(約1213×1078px)に置き換える。
路面・ランオフ・丘の起伏の表現精度を約4倍に引き上げ、既知の「SRTM品質問題」を解消する。

## 検証済みデータソース(2026-07-05にTNM APIで確認済み)

最新プロジェクト `AL_11County_B23`(2023年計測、2026-06-09公開)。UTM zone 16。

- https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/AL_11County_B23/TIFF/USGS_1M_16_x53y371_AL_11County_B23.tif (228.7 MB)
- https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/AL_11County_B23/TIFF/USGS_1M_16_x53y372_AL_11County_B23.tif (362.8 MB)

サーキットのbbox(`public/data/tracks/barber/satellite_meta.json`のbbox)は
この2タイル(南北隣接)にまたがる。ダウンロードは`pipeline/cache/`にキャッシュし、
既にあれば再ダウンロードしない(合計約590MB)。

フォールバック(上記が取得不能な場合のみ): `AL_25Co_B2_2017` / `AL_JeffersonCo_2013` の同名タイル。
TNM APIで再照会する場合:
`https://tnmaccess.nationalmap.gov/api/v1/products?datasets=Digital Elevation Model (DEM) 1 meter&bbox=-86.63,33.52,-86.60,33.54&outputFormat=JSON`

## 成果物

1. 新規スクリプト `pipeline/fetch_usgs_dem.py`
   - 既存スクリプトの流儀に従う(argparse、docstring、デフォルトパス定数、リトライ、
     `pipeline/cache/`キャッシュ。`fetch_terrain_tiles.py`と`fetch_bing_tiles.py`を参照)
   - 処理: GeoTIFF 2枚をダウンロード→モザイク→EPSG:3857(Web Mercator)へ再投影→
     satellite_meta.jsonのbboxで正確にクロップ→Terrarium RGBエンコードでterrain.pngを出力→
     terrain_meta.jsonを出力
2. 再生成された `public/data/tracks/barber/terrain.png` + `terrain_meta.json`
3. 新terrainで再ベイクされた `public/data/tracks/barber/track.json` の標高プロファイル
   (`refine_elevation.py`を実行。下記「スムージングの扱い」参照)
4. 検証レポート(下記)

## 絶対に守る制約

- **エンコード互換**: 出力terrain.pngはTerrarium RGBエンコード
  (`height = R*256 + G + B/256 - 32768`、1/256m精度)。ビューアの
  `src/engine/track/TerrainSampler.ts` と `groundMath.ts` の
  `decodeTerrariumHeight` がそのまま読めること。
- **UV規約互換**: `groundMath.ts` の `latLngToTerrainUv` は経度方向リニア・
  緯度方向Web Mercatorのピクセルグリッドを前提とする。したがって再投影先は
  **EPSG:3857**でbbox四隅にピッタリ合わせること(行0=北端=maxLat)。
  リサンプリングはbilinearまたはcubic。
- **bbox**: satellite_meta.jsonのbboxを**そのまま**terrain_meta.jsonに書く
  (既存の`fetch_terrain_tiles.py`と同じ方針)。
- **メタスキーマ互換**: TerrainSampler.tsの`TerrainMeta`インターフェースは
  `zoom: number`を要求する(未使用だが型は残っている)。数値を入れておく
  (例: 実効解像度に相当する`22`など任意の数値でよい)。追加フィールド
  `"source": "USGS 3DEP 1m AL_11County_B23"` を入れること(TSは無視する)。
- **void/NoData処理**: DEMのNoDataピクセルは近傍値で埋める(NaNをエンコードすると
  ビューアの四隅サニティチェック(-100〜1000m)で全体がフォールバックする)。
  Barber周辺は水面がほぼ無いのでvoidは稀のはず。埋めた画素数をログに出す。
- **バックアップ規約**: 置き換える前に既存の`terrain.png`/`terrain_meta.json`を
  `terrain.z15.bak.png`/`terrain_meta.z15.bak.json`として保存。track.jsonは
  refine_elevation.pyが自前のバックアップを取るならそれに従う(要確認)。
- **fujiには一切触らない**。barberディレクトリと新規スクリプトのみ。
- 依存ライブラリ: `rasterio`+`numpy`を推奨(macOS arm64はwheelあり)。
  既存スクリプトのPillow自動インストールと同じ流儀で、無ければ
  `pip install --user`を試みる。GDAL CLIへの依存(brew)は避ける。
- 出力解像度: bboxをEPSG:3857で1m/pxにすると約1213×1078。PNGサイズは
  数MB程度になる見込み。10MBを超えるようなら報告(zlib圧縮レベル調整可)。

## スムージングの扱い(重要・判断ポイント)

`refine_elevation.py`のMAX_GRADE=0.12やスムージングはノイズだらけのSRTM前提の
チューニング。1m LiDARは本物のディテールを持つため、**過剰スムージングで実在の
起伏を潰す恐れがある**。手順:

1. まず現行パラメータのままrefineを実行し、raw(スムージング前)と
   smoothed(後)の標高プロファイルを距離軸でダンプ(CSVか簡易プロット)
2. 両者の差のRMS/最大値を報告。smoothingが1mを超えて実在の形状を
   削っている区間が広い場合は、**変更せずに報告して指示を待つ**
   (Fable 5がパラメータ再調整を判断する)

## 検証(全6項目を実施し、結果をレポートに含める)

1. **独立ソース照合**: USGS EPQS API(`refine_elevation.py`内に既存の利用コード
   あり)でセンターライン上の20点程度の標高を取得し、新terrain.pngのbilinear
   サンプル値と比較。|差|の中央値が1.5m以内であること(EPQS自体も3DEP由来なので
   本来はサブメートルで一致するはず。大きくズレたら投影/UV変換のバグを疑う)
2. **旧新比較**: 旧terrain.png(z15)と新terrain.pngを同一グリッドでサンプルし、
   差分の統計(平均/RMS/最大)と、差が最大の場所の座標を報告
3. **ユニットテスト**: `npm run test`(vitest)が全部通ること
4. **ビルド**: `npm run build`が通ること
5. **ビジュアル**: 既存ハーネス`snap.mjs`・`align_check.mjs`・`final_check.mjs`を
   実行(使い方は各ファイル冒頭を読む)。before/afterスクリーンショットを
   `specs/reports/p1a/`に保存
6. **性能**: `perf_fps.mjs`でbefore/after計測。FPS低下が2以上あれば原因を調査
   (terrain.pngのデコードは起動時1回なのでランタイム影響はないはず)

## スコープ外(やらない)

- ビューアの地面メッシュ分割数(`TrackBuilder.ts`の`segsX/segsZ = 192`)の引き上げ。
  これは1m DEMの恩恵を最大化するが、性能への影響評価と合わせて別タスクで行う。
  レポートに「192のままでどの程度改善が体感できるか」の所見だけ書くこと。
- 車両y座標のdist参照ドリフト対策(P2で対応)
- 富士の標高置き換え(P1bで対応)

## レポート

`specs/reports/p1a/REPORT.md`に: 実施内容、検証結果(上記1〜6)、
判断を保留した点、気づいた課題。日本語で。
