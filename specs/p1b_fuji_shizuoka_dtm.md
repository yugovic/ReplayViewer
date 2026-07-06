# P1b仕様: 富士標高をVIRTUAL SHIZUOKA点群由来1m DTMに置き換える

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 目的

富士の`terrain.png`(現状: Terrarium z15、451×461px ≒ 実効3.7m/px)を、
VIRTUAL SHIZUOKA(静岡県LiDAR点群オープンデータ、CC BY 4.0)の
**Groundデータ(地表クラスのみ)**から生成した約1m DTMに置き換える。
P1a(Barber、`pipeline/fetch_usgs_dem.py`)と同じ出力契約・検証水準。

## 検証済みデータソース(2026-07-05にFable 5が実データで確認済み)

**使うのは2019年版データセット(富士山南東部・伊豆東部)。**
2021年版(LP22)はカバレッジがFSWの西で途切れており使えないことを
インデックス実データで確認済み。混同しないこと。

- インデックス: `https://gic-shizuoka.s3.ap-northeast-1.amazonaws.com/2020/Vectortile2025/LP00/{z}/{x}/{y}.pbf`
  - Mapbox Vector Tile形式。z12タイル `12/3628/1617` に「Ground」レイヤーがあり、
    各フィーチャが `MESH_NO`(国土基本図図郭)と `URL`(zip直リンク)を持つ
  - フィーチャ座標系: タイルローカル0..4096、**y軸は上向き**
    (mapbox-vector-tileライブラリのデフォルトデコード、つまり
    タイル南端がy=0。Fable 5が両解釈を試しy_upで正しい交差を確認済み)
  - 富士のbboxはz12タイル1枚(3628/1617)に収まるが、スクリプトは
    bboxからタイル番号を計算して必要枚数を取得する汎用実装にすること
- zip URL例(bbox交差は42図郭、`08ME29xx`/`08ME39xx`系):
  `https://virtual-shizuoka.s3.ap-northeast-1.amazonaws.com/2019/LP/Ground/08/ME/39/08ME3936.zip`
- ライセンス: CC BY 4.0(クレジット必須。下記「帰属」参照)

## 成果物

1. 新規スクリプト `pipeline/fetch_shizuoka_dtm.py`
   - `fetch_usgs_dem.py`の流儀・グリッド数学(ノード登録、EPSG:3857、
     cos(lat)スケール、Terrariumエンコード)を踏襲。共通化はせずコピーでよい
     (パイプラインスクリプトは独立性優先の慣習)
   - 処理: satellite_meta.jsonのbbox → インデックスPBF取得・デコード →
     bbox交差図郭のzipを`pipeline/cache/shizuoka_lp/`にダウンロード(キャッシュ、
     サイズ検証、リトライはP1aと同様)→ LAS読み込み → EPSG:3857へ座標変換 →
     約1mノード登録グリッドへ点群ビニング(セル平均)→ 穴埋め →
     Terrarium PNG + meta出力
2. 再生成された `public/data/tracks/fuji/terrain.png` + `terrain_meta.json`
   (旧版は `terrain.z15.bak.png` / `terrain_meta.z15.bak.json` にバックアップ)
3. 再ベイクされた `public/data/tracks/fuji/track.json` 標高プロファイル
   - 富士のフローは `populate_terrain_elevation.py` → `smooth_elevation.py --track`
     (各docstring参照。refine_elevation.pyはBarber専用+US専用APIなので使わない)
   - 実行前に `track.p1b-pre.bak.json` を手動バックアップ(P1aの教訓:
     既存.bakは上書きされない仕様なので、必ず新規名で取る)
4. 検証レポート `specs/reports/p1b/REPORT.md`(日本語)

## 技術ノート(実装前に必ず理解すること)

- **LAS座標系**: VIRTUAL SHIZUOKAは平面直角座標系第8系(JGD2011)=
  **EPSG:6676**、高さは標高(ジオイド基準、正標高)のはず。ただし
  **LASヘッダのCRS情報を必ず確認**し、ヘッダに無ければEPSG:6676と仮定して
  サニティチェックで裏取りする。FSWの路面標高は**約540〜590m**。
  もしGSI標高APIとの差が系統的に約+40m前後あれば楕円体高の混入を疑い、
  **勝手にオフセット補正せず報告して指示を待つ**(Fable 5判断ポイント)
