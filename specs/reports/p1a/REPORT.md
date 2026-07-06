# P1a レポート: Barber標高をUSGS 3DEP 1m DEMに置き換え

実施: 2026-07-05 / 実装・検証: Opus 4.8 / 仕様: `specs/p1a_barber_1m_dem.md`

## 結論(サマリ)

USGS 3DEP 1m LiDAR DEM(2023計測, `AL_11County_B23`)から Barber の `terrain.png`
を再生成し、旧AWS Terrarium z15(304×272px, 実効約4m/px)を新版(1211×1083px,
実効約1.0m/px)に置き換えた。track.json の標高プロファイルも新terrainから再ベイク済み。

**検証6項目はすべて合格**(下表)。過剰スムージングは検出されず(最大 0.173m << 1m)、
スムージング・パラメータは**変更していない**(下記「スムージングの扱い」参照)。

| # | 検証項目 | 結果 | 要点 |
|---|---------|------|------|
| 1 | 独立ソース照合(EPQS) | **PASS** | \|差\|中央値 **0.024m** ≤ 1.5m(最大0.212m) |
| 2 | 旧新比較 | **PASS(報告済)** | センターラインRMS 0.175m、全面グリッドRMS 0.861m、最大差11.65m(丘/ランオフ) |
| 3 | ユニットテスト | **PASS** | vitest 139件 全通過(13ファイル) |
| 4 | ビルド | **PASS** | `tsc && vite build` エラーなし |
| 5 | ビジュアル | **PASS** | before/after撮影・全周描画健全・ページエラー0 |
| 6 | 性能 | **PASS** | chase 30.2→30.6 FPS(全シナリオ±1FPS以内、draws/tris同一) |

## 変更・作成ファイル一覧

新規:
- `pipeline/fetch_usgs_dem.py` — DEM取得→モザイク→EPSG:3857再投影→bboxクロップ→Terrariumエンコード
- `public/data/tracks/barber/terrain.png` — 再生成(1211×1083px, 1.38MB)
- `public/data/tracks/barber/terrain_meta.json` — 再生成(下記スキーマ)
- `public/data/tracks/barber/terrain.z15.bak.png` — 旧terrainのバックアップ(規約通り)
- `public/data/tracks/barber/terrain_meta.z15.bak.json` — 旧metaのバックアップ
- `public/data/tracks/barber/track.p1a-pre.bak.json` — **refine実行前のtrack.json手動バックアップ**(理由は「判断保留・注記」参照)
- `specs/reports/p1a/REPORT.md`(本ファイル) + `smoothing_profile.csv`, `epqs_vs_new.csv` + スクリーンショット9枚

更新:
- `public/data/tracks/barber/track.json` — `refine_elevation.py`で標高プロファイル(alt/y, origin.alt, elevationRange)を再ベイク

ダウンロードキャッシュ(再実行時に再DLしない):
- `pipeline/cache/usgs_dem/USGS_1M_16_x53y371_AL_11County_B23.tif`(228.7MB)
- `pipeline/cache/usgs_dem/USGS_1M_16_x53y372_AL_11County_B23.tif`(362.8MB)

fuji・他トラック・src/ のビューアコードには一切触れていない。

## 実施内容の詳細

### 1. DEM取得と再投影(`pipeline/fetch_usgs_dem.py`)

