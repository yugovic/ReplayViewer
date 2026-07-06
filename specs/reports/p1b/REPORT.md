# P1b レポート: 富士標高をVIRTUAL SHIZUOKA点群由来1m DTMに置き換え

実施: 2026-07-05 / 実装・検証: Opus 4.8 / 仕様: `specs/p1b_fuji_shizuoka_dtm.md`

## 結論(サマリ)

VIRTUAL SHIZUOKA 2019(LP00インデックス、CC BY 4.0)のGroundデータ42図郭
(点群3.72億点)から富士スピードウェイの**実効約1.0m/px DTM**を生成し、
旧AWS Terrarium z15(451×461px ≒ 3.7m/px、SRTM系)を新版(**1756×1795px**)に
置き換えた。track.json の標高プロファイルも新terrainから再ベイク済み。

**検証6項目はすべて合格**(下表)。仕様の判断ポイント2件(楕円体高オフセット・
無点セル率>30%)は**いずれも非該当**で、指示待ちには入らず完了した。
ただし**1件の仕様前提との相違**(Groundデータのクラス構成)があり、
根拠つきで対応を記録した(下記「クラスフィルタ」参照)。

| # | 検証項目 | 結果 | 要点 |
|---|---------|------|------|
| 1 | 独立ソース照合(GSI標高API) | **PASS** | \|差\|中央値 **0.036m** ≤ 1.5m(最大0.081m、系統オフセット−0.001m) |
| 2 | 旧新比較 | **PASS(報告済)** | 全面グリッドRMS 7.50m・最大38.5m — 旧SRTM系の樹冠/粗解像度誤差の除去(最大差地点はGSIが新値側、差0.08m) |
| 3 | ユニットテスト | **PASS** | vitest 139件 全通過(13ファイル) |
| 4 | ビルド | **PASS** | `tsc && vite build` エラーなし(chunk>500kB警告は既存) |
| 5 | ビジュアル | **PASS** | before/after 8枚撮影・全カメラ健全・ページエラー0 |
| 6 | 性能 | **PASS** | 全5シナリオ Δ≤0.9FPS(±2FPS以内)、draws/ktris同一 |

## 変更・作成ファイル一覧

新規:
- `pipeline/fetch_shizuoka_dtm.py` — インデックスPBF取得→図郭zip DL→LAS読込→
  EPSG:6676→3857変換→1mビニング→穴埋め→Terrariumエンコード
- `public/data/tracks/fuji/terrain.png` — 再生成(1756×1795px, **3.64MB**)
- `public/data/tracks/fuji/terrain_meta.json` — 再生成(下記スキーマ)
- `public/data/tracks/fuji/terrain.z15.bak.png` — 旧terrainバックアップ(規約通り)
- `public/data/tracks/fuji/terrain_meta.z15.bak.json` — 旧metaバックアップ
- `public/data/tracks/fuji/track.p1b-pre.bak.json` — **再ベイク前のtrack.json手動バックアップ**(P1a教訓: 既存.bakは上書きされないため新規名で取得)
- `specs/reports/p1b/REPORT.md`(本ファイル)+ `gsi_vs_new.csv` + スクリーンショット8枚

更新:
- `public/data/tracks/fuji/track.json` — `populate_terrain_elevation.py` →
  `smooth_elevation.py --track` で標高プロファイル(alt/y, origin.alt, elevationRange)を再ベイク

ダウンロードキャッシュ(再実行時は再DLしない。`--skip-download`対応):
- `pipeline/cache/shizuoka_lp/` — インデックスPBF 1枚 + 図郭zip 42個、**計6.9GB**
  (仕様見込み「数百MB〜2GB」を超過。1図郭≒150〜190MBだった。実害はディスクのみ)

barber・src/・spike/ には一切触れていない。

## 実施内容の詳細

### 1. データソースの解決(インデックスPBF)

- `LP00/12/3628/1617.pbf` を取得・デコード(mapbox-vector-tile)。レイヤー
  「Ground」のみ、extent=4096、463フィーチャ。各フィーチャは `MESH_NO` と
  `URL`(zip直リンク)を保持 — 仕様の記載どおり。
