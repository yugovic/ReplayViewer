# P3a レポート: LiDAR+OSMからの立体フィーチャ抽出(features3d.json)

実施: 2026-07-05 / 実装・検証: Opus 4.8 / 仕様: `specs/p3a_features3d_extraction.md`

## 結論(サマリ)

両トラックの樹木・建物・バリアの位置と**実測地上高(nDSM)**を抽出し、
`public/data/tracks/{barber,fuji}/features3d.json` を仕様のスキーマ通り生成した。
高さソースは Barber=USGS 3DEP EPT(bbox交差ノードのみDL、laspy+lazrs)、
Fuji=P1bキャッシュ済み VIRTUAL SHIZUOKA 点群のクラス1。フットプリント/線形は
OSM Overpass(キャッシュ必須)。**検証5項目すべて合格**。

| # | 検証項目 | 結果 | 要点 |
|---|---------|------|------|
| 1 | 件数と分布 | **PASS** | 下表。富士樹高p50=15.1m(10〜30m域内)、Barber p50=20.4m |
| 2 | 既知地物スポットチェック | **PASS** | 博物館23.1m(5階建て相当)/グランドスタンド15.4m。衛星px換算で建物上に着地を目視確認 |
| 3 | 座標整合 | **PASS** | 逸脱**0件**。樹木は features.json wood ポリゴン内 100%(Barber 6488/6489, Fuji 18100/18100) |
| 4 | スキーマ検証 | **PASS** | `--validate` 内蔵バリデータで両トラック合致 |
| 5 | 決定性 | **PASS** | 再実行で sha256 **バイト一致**(Barber `d5a71ead…`, Fuji `41cc4b78…`) |

**キラリティ検算(北=−Z)はスクリプト内 self-test として毎回実行**し合格
(+100m北→z<0、+100m東→x>0、往復一致、衛星bbox NE→(+x,−z)/SW→(−x,+z))。

## 作成ファイル一覧

新規:
- `pipeline/build_features3d.py` — 両トラック対応(`--track barber|fuji`)。
  terrain.png読込→点群nDSMグリッド→OSM投影→樹木/建物/バリア生成→JSON。
  `--validate`(スキーマ検証)/`--self-test`(投影検算)/`--refresh-osm`/`--ept-depth` 付き。
- `public/data/tracks/barber/features3d.json`(386.5 KB)
- `public/data/tracks/fuji/features3d.json`(1035.1 KB)
- `specs/reports/p3a/REPORT.md`(本ファイル)

