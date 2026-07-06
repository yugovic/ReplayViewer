# P5: G3D観戦モード — 実装レポート

Google Photorealistic 3D Tiles の上で実リプレイ車両1台を走らせる観戦モードを
`spike/g3d` の拡張として実装した(本体 `src/`・5199・5200 は未変更)。

---

## ユーザー向け起動手順

```bash
cd replay-viewer-v2/spike/g3d
npm run dev        # → http://localhost:5299/ が開く(ポート5299固定)
```

APIキーは `spike/g3d/.g3d_key`(devサーバーが `/g3d-key` で供給)から自動読込。

観戦モードのURL(`?race=` があれば観戦モード、無ければ従来のライン検証モード):

- Barber: `http://localhost:5299/?track=barber&race=barber_r1`
- Fuji: `http://localhost:5299/?track=fuji&race=fuji_aim_01`
- ラップ指定(任意): `&lap=GR86-022-13_lap_008.json`(省略時は最速ラップを自動選択)

### キー操作

| キー | 動作 |
|------|------|
| `Space` | 再生 / 一時停止 |
| `[` `]` | 再生速度切替(0.5 / 1 / 2 / 4x) |
| `C` | カメラ循環(Chase → TV → Orbit) |
| シークバー | 任意時刻へジャンプ(画面下中央) |
| ドラッグ / ホイール | Orbitモード時の視点操作 / ズーム |
| `↑` `↓` | 高さ微調整 ±1m(車両+ライン共通) |
| `Shift+↑` `↓` | 高さ微調整 ±10m |
| `0` | 高さをトラック既定値へリセット |
| `S` | スクリーンショット保存 |
| `L` | センターライン表示ON/OFF |
| `K` | APIキー再入力 |

再生はループする。HUD(左上)に車両ID・ラップタイム・カメラモード・再生速度・
速度(km/h)、右下にGoogle帰属表示を常時表示。

---

## 実装概要

- **データ配信**: `vite.config.js` に `/race/{race_id}/{file}.json` middleware を追加
  (track.json と同方式・コピーせず in-place 配信、パストラバーサル対策込み)。
- **ラップ選択**: `laps.json` の `selected[]` から `laps[].lap_time_seconds` を
  参照して**最速ラップを自動選択**(決定的・Math.random不使用)。
  検証: Barber→`GR86-022-13_lap_008`(97.007s, overall best)、
  Fuji→`osaki_hmr_demio_101_lap_003`(141.385s, overall best)を選択。
- **座標変換**: 車両の lat/lng を **P4と同一の** `getCartographicToPosition`
  → `ecefToLocal`(ENUローカル)で配置。ローカルx/z座標は不使用。
- **高さ**: track.json centerline の `dist`→`alt` をラップの `dist[]` で線形補間し、
  トラック定数のジオイド補正(**barber −30.0m / fuji +45.0m**)を加算。
  `↑/↓` トリムは車両+ライン共通オフセットとして流用(既定=ジオイド定数)。
- **姿勢**: ヨーは前後サンプル差分ベクトルから算出(ピッチ/ロールは0)。
- **車両**: 低ポリ箱車(ボディ+キャビンの2ボックス、約1.8×1.3×4.6m、赤)
  + 接地マーカー(暗い楕円)。**車両専用の DirectionalLight+AmbientLight** を追加
  (Google タイルは unlit のため二重陰影にならない)。
- **カメラ**: Chase(後方上空追従・スムージング付)/ TV(コース周囲の空中4点を
  進行度で自動切替)/ Orbit(既存OrbitControls・車両非追従)。既定Chase。

---

## 検証結果(Playwright + 実Chrome)

barber・fuji 両方を実際に再生し、車両が Google 路面上を走ることを確認。
スクショは `shots/`(HUD + Google帰属表示を合成済み):

| ファイル | 内容 |
|----------|------|
| `barber_chase.png` | Barber Chase視点。車両が路面に接地、赤ラインが車両直下を通過 |
| `barber_tv.png` | Barber TV(俯瞰)。ピンクのセンターラインがコースリボンに完全一致 |
| `fuji_chase.png` | Fuji Chase視点。車両が路面に接地 |
| `fuji_tv.png` | Fuji TV(俯瞰)。富士山を背景にラインがサーキット全周に一致 |

### 浮き/沈み/コース外

- **一致良好**: 両トラックとも俯瞰でセンターラインがコース路面に一致、
  Chaseで車両は路面に接地し接地マーカーが路面に落ちている。
  高さ既定値(barber −30 / fuji +45)のまま浮き/沈み/コース外走行は観測されず、
  修正は不要だった。座標変換の自己検証(centerline投影=既存ライン一致)もOK。

### FPS

- 自動キャプチャは Chrome のバックグラウンドタブ rAF スロットリング下で実行した
  ため、稼働中の実FPSは直接サンプルできなかった(HUDのFPS値は0表示)。
- 代替として **描画1フレームの実測コスト**を計測:
  フルRetina解像度 **2940×1602**、タイル 386 groups ロード時で
  **約5.9 ms/frame(≒170fps上限)**。表示リフレッシュ(60fps)に対して
  十分な余裕があり、対話操作は快適。

---

## 検証手法についての注記

自動化環境では対象タブが常にバックグラウンド(`document.visibilityState==='hidden'`)
となり、`requestAnimationFrame` が完全停止する。3d-tiles-renderer のダウンロード/
パースキューは rAF でスケジュールされるためタイルが一切ロードされない問題があった。
検証時のみ `schedulingCallback` を `setTimeout` に差し替え、`tiles.update()`/
`tryRunJobs()`/`renderer.render()` を手動ポンプしてロードを駆動した(本体挙動は不変)。
スクショは devサーバーの `/save-shot`(dev専用、POST→PNG保存)経由で
`shots/` に保存し、HUDと帰属表示をcanvasに合成した。
これらは検証補助であり、通常起動(タブがフォアグラウンド)では不要。

## 制約遵守

- 本体 `src/`・5199・5200 は未変更。spike は 5299 固定。
- `.g3d_key` の中身は読み出し・出力していない。
- タイルの保存・焼き込みは行っていない(スクショは帰属込み評価目的のみ)。
- Math.random 不使用(ラップ選択・色は決定的)。