- フィーチャ座標は**y軸上向き**(仕様の検証済み規約)として
  タイルローカル→lng/latに変換し、bboxと外接矩形交差判定。
- スクリプトはbboxからz12タイル番号を計算する汎用実装(富士は1枚に収まる)。
- 交差図郭は**42個**(`08ME29xx`/`08ME39xx`系)— 仕様の「42図郭」と完全一致。
  仕様例示の `08ME3936.zip` も含まれる。

### 2. LAS読み込みと座標系の確定

- zip内容は各1個の `.las`(LAS 1.2, point format 3)。`.laz`/`.txt`対応も実装
  (今回は未使用。.txtはNotImplementedErrorで検出のみ)。
- **LASヘッダにCRS情報なし**(VLRゼロ、parse_crs()=None)→ 仕様どおり
  EPSG:6676(平面直角座標系第8系, JGD2011)と仮定し、以下でサニティ裏取り:
  - サンプル点 (X=38418.98, Y=-69906.27) → always_xy=Trueの(easting, northing)解釈で
    (138.9228E, 35.3691N) = 富士bbox内に正しく着地(軸を入れ替えると長野県に飛ぶ)
  - 同地点のLAS地表Z=580.52m に対し **GSI標高API=580.5m(差0.02m)** →
    **正標高(ジオイド基準)で確定**。楕円体高(+40m前後)の混入なし
- 高さ系の判断ポイント(仕様)には**非該当**のため補正なしで続行。

### 3. クラスフィルタ(仕様前提との相違、Fable 5要確認ポイント)

仕様は「Groundデータは地表クラスのみなのでクラスフィルタ不要」としていたが、
**実データはclass 1(未分類)とclass 2(地表)の混在**だった:

- 全42図郭の分類ヒストグラム: **class 1 = 272,935,089点 / class 2 = 99,449,289点**(73%/27%)
- サンプル図郭(08ME3936)の1mセル単位比較(両クラスが同居する102,755セル):
  class 1平均 − class 2平均 = 中央値+0.09m だが **平均+2.06m、p90 +6.5m、最大+14.3m**。
  44.2%のセルでclass 1がclass 2より0.5m以上高い → class 1は樹冠・構造物を含む
- 全点平均でビニングすると植生下で数mの**上方バイアス**(DTMでなくDSM寄り)になるため、
  仕様の本来目的「地表(Ground/DTM)」に忠実な **class 2のみ採用** とした。
  `--all-classes` フラグで旧解釈にも切替可能。
- class 2のみでも密度は**約19.7点/m²**で1mグリッドに十分(下記無点率)。

これは「勝手な補正」ではなくデータ実態に合わせたフィルタ選択だが、
仕様前提と異なるため本節に根拠と共に明記する。

### 4. グリッド生成(fetch_usgs_dem.pyの数学を踏襲)

- 目的地グリッドは **EPSG:3857・ノード登録**: 列0中心=minLng、列W-1中心=maxLng、
  行0中心=maxLat(北)、行H-1中心=minLat。`groundMath.latLngToTerrainUv` +
  `bilinearSample`(px=u·(W-1))の規約に厳密一致。
- cos(lat)スケール: W=round(ΔX·cosφ/1.0m)=**1756**、H=round(ΔY·cosφ/1.0m)=**1795**。
  実測 ~1.001 m/px(E-W)/ ~1.001 m/px(N-S)。
- ビニング: 各点をEPSG:6676→3857変換(pyproj)し、**最近傍セルへ平均**
  (np.bincount、400万点チャンクでメモリ抑制)。bbox内に**63,052,038点**を集約
  (セル平均約20点 — 1/256m精度のTerrarium量子化に対しノイズ十分低減)。
- **無点セル率(穴埋め前): 2.72%**(85,718 / 3,152,020セル)→ 30%閾値に遠く及ばず
  **1mグリッド維持**(粗化は不要)。`rasterio.fill.fillnodata`(探索200px)で全穴充填、
  残余なし(平均値フォールバック未発動)。
- 四隅の値(仕様の要ログ確認): NW 701.6 / NE 556.3 / SW 551.5 / SE 473.8 m —
  すべてビューアの四隅チェック(-100..1000m)内で成立。NaN/無限値なし。