- 仕様記載の2枚のGeoTIFFをS3から取得(HEADでContent-Length検証、リトライ4回、`.part`経由でアトミック確定、既存かつサイズ一致ならスキップ)。
- ソースCRS = **EPSG:26916 (NAD83 / UTM 16N)**、nodata = -999999.0。
- 巨大タイル全体(各約10k×10k)を読まず、bbox四隅をソースCRSに変換し80mマージンを付けた**ウィンドウ**のみをモザイク(実読み込み 1375×1243px)。メモリ・時間を抑制。
- 目的地グリッドを **EPSG:3857** で構築。**ノード登録**:列0中心=minLng、列W-1中心=maxLng、行0中心=maxLat(北)、行H-1中心=minLat。これは `groundMath.latLngToTerrainUv`(u=経度リニア、v=Web Mercator y、v=0が北端)+ `bilinearSample`(px=u·(W-1))が角ピクセル**中心**をbbox角とみなす規約に厳密一致させたもの。
- 解像度: EPSG:3857のメートルは緯度で1/cos(lat)≈1.20倍に膨張するため、地上約1m/pxとなるよう W=round(ΔX·cosφ)=1211、H=round(ΔY·cosφ)=1083。実測 ~1.000 m/px(E-W)、~1.001 m/px(N-S)。
- リサンプリング: **bilinear**(rasterio.warp.reproject)。
- void/NoData: **0画素**(Barber周辺に水面ほぼ無し、想定通り)。NaN発生時は`fillnodata`(近傍補間, 探索距離200px)→残余は平均で埋めるフォールバックを実装済み。
- Terrariumエンコード: `t = round((h+32768)·256)`, R=t>>16, G=t>>8&0xFF, B=t&0xFF(1/256m精度)。ビューアの`decodeTerrariumHeight`と完全互換。
- 出力: `terrain.png` 1211×1083, **1.38MB**(10MB制約を大きく下回る)。全bbox標高 169.35..228.15m(平均196.59)。

`terrain_meta.json`(スキーマ互換確認済み):
```json
{ "imageFile":"terrain.png", "imageWidth":1211, "imageHeight":1083,
  "bbox": <satellite_meta.jsonと同一>, "mercator":true, "encoding":"terrarium",
  "zoom":22, "source":"USGS 3DEP 1m AL_11County_B23" }
```
`zoom`は`TerrainMeta`型が要求するため実効解像度に相当する22を投入(TS側は未使用)。`source`はTSが無視する追加フィールド。

### 2. 標高再ベイク(`refine_elevation.py`、パラメータ変更なし)

`python3 pipeline/refine_elevation.py`(exit 0)。

- Before(旧z15由来): min 181.80 / max 203.46m, max\|grade\| 12.1%
- After(新1m由来): min 181.65 / max 203.38m, max\|grade\| 12.1%
- 旧プロファイルとの差: 平均\|Δ\| **0.162m**、最大\|Δ\| **0.280m**
- origin.alt: 195.77 → 195.60m、elevationRange: [181.65, 203.38]
- 勾配クランプは40反復で収束(上限200)。elevation span 21.73m は許容[10,45]内。

## スムージングの扱い(仕様の重要判断ポイント)

現行パラメータ(`MAX_GRADE=0.12`, `MOVING_AVERAGE_WINDOW=5`)のまま、raw(bilinearサンプル,
スムージング前)と smoothed/clamped(処理後)をセンターライン532点で比較した
(`smoothing_profile.csv`)。

| 比較 | RMS | 最大 |
|------|-----|------|
| \|raw − 移動平均\| | 0.042m | 0.150m |
| \|移動平均 − クランプ\| | 0.005m | 0.060m |
| **\|raw − 最終\|** | **0.043m** | **0.173m** |

- \|raw − 最終\| が **1mを超える点は 0/532(0.0%)**。最大でも 0.173m(dist=655.6m: raw 185.75 → 最終 185.92)。
- span は raw 21.84m → 最終 21.73m とほぼ不変。勾配クランプの補正はごく僅か。

**判断: パラメータは変更しない(変更不要)。** 1m LiDARの実在ディテールを潰す
「過剰スムージング(>1mが広範囲)」は発生していない。理由はセンターラインが
サーキット路面で沿道方向の勾配が緩く(12%上限に対し十分小)、7m間隔の点自体が
既に低域通過的で、window=5の移動平均でも削れる量が最大0.173mに留まるため。
仕様の「>1mが広範囲なら変更せず報告して指示待ち」の条件には**該当しない**ため、
指示待ちには入らず通常フローで完了した(証拠は上表・CSV)。

