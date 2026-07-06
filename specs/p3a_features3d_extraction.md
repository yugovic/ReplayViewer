# P3a仕様: LiDAR+OSMからの立体フィーチャ抽出(features3d.json)

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 目的

両トラックの「樹木」「建物」「バリア(ガードレール/フェンス/壁)」の
**位置と実測高さ**を抽出し、ビューア描画用の静的データ
`public/data/tracks/{barber,fuji}/features3d.json` を生成する。
描画(P3b)は別タスク。本タスクはデータ生成と品質検証のみ。

## 検証済みデータソース(2026-07-05にFable 5が確認済み)

- **富士の高さ**: P1bでキャッシュ済みの `pipeline/cache/shizuoka_lp/`(42zip、6.9GB)。
  この中の**クラス1(未分類)が樹冠・建物を含む**(全点の73%、約14点/m²。
  P1bのREPORT参照)。クラス1の高さ − DTM(クラス2から作った地表面)= nDSM
  (地物の地上高)。**追加ダウンロード不要**
- **Barberの高さ**: USGS 3DEP点群のEPT配信
  `https://s3-us-west-2.amazonaws.com/usgs-lidar-public/AL_11County_2_B23/ept.json`
  (実在・スキーマ確認済み: EPSG:3857、laszip、Classification列あり、
  hierarchyType=json)。bboxが小さいので、bboxに交差するオクツリーノードだけを
  適切な深さまで辿ってダウンロードする(laspy+lazrsで読める。PDAL/GDAL CLI禁止)。
  目安: 地物判定に約2点/m²以上。キャッシュは `pipeline/cache/usgs_ept/`
- **フットプリント/線形地物**: OSM Overpass(`fetch_osm_features.py` と同じ
  エンドポイント・キャッシュ・リトライ規約)。取得タグ:
  - 建物: `building=*`(ways/multipolygon)
  - バリア: `barrier=guard_rail|fence|wall|retaining_wall`(ways)
  - 樹木領域: `natural=wood`、`landuse=forest`(既存features.jsonの
    woodsと同源でよい)、`natural=tree_row`(way)、`natural=tree`(node)

## 成果物

1. 新規スクリプト `pipeline/build_features3d.py`(両トラック対応、
   `--track barber|fuji`)
2. `public/data/tracks/{barber,fuji}/features3d.json`
3. 検証レポート `specs/reports/p3a/REPORT.md`(日本語)

## features3d.json スキーマ(この形を守る。P3b描画がこれを前提にする)

```json
{
  "version": 1,
  "origin": { "lat": ..., "lng": ..., "alt": ... },
  "source": "USGS 3DEP EPT AL_11County_2_B23 + OSM / VIRTUAL SHIZUOKA 2019 + OSM",
  "trees": [ { "x": 0.0, "z": 0.0, "height": 12.5, "crownRadius": 4.2 } ],
  "buildings": [ { "footprint": [[x,z],...], "height": 8.4, "tags": {"building": "yes", "name": "..."} } ],
  "barriers": [ { "type": "guard_rail", "points": [[x,z],...], "height": 0.8 } ]
}
```

- 座標は**ローカルシーンXZメートル**(latLngToLocal相当の等距円筒、
  origin=track.jsonのorigin、**北=−Z**)。`fetch_osm_features.py` の
  投影コードの規約を必ず踏襲する(鏡映修正済みの本線コード。.bak-mirror
  は旧版なので参照しない)
- `height` は**地上高**(メートル)。絶対標高ではない(描画側が地形に載せる)
- footprintの巻き順は反時計回り(XZ平面を+Yから見て)に正規化する

## 抽出方法

### nDSMグリッド(両トラック共通、約1m)
- 地表: 既存 terrain.png をサンプル(P1で1m LiDAR化済み。规約は
  `populate_terrain_elevation.py` と同じ)
- 地物点: 富士=キャッシュzipのクラス1点、Barber=EPTノードの
  クラス1(Unclassified)+クラス5(High Vegetation)+クラス6(Building)点
  (プロジェクトごとに分類の充実度が違う。**まずClassificationの分布を
  ログに出して方針を確定**すること)
- nDSM = 点のZ(正標高) − terrain標高。負値と>60mは棄却

### 樹木
- OSMのwood/forestポリゴン内を約8mグリッド+ジッタで走査し、
  nDSM > 3m のセルに1本生成。height=そのセルのnDSM(p75程度の代表値)、
  crownRadius = clamp(height*0.35, 2, 6)
- tree_row(線)は5m間隔、tree(点)はそのまま、樹高はnDSM(無ければ10m)
- **総数上限: 1トラックあたり20,000本**。超える場合はグリッドを粗くする
  (間引きの乱数はシード固定で再現可能に)

### 建物
- OSM footprintごとに height = footprint内nDSMのp90(点が10個未満なら
  タグ`height`/`building:levels`×3.2m、それも無ければ6.0m)
- footprintはローカルXZへ投影して格納

### バリア
- OSM barrier waysをローカルXZポリラインで格納。height既定値:
  guard_rail=0.75、fence=2.0、wall=1.5、retaining_wall=2.0
  (OSMのheightタグがあれば優先)

## 絶対に守る制約

- **キラリティ**: 投影は`fetch_osm_features.py`の本線実装と同一に。
  既知地物での検算をスクリプト内self-testとして実装すること
  (例: 富士のメインストレートは敷地の**東側**にある→ローカル+X側、等、
  実データで確認できる形で)
- 既存 features.json / track.json / terrain.png には**一切書き込まない**
- Overpassのレート制限に配慮(キャッシュ必須、`pipeline/cache/`)
- EPTノードのダウンロードは必要最小限(bbox交差判定してから取得、
  キャッシュ、リトライ)。総量が5GBを超えそうなら深さを1段浅くする
- src/ のビューアコードには触らない(P3bの領分)
- 乱数はシード固定(features3d.jsonの再生成が決定的であること)

## 検証(全5項目)

1. **件数と分布**: 両トラックの trees/buildings/barriers 件数、
   樹高分布(p10/p50/p90)、建物高さ一覧(名前付きは名前も)。
   富士の樹木は概ね10〜30m、Barberは5〜25mに収まるはず
2. **既知地物のスポットチェック**(最重要):
   - 富士: メイングランドスタンドの高さ(実物は20m級)、
     ピットビル、コース西側の森の樹高
   - Barber: 博物館(Barber Vintage Motorsports Museum、5階建て相当)、
     コース周辺の森
   衛星写真(satellite.jpg)上の位置と目視整合することもピクセル座標換算で確認
3. **座標整合**: features3d.jsonの樹木/建物を既存features.jsonの
   woodsポリゴン・satellite_meta bboxに重ねて範囲逸脱がないこと
   (逸脱0件。1件でもあれば投影バグ)
4. **スキーマ検証**: JSONが上記スキーマに合致(簡易バリデータを
   スクリプトに内蔵、`--validate`)
5. **決定性**: 同一入力で2回実行してfeatures3d.jsonがバイト一致すること

## レポート

`specs/reports/p3a/REPORT.md`: 実施内容、検証5項目、Classification分布、
EPTダウンロード量、判断保留事項。