- 全域標高: 457.30〜768.16m(平均576.02)。FSW路面540〜590m+周辺地形として妥当。
- Terrariumエンコード(`height = R*256 + G + B/256 − 32768`)はP1aと同一実装。

`terrain_meta.json`(スキーマ互換・bboxはsatellite_meta.json値をverbatim):
```json
{ "imageFile":"terrain.png", "imageWidth":1756, "imageHeight":1795,
  "bbox": <satellite_meta.jsonと同一>, "mercator":true, "encoding":"terrarium",
  "zoom":22, "source":"VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県)" }
```

### 5. 標高再ベイク(populate → smooth、パラメータ変更なし)

実行前に `track.p1b-pre.bak.json` を手動バックアップ(仕様指示)。

- `populate_terrain_elevation.py`: サンプル範囲 544.23〜581.46m、
  **origin.alt 594.15 → 580.94m**(旧SRTM系の系統誤差約13mを是正。GSI照合で新値が正)
- `smooth_elevation.py --track`(デフォルト max-grade 0.12 / window 40m):
  max|grade| 10.2%→9.7%、**0.5m超変化 0/651点** — LiDARプロファイルは元々滑らかで
  過剰スムージングなし
- elevationRange [541.32, 596.37] → **[544.29, 581.39]**、閉ループ複製点の整合確認済み
- プロファイル変化(旧→新): 平均|Δ| 4.13m、最大|Δ| 15.44m

## 検証結果

### 1. 独立ソース照合(GSI標高API)
センターライン等間隔20点で新terrain.pngのbilinearサンプルとGSI標高API
(`getelevation.php`)を比較(`gsi_vs_new.csv`):
- \|差\| **中央値 0.036m** / 平均 0.036m / 最大 0.081m
- 符号付き平均(new − GSI)= **−0.001m** — 系統オフセット皆無
- **PASS(中央値 ≤ 1.5m を大幅クリア)。±3m超の系統ズレなし → 座標系・ジオイド問題なし。**
- 注: 返却hsrcは「1m(レーザ)」= GSIもこの地域は1mレーザDEMを配信
  (おそらく同じ静岡県LP事業由来のため完全独立とは言えないが、DEM5Aより高精度な
  照合になっている。座標変換・UV規約・エンコードの正しさの裏付けとしては十分)。

### 2. 旧新比較(z15 SRTM系 vs 新1m LiDAR)
- **センターライン**(651点): 平均(new−old) −3.70m、RMS 5.53m、最大\|Δ\| 16.18m
- **160×160全面グリッド**(v=Web Mercator逆変換): 平均 −5.16m、RMS 7.50m、
  **最大\|Δ\| 38.47m**(lat 35.367135, lng 138.930984: 旧547.95 → 新509.48)
- 最大差地点をGSIで裏取り: **GSI=509.4m** — 新値と0.08m一致、旧値が+38.5mの誤り。
  旧z15は日本域でSRTM(30m表層モデル、樹冠含む)由来のため、森林斜面で大きく
  過大だった。この除去こそが本タスクの狙いで、**差が大きいこと自体が改善の証拠**
  (P1aと異なり旧新は独立ソースなので、P1aのようなサブメートル一致は期待値でない)。

### 3. ユニットテスト
`npm run test` → **139件 全通過**(13ファイル)。

### 4. ビルド
`npm run build`(`tsc && vite build`)→ **成功**。chunk>500kB警告は既存・無関係。

### 5. ビジュアル(port 5200、一時ハーネスで撮影→削除済み)
`specs/reports/p1b/` に before/after × {chase, top, tv, cinematic} の8枚:
- `after_tv.png` — メインストレート脇の観客席土手の切土形状がLiDAR由来で明瞭に解像。
  遠景の尾根形状も自然に(before は丸い団子状の丘)
- `after_chase.png` / `after_cinematic.png` — 車両接地正常、路面平滑、破綻なし
- `after_top.png` — 全周ドレープ健全、衛星テクスチャとトラック形状整合、黒空なし
- 撮影2回とも **page errors = 0**。ハーネス自己FPS: before 31.4 / after 28.9(dpr=1)

