# G3D Spike — Google Photorealistic 3D Tiles 検証

HackTheTrack Replay Viewer とは**完全に独立**したミニアプリ。
Google の Photorealistic 3D Tiles(フォトグラメトリ3Dメッシュ)を表示し、
本体のトラックセンターライン(`public/data/tracks/*/track.json` の lat/lng/alt)
を同一の ECEF/ENU フレームに変換してオーバーレイし、位置整合を目視評価する。

本体の `package.json` / `vite.config.ts` / `src/` には一切依存しない。
依存はこのディレクトリ内で完結する(`three` + `3d-tiles-renderer` + `vite`)。

## 前提: API キー

Google Maps Platform の API キー(**Map Tiles API** を有効化したもの)が必要。
キーはコードには含めない。以下のいずれかで渡す(上から優先):

1. URL パラメータ `?key=YOUR_KEY`(読み込み後に URL からは自動で消去される)
2. `localStorage` の `g3d_api_key`(入力欄から保存される)
3. **推奨: ローカルファイル `spike/g3d/.g3d_key`**(キー文字列を1行だけ書く。
   `.gitignore` 済み・dev サーバーがローカルページにのみ提供。チャットや
   コード・URL にキーを出さずに済む)

```bash
# 配置例(YOUR_KEY を実際のキーに)
echo "YOUR_KEY" > spike/g3d/.g3d_key
```

キーが無い場合は入力ゲートが表示され、そこで待機する。

## 起動

```bash
cd spike/g3d
npm install     # 初回のみ
npm run dev     # ブラウザが自動で開く(http://localhost:5299 固定。本体の5199とは衝突しない)
```

### URL パラメータ

| パラメータ | 意味 | 既定 |
| --- | --- | --- |
| `?track=barber` | Barber Motorsports Park | ✔(既定) |
| `?track=fuji` | Fuji Speedway | |
| `?key=...` | API キー(一度だけ。localStorage に保存後 URL から消去) | |

例: `http://localhost:5299/?track=fuji`(キーは `.g3d_key` 推奨)

## 操作

| 入力 | 動作 |
| --- | --- |
| ドラッグ | 軌道回転(OrbitControls) |
| ホイール | ズーム |
| `S` | スクリーンショットを PNG でダウンロード(帰属表示込み) |
| `L` | オーバーレイライン表示の ON/OFF |
| `↑` / `↓` | ラインの高さを ±1m 調整(ジオイド差の実測に使う) |
| `Shift`+`↑`/`↓` | ±10m 調整 |
| `0` | 高さオフセットをリセット |
| `K` | API キーを再入力 |

高さオフセット機能は、本体の標高(EGM96 ジオイド基準)と Google タイル
(WGS84 楕円体高)の差を、ライン全体が路面に一致する量まで手で合わせて
読み取るためのもの。表示中のオフセット値が観測されたジオイド差になる。

## 座標整合の仕組み(重要)

- 本体トラックの `x/z` ローカル座標は**使わない**(北=−Z 系のため)。
  `track.json` の `lat/lng/alt` だけを信頼する。
- 各点を `WGS84_ELLIPSOID.getCartographicToPosition` で ECEF に変換。
- `3d-tiles-renderer` の `ReorientationPlugin` と**同一の** ENU フレーム
  (`getObjectFrame`)を自前で生成し、その逆行列で ECEF→ローカルに変換して
  から `Float32` バッファに格納する。これにより:
  - タイル群とオーバーレイが**完全に同一の変換**を受ける(整合が保証される)
  - float32 精度劣化(ECEF の ~6.3e6 m を直接格納すると ~1m 誤差)を回避
- 残るズレは「本体データの地理座標と Google メッシュ形状の実差」だけになり、
  そのまま評価値として読める。

## 制約 (ToS)

- タイルの保存・焼き込み・オフライン利用は不可。ランタイム表示のみ。
- 帰属表示(画面右下の copyright)は常時表示する(ToS 要件)。
- スクリーンショットは評価目的でのみ使用し、帰属表示を残す。

## 評価レポート

`../../specs/reports/p4/REPORT.md`(スクリーンショットは `shots/`)。
