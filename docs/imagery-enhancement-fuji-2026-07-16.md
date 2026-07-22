# 富士トラック周辺 航空写真2×高解像度化

## 結論

VIRTUAL SHIZUOKAの20cmオルソ画像を入力にし、富士スピードウェイ中心線から
80m以内を高品質域、80〜140mを透過フェード域とする2×タイルを生成した。

- 出力グリッド: **0.1096m/px**
- 元画像が持つ実情報量: **0.2192m/px（約20cm級）のまま**
- 方式: Real-ESRGAN x4plusを4×推論後2×へ縮小し、Lanczosと50%混合
- 出力: 1024×1024 WebP、119枚、16.84MB
- Google API呼び出し: **0回**
- 用途: Replayの視覚表現のみ。形状抽出、距離計測、測量検証には使わない
- Replay表示名: **静岡 20cm SR**

出力マニフェストは
`public/data/tracks/fuji/satellite_corridor_x2/manifest.json` にあり、入力画像の
SHA-256、出典、ライセンス、処理強度、情報解像度を保持する。

## 入力と権利

- 入力: `public/data/tracks/fuji/satellite_shizuoka.jpg`
- 出典: VIRTUAL SHIZUOKA 2019 LP orthophoto 20cm
- 提供者: 静岡県
- ライセンス: CC BY 4.0 / ODbL
- データセット: <https://www.geospatial.jp/ckan/dataset/shizuoka-2019-pointcloud>

Google Map Tilesは表示用途に限定し、この事前処理には一切使用していない。

## 客観検証

ホームストレート、中盤、終盤の3地点で、ネイティブ画像を一度1/2へ縮小してから
2×復元し、未加工のネイティブ画像を正解として比較した。強度25%時点で全地点が
Lanczos単独を上回った。

| 地点 | Lanczos PSNR / SSIM | Real-ESRGAN 25% PSNR / SSIM |
|---|---:|---:|
| ホームストレート | 29.2098 / 0.897112 | 30.0340 / 0.902947 |
| 中盤 | 30.5911 / 0.917601 | 31.7030 / 0.926652 |
| 終盤 | 32.2285 / 0.920972 | 33.0664 / 0.923726 |

0.05刻みで混合強度を探索し、平均PSNR低下0.30dB以内かつ平均SSIM低下0.002以内の
最も強い値として**50%**を採用した。生のGAN出力を強く使うと指標が悪化するため、
100%適用はしていない。

詳細値と比較画像:

- `public/data/tracks/fuji/satellite_corridor_x2/validation/validation-report.json`
- `public/data/tracks/fuji/satellite_corridor_x2/validation/*_comparison.jpg`

## 再生成

公式Windows GPU版Real-ESRGANを
`pipeline/tools/realesrgan-ncnn-vulkan/` に配置して実行する。

```powershell
python -m unittest pipeline\test_enhance_track_corridor.py
python pipeline\enhance_track_corridor.py --mode validation --engine realesrgan --sr-strength 0.25
python pipeline\enhance_track_corridor.py --mode tiles --engine realesrgan --sr-strength 0.50
```

実行ファイルは公式Real-ESRGAN READMEが案内するWindows版を使用した。
<https://github.com/xinntao/Real-ESRGAN/blob/master/README.md>

## 実装

- `pipeline/enhance_track_corridor.py`: 座標変換、コリドーマスク、検証、GPU推論、
  2×縮小、透過WebP、マニフェスト生成
- `pipeline/test_enhance_track_corridor.py`: 富士原点の画像内投影と
  core/fade/exteriorマスクの単体テスト

出力はタイル構造であり、16K全面画像を常時GPUへ載せる必要がない。Replay側では
マニフェストを読み、視錐台内タイルだけロードする構成へ接続した。

## Replayでの表示

- レイヤーパネルのSatellite欄から `静岡 20cm SR` を選ぶ。
- URLから直接選ぶ場合:
  `?track=fuji&race=fuji_aim_01&sat=shizuoka_x2`
- 下地は必ず `静岡県 20cm` を使い、その上へアルファ付きSRタイルを重ねる。
- 最大6並列で視錐台内だけロードし、視錐台を外れたタイルはGPUメモリから解放する。
- `Google Ground` が有効な間はSRタイルを非表示・解放し、Googleを無効にすると
  SR選択状態を復元する。
- 地形と道路表示ON/OFFの双方で、既存のground drapeと同じ高さへ追従する。

実装:

- `src/engine/track/EnhancedCorridorGround.ts`
- `src/engine/track/EnhancedCorridorGround.test.ts`
- `src/replay/satelliteVariants.ts`
- `src/engine/track/TrackBuilder.ts`

ブラウザ検証では119タイルを認識し、初期視点で12枚をロード。通常20cmへ切替えると
0枚まで解放され、SRへ戻すと再ロードされた。コンソールエラー、ページ例外、
HTTP 4xx/5xx、Google Tile API通信はいずれも0だった。

## 制約

- 0.1096m/pxは出力格子であり、真の10cm撮影データではない。
- AIが白線、縁石、車両、建物輪郭を推測する可能性がある。
- 真にz20を超える実情報が必要なら、10cm級の正規オルソまたはドローン測量が必要。
- 透明フェード領域では下地との混合になるため、画面全域が変化するものではない。