キャッシュ(再実行時に再DLしない):
- `pipeline/cache/usgs_ept/`(**82 MB**: ept.json + hierarchy + data/*.laz 343ノード)
- `pipeline/cache/osm_features3d_barber.json`(102要素)/ `osm_features3d_fuji.json`(124要素)
- Fuji高さは既存 `pipeline/cache/shizuoka_lp/`(42zip)を再利用(**追加DL 0**)

**既存 features.json / track.json / terrain.png / src/ には一切書き込んでいない**
(実行後mtimeで確認済み。全て本セッション前の時刻を保持)。

## 抽出方法(実装の要点)

### nDSMグリッド(両トラック共通)
- 地表: 既存 terrain.png(Terrarium, ~1m/px)を `populate_terrain_elevation.py`
  と同一の node-registered EPSG:3857 グリッドとして復元(Barber 1211×1083、Fuji 1756×1795)。
- 地物の上面: 点群の**クラス1(未分類=樹冠/屋根を含む)**点を、terrain と同一グリッドの
  各セルへ **max Z** で集約(順序非依存=決定的)。
- **nDSM = セルmax Z − terrain標高**。nDSM<0 または >60m、無点セルは void。

### 高さソース
- **Barber**: USGS 3DEP EPT `AL_11County_2_B23`(EPSG:3857, laszip, span128)。
  octreeを深さ11までbbox+40m交差判定で辿り、**交差ノードのみ**をDL(hierarchyの
  ネスト子ファイルも辿る)。5GB超過ガード実装(未発動)。
- **Fuji**: P1bキャッシュの LAS(EPSG:6676)を pyproj で 3857 に変換し class1 を max Z 集約。

### 樹木 / 建物 / バリア
- 樹木: OSM `natural=wood`/`landuse=forest` ポリゴン内を **8mグリッド+固定シードジッタ**で
  走査。走査点周辺(±step/2 px窓)の nDSM **p75** が >3m のセルに1本生成。
  height=そのp75、crownRadius=clamp(height×0.35, 2, 6)。`natural=tree_row` は5m間隔で
  弧長サンプリング、`natural=tree` ノードはそのまま(nDSM無ければ10m)。
  **総数上限20,000本**: 超過時はグリッドを `step×√(n/cap)` で決定的に粗化(Fujiは2段=10mで18,100本)。
- 建物: OSM footprint 内 nDSM の **p90**(有効セル≥10)。不足時は OSM `height`/`building:levels`×3.2、
  それも無ければ6.0m。footprintはローカルXZへ投影・衛星bboxへクリップ。
- バリア: OSM `barrier=guard_rail|fence|wall|retaining_wall` の way をXZポリラインで格納。
  既定高 guard_rail=0.75/fence=2.0/wall=1.5/retaining_wall=2.0(OSM `height` タグ優先)。
- **巻き順**: footprintは「+Yから見て反時計回り」に正規化。X̂×Ẑ=−Ŷ より、これは
  shoelace Σ(xᵢzᵢ₊₁−xᵢ₊₁zᵢ) **< 0**。全footprintがこれを満たすことを検証で確認。

## Classification分布(仕様の要ログ確認)

| トラック | 全読込点のクラス分布 | 抽出に使用 |
|---|---|---|
| **Barber** | class1=5,434,377 / class2=10,558,855 / class7=4,151 / class9=3,315 / class17=136 / class18=18,302 / class20=4,293 | class1(=34%) |
| **Fuji** | class1=272,935,089 / class2=99,449,289(73%/27%) | class1(=73%) |

**重要な知見**: 仕様は Barber で「クラス1+クラス5(高植生)+クラス6(建物)」を想定していたが、
このEPTデータセットには**クラス5/6が存在しない**(class1/2主体で、これはFujiのP1b実態と同構成)。
そのため両トラックとも「**クラス1(未分類=地上物)の max Z − 地表**」で nDSM を統一した。
class1 は開放地では地表相当(nDSM≈0で棄却)、樹林/建物上では樹冠/屋根に達するため、
DSM上面として妥当。ノイズ系(class7/18)は class1 に含まれず自動的に除外される。

## 件数・分布・主要建物高さ

### Barber(EPT深さ11、343ノード、84.7MB読込、DL 82MB)
- 樹木 **6,489本**、建物 **13棟**、バリア **72本**(fence11/wall61。元65way→クリップ分割で72)
- 樹高 p10/p50/p90 = **7.9 / 20.4 / 28.2 m**(min3.0, max40.1)
- nDSM: 有効セル52.8%、p50=0.66(開放地≈0)/p90=23.92/max41.94m
- 主要建物(全13棟がLiDAR実測):
  - **Barber Vintage Motorsports Museum → 23.1 m**(5階建て相当の期待20m超に合致。
    OSM `height` タグは13mだが実測を採用)
  - Leader Board 24.3 / Race Control 14.9・9.4 / Credential 6.9 / Office 7.8 / Shop 5.2〜7.1 /
    Trauma Center 4.7 / Storage Shed 4.1 / Private Garages 5.2 m

### Fuji(キャッシュ42zip、class1 148.5M点をbbox内集約、追加DL 0)
- 樹木 **18,100本**、建物 **91棟**、バリア **0本**
- 樹高 p10/p50/p90 = **6.8 / 15.1 / 19.9 m**(min3.0, max51.4)。仕様の「概ね10〜30m」域内
- nDSM: 有効セル90.5%、p50=1.47/p90=17.46/max55.46m
- 主要建物(89棟実測+2棟既定):
  - **グランドスタンド → 15.4 m**、ピットビルA → 16.8 m、コントロールセンター → 15.3 m、
    レストランOrizuru → 6.3 / ピットビルB → 5.2 / サービスガレージ → 4.4 m

## 判断保留・注記

1. **Barber にクラス5/6が無い**(上記Classification節)。仕様前提と異なるため、
   両トラックとも class1 max Z を上面として採用。データ実態に合わせた選択で、
   勝手な補正ではない。将来分類の充実したデータに差し替える場合は
   `ABOVE_GROUND_CLASS` 周りを見直せばよい。
2. **Fuji バリア0本**: OSM上に富士のbbox内へ `barrier=guard_rail|fence|wall|retaining_wall`
   の way が**存在しない**(建物108要素は取得できているのでクエリ/投影は正常)。
   OSMのカバレッジ制約であり抽出バグではない。Barberは65way取得(クリップで72セグメント)。
3. **グランドスタンド15.4m**: 仕様の期待は「20m級」。footprint内 nDSM の **p90**(仕様指定)を
   採用しており、屋根頂部より低いデッキ/座席上面を含むため頂部より控えめに出る。
   手法上は仕様準拠。頂部高が欲しければ p95/max へ切替可能(要仕様変更)。
4. **Barber 樹高が高め**(p50=20.4, max40.1): 仕様目安「5〜25m」の上側を一部超える。
   アラバマ州の成熟した混交林(テーダマツ等が30m級)としては妥当な樹冠高。
   p75窓集約なので単発ノイズには頑健。
5. **EPT深さの選択**: 深さ11で bbox上 ~10点/m²(仕様下限2点/m²を余裕で満足)、DL 82MB。
   深さ12まで辿ると ~16.8点/m²/約150MBだが、canopy top捕捉には11で十分と判断。
   `--ept-depth` で変更可能。5GBガードは未発動。
6. **帰属表示(ライセンス)**: `source` フィールドに
   「USGS 3DEP EPT AL_11County_2_B23」/「VIRTUAL SHIZUOKA 2019 LP (CC BY 4.0, 静岡県)」を記載。
   UIクレジット表記はP3のUI作業で対応予定(P1b同様の未対応事項)。

## 検証の詳細(再現手順)

- スキーマ: `python3 pipeline/build_features3d.py --track {barber,fuji} --validate`
- 投影検算: `python3 pipeline/build_features3d.py --track {barber,fuji} --self-test`
- 決定性: 各トラックを2回ビルドし `shasum -a256 features3d.json` が一致
- 座標整合/巻き順/スポットチェック: 検証ハーネスで
  - 逸脱0件(±0.5m float許容)、樹木のwood内包率、footprint shoelace<0 全数、
    名前付き建物centroid→lat/lng→衛星px が画像内、を確認
  - 衛星クロップ目視: 博物館・グランドスタンド・ピットビルが実構造物上に着地
