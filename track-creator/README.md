# track-creator

自作トラッククリエイター。**「① 高低差(標高)を先に取得 → ② コース幅・縁石を設定 → ③ GLB を出力」** という3ステップでサーキットを生成する Node/TypeScript ツール。ReplayViewer 本体とは独立して動く(座標規約は共通: メートル、ENU Y-up、x=東 / z=南(-北))。

②は **ブラウザ上のインタラクティブエディタ**(下記)でも、track.json の手編集でもできる。

## クイックスタート

```bash
cd track-creator
npm install

# ① 標高取得(中心線に沿ってコリドー3点をサンプリング → elevation.json に保存)
npm run elevation -- tracks/demo

# ② エディタで幅・縁石を編集(推奨)
npm run edit          # → http://localhost:5174/editor/?track=fuji

#    または track.json を手編集(下記スキーマ参照)。dist の当たりを付けるには:
npx tsx src/cli/inspect.ts tracks/demo

# ③ ビルド → out/demo.glb(エディタの「保存してGLBビルド」でも同じ)
npm run build -- tracks/demo

# プレビュー(three.js ビューア。 /preview/?glb=名前 / &top=1)
npm run view
```

## インタラクティブエディタ(npm run edit)

Blender の Track Tools 的なワークフローをブラウザで:**実際の走行データと航空写真を重ねて、客観的な情報を見ながら幅・縁石をドラッグ編集**する。

| 機能 | 操作 |
|---|---|
| コース幅(エッジ編集) | 「コース幅」モード → トラックのふちが点列(緑=左 / 桃=右、1〜10mの選択間隔)で表示される → 点を横にドラッグでふちを自由変形。**ブラシ半径**で周辺の点もコサイン減衰で追従(0=その点だけ)。左右は独立(「左右対称に編集」でミラー)。「エッジをリセット」で基本幅に戻る |
| 縁石 | 「縁石」モード → 路面を2クリック(開始→終了)。中心線の左右どちらをクリックしたかで側が決まる。端点ハンドルで伸縮・**中央ハンドルでスパンごと移動**、L/Rタグで左右反転。縁石は編集後のエッジに自動追従 |
| 地図オーバーレイ | 地理院タイル「全国最新写真」を実座標に配置(track.json の origin 必須)。不透明度調整・「路面より手前に表示」でトレース作業 |
| 走行データ | `tracks/<名前>/refs.json` のポリライン(ロガーのラップ軌跡など)を色分け表示 |
| 視点 | 上面(正射影)/ 3D 切替。1/2/3 キーでモード切替 |
| Undo | Ctrl+Z(ドラッグ1回=1ステップ) |
| 保存 | 「保存」= track.json 書き戻し、「保存してGLBビルド」= そのまま out/<名前>.glb 生成 |

編集はすべて CLI と同じビルダーでリアルタイムに再生成されるので、**エディタで見えるものがそのまま GLB になる**。

### 実データの取り込み(富士の例)

```bash
npx tsx src/cli/importFuji.ts
```

tracktools-kit の実測データから `tracks/fuji/` を生成する:
- `centerline.json`(ロガー由来の中心線 652点 + LiDAR 由来の左右端標高)→ track.json + elevation.json(`source: "imported"` — `npm run elevation` では上書きされない)
- Blender オーバーレイスクリプトに焼き込まれたラップ軌跡(lap1 / lap2 / lap3_FASTEST)→ refs.json

## パイプライン

```
track.json ──┐
             ├─ ① npm run elevation ──▶ elevation.json   (標高コリドー: 左端/中心/右端)
             │                              │
             └─ ② 幅・縁石を編集            │
                          │                 │
                          ▼                 ▼
                 ③ npm run build ──▶ out/<name>.glb  (road + curbs ノード)
```

標高を **ファイルに事前保存** するのがポイント。GSI タイルの再取得なしに幅や縁石を何度でも調整でき、`elevation.json` を編集すれば局所的な地形修正も手作業でできる。

## track.json スキーマ