## 検証結果

### 1. 独立ソース照合(EPQS = 独立した3DEP由来)
`pipeline/cache/epqs_barber.json`(532点, キーは現行センターラインlat/lngと**全点一致**)を
独立ソースとして、新terrain.pngのbilinearサンプルと比較(`epqs_vs_new.csv`)。
- \|差\| **中央値 0.024m** / 平均 0.038m / 最大 0.212m(20点等間隔サンプルでも中央値 0.035m)
- 符号付き平均(new − EPQS)= +0.008m(系統ズレ実質なし)
- **PASS(中央値 ≤ 1.5m を大幅クリア)。** EPQSも3DEP由来のためサブメートル一致が期待され、
  実際サブデシメートルで一致 → 投影(EPSG:3857)・UV変換・ノード登録が正しいことを裏付け。

### 2. 旧新比較(z15 vs 1m、同一グリッドでサンプル)
- **センターライン**(533点): 平均 −0.155m, RMS 0.175m, 最大\|Δ\| 0.401m
- **120×108 全面グリッド**(bbox内, v=Web Mercator逆変換): 平均 −0.123m, RMS 0.861m, **最大\|Δ\| 11.65m**
- 最大差の位置: lat 33.528749, lng −86.613107(旧 184.26m → 新 195.91m)。丘/ランオフ斜面で、
  粗い4mタイルが均していた斜面を1m DEMが鋭く解像した箇所 = 本改善の狙い通り。
- センターライン上は旧新とも3DEP系のため差は小(~0.2m)。off-track の起伏精度が主な向上。

### 3. ユニットテスト
`npm run test` → **139件 全通過**(13ファイル, groundMath/trackBuilder/featureBuilder等含む)。

### 4. ビルド
`npm run build`(`tsc && vite build`)→ **成功**、型エラー・ビルドエラーなし
(chunk>500kBの警告は既存・本件と無関係)。

### 5. ビジュアル
before/afterスクリーンショットを `specs/reports/p1a/` に保存:
- `before_*` / `after_*`(chase系, z15 vs 1m)— 遠景の丘・左右ランオフの起伏/植生ディテールが新版で明瞭に精細化。
- `after_aligncheck_top.png` — 全周トップビュー。衛星テクスチャが新1m地形に正しくドレープ、トラック形状・レーシングライン・起伏すべて健全。黒空や破綻なし。
- `after_finalcheck_tv.png` / `after_finalcheck_cinematic.png` — ゴースト付き描画も車が路面に正しく接地、地形起伏自然。
- `snap`/`align_check`/`final_check` いずれも **page errors = 0**、final_checkの自己FPS計測 60.2。

### 6. 性能(`perf_fps.mjs`, dpr=2, 1440×820)
GPU: Apple M3 (ANGLE Metal)。terrainデコードは起動時1回のみ・地面メッシュは解像度に依らず192×192のため、ランタイム影響ゼロを実測で確認。

| シナリオ | before(z15) FPS | after(1m) FPS | Δ |
|---|---|---|---|
| chase (default) | 30.2 | 30.6 | +0.4 |
| tv | 26.3 | 26.8 | +0.5 |
| cinematic | 30.3 | 30.7 | +0.4 |
| cockpit | 32.0 | 32.3 | +0.3 |
| top | 23.0 | 23.1 | +0.1 |
| chase+telemetry | 29.5 | 30.5 | +1.0 |
| chase+ghost | 30.7 | 30.7 | 0.0 |
| chase+ghost+telemetry | 28.3 | 28.7 | +0.4 |
| paused | 29.0 | 28.5 | −0.5 |