- **Groundデータは地表クラスのみ**なのでクラスフィルタ不要。念のため
  classificationの分布をログに出す
- **点密度と穴**: LP密度は1〜4点/m²程度。1mセルでは無点セルが出る。
  ビニング後にrasterio.fill.fillnodata(P1aと同じ)で穴埋めし、
  **穴埋め前の無点セル率をレポートに記録**。無点率が30%を超えるようなら
  グリッドを1.25m〜1.5mに粗くしてよい(メタのimageWidth/Heightが変わるだけで
  ビューアは解像度非依存)
- 読み込みライブラリ: `laspy`(+ zipに.lazが入っていた場合に備え
  `laspy[lazrs]`)、座標変換は`pyproj`、いずれも`pip install --user`の
  自動導入をP1aと同じ流儀で。**PDAL/GDAL CLIは使わない**
- zipの中身が.lasでなく.txt(x,y,z)の場合もフォーマットを確認して対応する
- ダウンロード総量は数百MB〜2GB程度の見込み。キャッシュ済みならスキップ
- メタに `"source": "VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県)"` を入れる

## 帰属(ライセンス要件)

CC BY 4.0のため出典明記が必要。本タスクでは terrain_meta.json の`source`
フィールドへの記載+REPORT.mdへの出典記録まででよい(アプリUIへの
クレジット表示はP3のUI作業でまとめて対応予定。REPORT.mdにその旨を残すこと)。

## 絶対に守る制約(P1aと同一)

- Terrarium RGBエンコード(`height = R*256 + G + B/256 - 32768`)
- ノード登録: 角ピクセルの**中心**がbbox角に一致(`groundMath.ts:51`の
  `u*(width-1)`前提。`fetch_usgs_dem.py`のdst_transform計算を踏襲)
- 緯度方向はWeb Mercatorリニア(EPSG:3857グリッド)
- bboxはsatellite_meta.jsonの値をそのままメタに書く
- メタの`zoom`フィールドは数値を維持(22でよい)
- NaN/無限値をPNGに入れない(ビューアの四隅チェック-100..1000mは
  富士では**不成立**(標高500m超)⋯ではなく成立する(1000m未満)。念のため
  四隅の値をログに出して確認すること)
- barberのデータ、src/のビューアコードには一切触らない

## 検証(全6項目、P1aと同水準)

1. **独立ソース照合**: 国土地理院標高API
   `https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon={lng}&lat={lat}&outtype=JSON`
   でセンターライン上約20点を取得(DEM5A由来、5mグリッド)し、新terrain.pngの
   bilinearサンプルと比較。|差|の中央値が**1.5m以内**(ソース解像度が違うので
   P1aほどは一致しない。系統オフセットが±3m超なら座標系/ジオイド問題を疑い報告)
2. **旧新比較**: 旧terrain.png(z15)と新版の差分統計(平均/RMS/最大と最大地点)
3. **ユニットテスト**: `npm run test` 全通過
4. **ビルド**: `npm run build` 成功
5. **ビジュアル**: ハーネス(snap.mjs等)を`?track=fuji`で実行、before/after
   スクショを`specs/reports/p1b/`に保存。ポート5199が塞がっていたら
   P1aと同様に5200で(ハーネス一時コピー→実行→削除)
6. **性能**: perf_fps.mjsでbefore/after(±2FPS以内)

## メモ: 既知の周辺事実

- 富士はGPSラインと路面のXY整合が良好(2026-07-04確認済み、鏡映修正済み)。
  本タスクは高さ品質のみを変える。XYずれが新たに見えたら即報告
- track.jsonのフィールド構成はbarberと同じ(points[].lat/lng/alt/y、
  origin、elevationRange)。populate後にelevationRangeとorigin.altが
  更新されることを確認する