```jsonc
{
  "name": "demo",
  "closed": true,                      // 周回コースか
  "origin": { "lat": 35.37, "lng": 138.92, "alt": 580.9 },  // source "gsi" のとき必須
  "controlPoints": [ { "x": 0, "z": 0 }, ... ],  // 中心線の制御点(ローカルm)。Catmull-Romが通る
  "elevation": {
    "source": "synthetic",             // "gsi" | "synthetic" | "flat"
    "step": 5,                         // サンプリング間隔 m
    "smooth": 9                        // 移動平均ウィンドウ(ノイズ・段差ならし)
  },
  "road": {
    "width": 12,                       // 基本舗装幅 m
    "step": 2,                         // メッシュの縦分割間隔 m
    "widthProfile": [                  // 任意: dist(m) キーで幅を線形補間(周回はラップ)
      { "dist": 500, "width": 12 },
      { "dist": 620, "width": 14.5 }
    ],
    "edges": {                         // 任意: 自由変形エッジ(エディタが書く)。
      "step": 2,                       //   step m ごとの中心線→ふち距離を左右独立に持つ。
      "left": [6, 6, 6.5, ...],        //   これがあると width / widthProfile より優先。
      "right": [6, 6, 6, ...]
    }
  },
  "curbs": [                           // 任意: 縁石スパン
    {
      "startDist": 545, "endDist": 640,  // 中心線の弧長範囲 m
      "side": "left",                    // 進行方向に対して左/右
      "width": 1.2,                      // リボン幅 m
      "stripeLength": 4                  // 赤/白 1ブロックの長さ m
    }
  ]
}
```

## 実在地形(国土地理院 標高タイル)

`elevation.source: "gsi"` にして `origin` の緯度経度を入れると、dem5a(航空レーザー測量、フォールバック dem10b)からバイリニア補間で標高を取得する。タイルは `tracks/<name>/cache/` にディスクキャッシュされ、2回目以降はオフラインで動く。

- ローカル y=0 は `origin.alt`(省略時は最初のステーションの標高)基準に置き直される
- 出典表記が必要: 「国土地理院 標高タイルを加工して作成」
- LiDAR点群(例: VIRTUAL SHIZUOKA)を使う高精度版は将来の拡張(下記)

## 出力 GLB

- `road` ノード: 3列(左端/中心/右端)×2mピッチのリボン。左右端の標高差からカント(横断勾配)を再現
- `curbs` ノード: 赤白ストライプを **頂点カラー(COLOR_0)で焼き込み** — どの glTF ビューアでもシェーダ不要で縞が出る
- +Y up・メートルなので、ReplayViewer には `public/data/tracks/<name>/` に置くだけで流用可能

## フォルダ構成

```
src/
  centerline.ts        Catmull-Rom 中心線の等間隔リサンプル・弧長補間(純粋関数)
  elevation/providers.ts  GSI標高タイル / 合成地形 / フラット
  mesh/stations.ts     中心線×標高×幅 → ステーション列
  mesh/road.ts         路面リボン
  mesh/curbs.ts        縁石リボン(ストライプ頂点カラー)
  glb.ts               @gltf-transform/core で GLB 書き出し
  cli/fetchElevation.ts  ① 標高取得
  cli/build.ts           ③ ビルド
  cli/inspect.ts         コーナー dist の確認用
preview/               three.js 簡易ビューア(npm run view)
tracks/demo/           サンプル(1.7km 周回、synthetic 地形、縁石11スパン)
```

## テスト

```bash
npm test   # centerline リサンプル / 幅プロファイル / カント / 縁石の 12 テスト
```

## 今後の拡張候補

- LiDAR 点群(LAS/LAZ → グリッド)を読む `ElevationProvider`(VIRTUAL SHIZUOKA 等)
- 路肩・グラベル・壁・フェンスのリボン、スタートライン
- 路面テクスチャ(UV は v=弧長[m] で既に出力済みなのでタイリングテクスチャを貼るだけ)
- GeoJSON / KML から中心線をインポート(Google Earth でなぞる → 実在コース)
- Web エディタ化(制御点のドラッグ編集 → 即時リビルド)
