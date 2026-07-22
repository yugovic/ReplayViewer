# トラック上の線を鮮明にするための調査と実装

調査・実装日: 2026-07-16  
対象: ReplayViewer / 富士スピードウェイ  
主目的: 航空写真全体の高解像度化より、白線・舗装境界・縁石を走行視点で明瞭にする

## 結論

この目的には、画像全体へ強いSuper Resolutionをかけるより、次の順が有効である。

1. ネイティブ画像上で線の位置を決める
2. 連続性・コース幅・閉ループ等のトラック制約で誤検出を除く
3. 線をサブピクセルまたはベクトルとして保持する
4. 元の航空写真/SR画像の上へ、細いアンチエイリアス線として再描画する
5. 元画像自体には弱い局所コントラスト・deblurだけを適用する

ベクトル線はズームしてもぼやけず、AIが存在しない線を作る危険も抑えられる。画像の
「見た目」と、線の「位置」を別レイヤーにするのが重要である。

## 先行研究から使える考え方

### サブピクセル線検出

Stegerのcurvilinear structure detectorは、線の横断輝度プロファイルを明示的にモデル化し、
線中心と幅をサブピクセルで推定する。左右の背景コントラストが非対称な場合に線位置が
偏る問題も補正対象にしている。航空画像の線抽出例も含む。

- [Steger, An Unbiased Detector of Curvilinear Structures, IEEE TPAMI 1998](https://doi.org/10.1109/34.659930)

白線を単なるCanny edge 2本として扱うより、「幅を持つ明るいridgeの中心」として扱う方が
トラック境界に向く。

### 勾配・構造を保つSuper Resolution

GAN系SRは質感をきれいにする一方、輪郭を曲げたり偽の細線を作ることがある。
Gradient Guidanceを使うStructure-Preserving SRは、画像空間だけでなく勾配構造を制約し、
幾何的輪郭の崩れを抑える方向の研究である。

- [Structure-Preserving Super Resolution with Gradient Guidance](https://arxiv.org/abs/2003.13081)
- [SRCT: Structure-Preserving Method for Sub-Meter Remote Sensing Image SR](https://pmc.ncbi.nlm.nih.gov/articles/PMC12846202/)

ただし単画像SRは元画像にない測量情報を追加しない。線位置はネイティブ画像または手修正値で
固定し、SRは表示色・質感だけに使う。

### 航空画像の道路線抽出

航空画像から道路中心線とエッジ線を同時に推定するCNN回帰、道路標示をUAV画像から抽出する
feature-pyramid型ネットワーク等が研究されている。

- [Automatic Extraction of Road Centerlines and Edge Lines from Aerial Images](https://isprs-annals.copernicus.org/articles/V-2-2020/925/2020/)
- [Road marking extraction in UAV imagery using attentive capsule feature pyramid network](https://doi.org/10.1016/j.isprsjprs.2022.01.003)

サーキットでは一般道路より強い事前知識を使える。左右線は概ね中心線に沿い、急に飛ばず、
互いに交差せず、閉ループになる。画像分類だけでなく、この形状制約を後処理へ入れる。

## 推奨する画像処理

### A. 線位置の抽出

- Lab色空間のL成分で局所照明差を減らす
- 白線にはwhite top-hat、Hessian/ridge response、方向別フィルタを併用
- 舗装境界には輝度だけでなく色・テクスチャ勾配を使う
- 中心線からの探索距離を制限し、左右別に候補を追跡
- 非最大抑制で太い反応を1本の中心へ細線化
- 曲率・幅の連続性と信頼度で短い誤反応を除外
- 低信頼区間だけ人が修正する

車両、影、建物、観客席、ガードレールは強いエッジになるため、画像全域の一律sharpenは避ける。

### B. ラスター画像の控えめな強調

- edge-preserving denoise
- 小半径のdeconvolutionまたはunsharp mask
- CLAHE等の局所コントラスト補正
- 検出した線の近傍だけ、輝度勾配を狭く再配分
- 色は大きく変えず、線のhaloとovershootを制限

評価はPSNR/SSIMだけでは不十分である。線中心の位置誤差、線幅誤差、連続率、偽線率、
走行視点での可視率も測る。

### C. ベクトル再描画

最終表示は、抽出・手修正した線中心を0.15〜0.25m程度の細いリボンとして地形へ投影する。
アンチエイリアス、深度オフセット、カメラ距離に応じた透明度調整を使えば、写真の雰囲気を
残しながら線だけを明瞭にできる。

## 今回の富士への実装

既存シェーダーにも白線表示はあったが、全周を中心線から左右一律6.5mとしていた。
富士の手修正済みエッジは左右非対称・可変幅なので、区間によって線位置が合わなかった。

今回、`track-creator/tracks/fuji/track.json` の次のデータを表示へ接続した。

- 左右各2,280点
- 2m間隔
- 画像解析後に人手修正済み
- `track-line-center` として扱い、追加の内側オフセットは0m

生成した `public/data/tracks/fuji/road_edges.json` を読み、左右各0.20m幅の白線リボンを
路面高さ+55mmへ描く。線は約2m間隔で地形へ追従し、描画順を航空写真、Google Ground、
ローカルSRタイルより上にしている。

これにより `静岡 20cm SR` 選択時にも線がSRタイルに隠れず、`Track lines` トグルで
縁石と一緒にON/OFFできる。データがない他トラックは、従来の一律幅シェーダーへ
フォールバックする。

実装:

- `src/engine/track/RoadEdgeLines.ts`
- `src/engine/track/RoadEdgeLines.test.ts`
- `pipeline/export_road_edges.py`
- `public/data/tracks/fuji/road_edges.json`
- `src/engine/track/TrackBuilder.ts`

## 検証結果

- 対象テスト: 3ファイル、22件すべてPASS
- TypeScript + Vite本番ビルド: PASS
- Playwright実画面: `shizuoka_x2` 上で左右2メッシュを確認
- 頂点数: 左4,558、右4,558
- `Track lines` ONで表示、OFFでグループ非表示
- console error: 0
- page error: 0
- HTTP 4xx/5xx: 0

スクリーンショット:

- `Temp/road-edge-lines-on.png`
- `Temp/road-edge-lines-off.png`

ローカル画像表示ツールはWindows sandbox helper障害で開けなかったため、このセッションでは
スクリーンショットの目視評価を完了とはしない。Three.js実メッシュ、表示状態、画面出力、
エラー有無までは自動検証した。

## 制約と次の改善

- 今回の線は手修正済みエッジを可視化したもので、測量線ではない。
- 白線が存在しない舗装端区間では、データ解釈を区間別に分ける必要がある。
- 既存カント標高は基準幅13mで作られているため、非常に広い可変エッジでは線高さを端で
  クランプしている。見た目に段差が出る区間はLiDAR地形へ切り替える。
- 次は、線種を `white paint / paved edge / curb / pit line` に分け、色・幅・透明度を別設定にする。
- 遠景では現幅、車載近景では写真を残すよう、距離に応じてopacityを下げると自然になる。

## Google Mapsの境界

Google Map Tilesはオンデマンド表示・目視比較だけに使う。Googleの現行ポリシーは、無許可の
保存に加えてimage analysis、machine interpretation、object detection、geodata extractionを
非表示用途として認めていないため、線抽出や高解像度化の入力には使わない。

- [Google Map Tiles API Policies](https://developers.google.com/maps/documentation/tile/policies?hl=en)

今回の線データとSR画像は、派生利用可能なVIRTUAL SHIZUOKA画像とリポジトリ内の
手修正データだけを基にしている。