全シナリオで差は **±1 FPS 以内(実行間ノイズ)**、draws/ktris は完全同一。**低下2FPS以上なし → PASS。**
(FPSの絶対値 23〜32 はRetina全解像度SSAO由来の既知課題で本件とは無関係。)

## 判断保留・注記(仕様の制約・進め方に関する明示事項)

1. **開発サーバーのポート(5199固定 → 実際は5200を使用)**
   ポート5199は**別プロジェクト**のdev server(title "G3D Spike - Google Photorealistic 3D Tiles")が
   既に占有していた。他プロジェクトのサーバーを勝手にkillするのは環境への副作用が大きいため、
   replay-viewer-v2 を **5200** で起動し、ハーネス(snap/align_check/final_check/perf_fps)は
   5199→5200 に置換した一時コピーをプロジェクトルートに作成→実行→**削除**した。
   ビューアのコードやハーネス原本は未変更。検証の本質(本terrain変更の可視/性能確認)には影響しない。
   検証後、私が起動した5200サーバーのみ停止(5199の別プロジェクトは非干渉)。

2. **track.jsonのバックアップ(refine_elevation.pyの挙動確認結果)**
   `refine_elevation.py`は`track.json.bak`が**未存在の時のみ**バックアップを作る仕様。
   既存の`track.json.bak`(Jul 2, **鏡映修正前**)は上書きされず、現行(鏡映修正後)track.jsonの
   保全にならない。そこでrefine実行前に手動で `track.p1a-pre.bak.json` を作成した(規約の
   「refineが自前バックアップを取るならそれに従う(要確認)」に対する確認・対応)。
   なお本タスクはgit管理下でない(リポジトリ未初期化)ため、手動バックアップが唯一のロールバック手段。

3. **依存関係のインストール**
   `rasterio 1.5.0`(+affine/click/cligj)を仕様の「無ければpip install --user」方針で
   ユーザーサイトに導入(arm64 wheel、GDAL CLI/brew不要)。numpy/Pillowは既存。
   `fetch_usgs_dem.py`もPillow自動導入と同じ流儀で未導入時に`pip install --user`を試みる。

いずれも「絶対に守る制約」節(エンコード/UV/bbox/メタ/void/バックアップ/fuji非干渉/依存/解像度)の
違反ではなく、すべて満たしている。

## 気づいた課題・所見

- **地面メッシュ192×192の限界(スコープ外, 仕様依頼の所見)**: 現行の地面メッシュは
  bbox全域を192×192分割 = 頂点間隔 約6.3m(E-W)/約5.6m(N-S)。terrain.pngが1m/pxになっても
  **メッシュ頂点密度(約6m)が幾何ディテールの律速**となり、1mの真価はまだメッシュに完全には
  反映されない。192のままで体感できる改善は主に (a) 各サンプル点の**標高精度向上**
  (4mタイルの平滑化・SRTMノイズの除去)、(b) 丘/ランオフ斜面の**局所的な鋭さ**
  (検証2の最大11.65m差の箇所)であり、"路面のうねり" レベルの微細ディテールを引き出すには
  `TrackBuilder.ts` の `segsX/segsZ` 引き上げが必要(性能評価とセットで別タスク)。目安として、
  1m DEMを完全に活かすには 192 → 約1000以上(頂点数≈27倍)が理想だが、頂点数・描画負荷への
  影響評価が前提。中間として 384〜512 で off-track 起伏の体感が段階的に向上する見込み。
- **標高データの一貫性**: 旧z15 Terrarium・EPQS・新1m DEM はいずれも米国内で3DEP/NED系に
  由来するため鉛直基準(NAVD88相当)が揃っており、置き換えによる系統オフセットは実質ゼロ
  (EPQS符号付き平均 +0.008m)。datum不整合の心配は不要。
- **再現性**: `fetch_usgs_dem.py` はキャッシュ済みGeoTIFFがあれば `--skip-download` で再投影のみ
  再実行可能。バックアップは既存を保護(上書きしない)設計。
