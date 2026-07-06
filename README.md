# Replay Viewer v2

Barber Motorsports Park のレーステレメトリを 3D で再生するリプレイビューアです。
Vite + TypeScript + React + Three.js + Zustand + uPlot で構築されています。

![stack](https://img.shields.io/badge/stack-Vite%20%2B%20React%20%2B%20Three.js-blue)

## セットアップ

```bash
npm install        # 依存関係のインストール
npm run dev        # 開発サーバー (http://localhost:5199 固定・自動でブラウザが開く)
npm run build      # 型チェック + プロダクションビルド (dist/)
npm run test       # vitest によるユニットテスト
npm run preview    # ビルド結果のプレビュー
```

## データパイプライン

`pipeline/` の Python スクリプトで `public/data/` 配下の静的データを生成します。
リポジトリ直上の `Datasets/barber/`（生 CSV）と旧 `replay-viewer/`（トラック定義・衛星画像）を入力に使います。

```bash
# 1. トラックセンターライン → public/data/tracks/barber/track.json
python3 pipeline/build_track.py

# 2. 衛星画像 (99MB PNG → 4096px JPEG) → satellite.jpg / satellite_meta.json
python3 pipeline/convert_satellite.py     # 要 Pillow

# 3. レーステレメトリ CSV → 車両ごとのベストラップ JSON
#    → public/data/races/barber_r1/ (laps_index.json + lap_*.json)
python3 pipeline/build_race.py
```

各スクリプトは `--input` / `--output` などの引数でパスを上書きできます（`-h` 参照）。

## 操作方法

| 操作 | 機能 |
|------|------|
| `1` | チェイスカメラ（車両後方追従） |
| `2` | コックピットカメラ |
| `3` | トップビュー（コース全景） |
| `4` | フリーカメラ（マウスでオービット操作） |
| `5` | TVカメラ（コース脇の固定カメラ、ズーム追従） |
| `6` | シネマティック（自動ディレクターが4〜8秒間隔でカット切替） |
| `Space` | 再生 / 一時停止 |
| シークバー | ドラッグでシーク、`0.5x/1x/2x/4x` で再生速度変更、`↺` でループ |
| `[change]`（左上） | ラップ選択モーダルを開く（Main タブ = 再生ラップ、Ghost タブ = 比較ラップ） |
| `Telemetry`（下中央） | uPlot テレメトリグラフ（速度/スロットル/ブレーキ、距離軸・時間軸切替、クリックでシーク、ゴースト重ね描き） |
| ミニマップ（左下） | セクター色分けのコース図。黄=メイン車両、シアン=ゴースト。クリックでその地点にシーク、`–`ボタンで折りたたみ |

ゴーストを選択すると、距離整列されたゴースト車両（半透明シアン）と DELTA 表示（＋赤=遅い / −緑=速い）、セクタータイムが HUD に表示されます。

## アーキテクチャ概要

```
src/
├── main.tsx / App.tsx        エントリ・データロード・UI合成
├── replay/                   データ層（純粋ロジック、vitest対象）
│   ├── dataLoader.ts         track.json / laps_index.json / lap JSON の取得
│   ├── projection.ts         緯度経度 → ローカル平面座標
│   ├── interpolation.ts      時刻→位置・姿勢・チャンネル値の補間 (sampleReplay)
│   └── delta.ts              距離整列（ゴースト時刻変換）・デルタ・セクター計算
├── state/replayStore.ts      Zustand ストア（再生状態・カメラ・ラップ・UI表示）
├── engine/                   Three.js レンダリング層
│   ├── ReplayScene.ts        シーン統括：車両リグ・ゴースト・ライト・更新ループ
│   ├── ViewerCanvas.tsx      React ↔ Three.js ブリッジ、rAFループ、キー入力
│   ├── track/TrackBuilder.ts 路面リボン・縁石・スタートライン・ブレーキマーカー・衛星地面
│   ├── DataTrail.ts          速度/ブレーキで色分けされた発光トレイル
│   ├── Effects.ts            postprocessing (Bloom/SSAO/SMAA/Vignette、品質プリセット)
│   ├── Sky.ts                スカイドーム
│   └── cameras/              カメラコントローラ群
│       ├── cameraMath.ts     チェイス/コックピット/トップ/TVズームの純粋計算
│       ├── director.ts       シネマティック用ショット選択の純粋ロジック
│       └── CinematicCamera.ts TV/チェイス/コックピットを自動ハードカット
└── ui/                       HUD・ラップ選択・再生コントロール・グラフ・ミニマップ
```

- **データフロー**: rAF ループ（ViewerCanvas）→ store.advance → `sampleReplay` で補間 →
  ReplayScene が車両/ゴースト/カメラ/トレイルを更新 → store へテレメトリを約20Hzで還流 → HUD/ミニマップが購読。
- **ゴースト比較**: ECU クロックのずれを避けるため時間ではなく距離で整列
  （`ghostTimeForMainTime`）。デルタは同距離での経過時間差。
- **パフォーマンス**: pixel ratio クランプ、品質 low で SSAO 無効、ホットパスの
  Vector3/Quaternion 再利用、three / postprocessing / uplot+react のチャンク分割。

## 検証スクリプト

devサーバー起動中（ポート5199）に Playwright でスクリーンショット検証ができます。

```bash
node snap.mjs          # チェイス/TV/トップの3ショット → /tmp/fix_*.png
node phase4_ghost.mjs  # ゴースト+テレメトリ表示の統合ショット → /tmp/p4_ghost.png
```