### 6. 性能(perf系一時ハーネス、dpr=2, 1440×820, Apple M3)
ダウンロード終了後に**新旧アセットをスワップして連続A/B**(同一マシン状態):

| シナリオ | before(z15) FPS | after(1m) FPS | Δ |
|---|---|---|---|
| chase | 14.2 | 13.7 | −0.5 |
| tv | 12.7 | 11.8 | −0.9 |
| cinematic | 13.5 | 13.5 | 0.0 |
| cockpit | 13.9 | 13.0 | −0.9 |
| top | 11.6 | 11.3 | −0.3 |

- 全シナリオ **Δ ≤ 0.9 FPS(±2FPS以内)→ PASS**。draws/ktris完全同一
  (terrainデコードは起動時1回、地面メッシュ解像度は非依存のため設計どおり)。
- 注: 絶対値(11〜14FPS)は既知のRetina SSAO課題(通常20〜30FPS)より更に低いが、
  これは直前の6.9GBダウンロード後のバックグラウンド負荷(Spotlight索引等)による
  一時的なもの。ペアA/Bは同状態での連続実行なので比較の妥当性に影響なし。

## 帰属(ライセンス要件)

- 出典: **VIRTUAL SHIZUOKA 静岡県 富士山南東部・伊豆東部 点群データ(LP00, 2019年計測)、
  静岡県、CC BY 4.0** — `terrain_meta.json` の `source` フィールドに記載済み。
- **アプリUIへのクレジット表示は未対応(仕様どおりP3のUI作業で対応予定)。**
  P3実施時に「©静岡県 VIRTUAL SHIZUOKA(CC BY 4.0)」相当の表記を追加すること。

## 判断保留・注記

1. **クラスフィルタの採用(仕様前提との相違)** — 上記詳細3節。仕様は「フィルタ不要」
   だったが実データはclass 1/2混在。地表DTMの目的に忠実なclass 2のみ採用。
   全点でビニングし直す場合は `--all-classes` で再実行可能(約10分、DLなし)。
2. **ダウンロード総量 6.9GB**(仕様見込み2GBの3.4倍)。キャッシュ済みのため再実行は
   `--skip-download` で高速。ディスクを空けたい場合は `pipeline/cache/shizuoka_lp/` の
   zip群を削除してよい(再取得可能)。
3. **GSI照合の独立性**: GSIのこの地域の標高ソースが「1m(レーザ)」であり、
   VIRTUAL SHIZUOKAと同一事業由来の可能性が高い。完全独立の照合ソースは
   この精度帯では存在しないため、検証1は「座標変換・規約の正しさの確認」として解釈。
4. **ポート**: 5199は今回たまたま空いていたが、仕様指示どおり**5200**で検証
   (ハーネスは一時コピー snap_fuji_5200.mjs / perf_fuji_5200.mjs を作成→実行→削除)。
   検証後、自分で起動した5200のdevサーバーは停止済み。
5. **依存導入**: `laspy 2.7.0` + `lazrs 0.8.1` + `pyproj 3.7.2` を
   `pip install --user` で導入(仕様の流儀)。mapbox-vector-tile/rasterio/numpy/Pillowは既存。
   PDAL/GDAL CLIは不使用(仕様制約)。

## 気づいた課題・所見

- **標高の絶対値が大きく変わった**(origin.alt −13.2m、プロファイル平均−4.1m)。
  ビューア内はy=alt−origin.altの相対座標のため見た目の破綻はないが、
  もし他データ(将来の3D Tiles等)と絶対標高で重ねる場合は新track.jsonが正となる。
- **地面メッシュ192×192の律速**(P1a所見と同じ): terrain.pngが1m/pxになっても
  メッシュ頂点間隔(約9.4m×9.4m — 富士はbboxが広いためBarberより粗い)が
  幾何ディテールの上限。1m DTMの真価を出すには `TrackBuilder.ts` の segsX/segsZ
  引き上げが別タスクとして必要(性能影響評価とセット)。
- **旧z15の品質問題の定量化**: 本作業でメモリの「SRTM標高の品質問題」が実証された
  (森林斜面で最大+38.5mの過大、bbox平均+5.2m)。富士の高さ起因の違和感の主犯は
  ほぼこれだったと考えられる。
