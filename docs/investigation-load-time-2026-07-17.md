# 初期ロード時間の原因分析と改善方針（2026-07-17）

## 要旨

「コンテンツのロードが遅い」体感の原因を、既存の `perf_profile.mjs` を使った実測
（dev server / 本番ビルド preview の両方、コールドキャッシュ、headless Chromium）で
特定した。**最大の犯人はコードでもアセットサイズでもなく、起動シーケンスの
ブロッキング構造**: `loadInitialReplay()` が衛星画像バリアント6種の存在確認
HEADプローブを `Promise.all` で待っており、29MBの `satellite_shizuoka.jpg` への
HEADが実測5.8〜8.9秒かかるため、**3D画面のマウント自体がその間1pxも進まない**。
これに (a) dev server固有のオーバーヘッド（モジュール変換でDCLまで7.7秒、データ
配信の遅延）、(b) トラック構築の同期メインスレッド処理（dev実測で単一8.5秒の
ロングタスク）、(c) 大判アセット（terrain.png 3.5MB、shizuoka 29MB等）が積み重なる。
改善は3段階: **P0=プローブを初期ロードのクリティカルパスから外す（数行の変更で
富士の初期表示を6〜9秒短縮、最も費用対効果が高い）**、P1=アセットの軽量化
（terrain 16bit化・shizuoka段階ロード・ImageBitmapデコード）、P2=ドレープ計算の
Worker化/事前計算と進捗UI。なお体感評価は dev server ではなく本番ビルド
（`vite build` + preview/実ホスティング）で行うべき。差は歴然（下表）。

## 1. 実測サマリー

計測: `node perf_profile.mjs <base-url>`（リポジトリ既存スクリプト）。
headless Chromium（SwiftShaderソフトウェアレンダリング）のため描画系の絶対値は
実GPUより悪化して出る点に注意。ネットワークウォーターフォールとブロッキング
構造の証拠としては有効。

| 指標（コールドキャッシュ） | dev server (5199) | 本番build preview (5299) |
|---|---|---|
| Fuji: 最初の3D描画 (firstAnyDraw) | 21.6 s | 7.0 s |
| Fuji: シーンがフル描画 (firstRichFrame) | 25.9 s | 8.1 s |
| Fuji: ラップ選択ダイアログ表示 | 36.5 s | 18.4 s ※ |
| Barber: シーンがフル描画 | 9.8 s | 1.6 s |
| DOMContentLoaded | 7.7 s | 0.4 s |

※ ダイアログ表示時刻はSwiftShaderの描画ロングタスクでReactのコミットが遅延した
影響を含む。実GPUではもっと早い。

### Fuji（dev）のウォーターフォール要点

1. 0〜7.7s: viteのモジュール変換（DCLまで）。この間データフェッチも遅い
   （barberでは track.json / laps.json の取得に7.5秒かかる実測。変換処理と
   静的配信が同一プロセスで競合）。
2. 8.1s〜: `loadInitialReplay()` が発火。track.json / laps.json と**並行して
   衛星バリアント6種のHEADプローブ**が走る。
   - `satellite_shizuoka.jpg`（29MB）へのHEAD: **8.87秒**
   - `satellite_bing_sr.jpg`（9.7MB）: 3.1秒 / `satellite_bing.jpg`: 2.4秒
   - しかも**各2回**（React StrictModeがdevでeffectを二重実行するため）
3. ~17s: `Promise.all` 完了 → Appがマウント → **単一8.5秒のロングタスク**
   （ReplayScene構築: 手続き型トラック生成＋衛星グラウンド192×192=約37k頂点の
   ドレープ計算等。SwiftShaderのシェーダーコンパイル分も含む）
4. 21.6s: 最初の描画。25.9s: フルシーン。
5. その後: satellite.jpg 3.4MB / terrain.png 3.5MB / features3d.json 1.0MB /
   RX3_race.glb 1.4MB が到着し順次差し替え。

### 本番previewでも残る問題

- HEADプローブの遅さはpreviewでも再現（shizuoka 5.8秒）。ファイルサイズに
  概ね比例しており、Windowsのコールドディスク読み＋Defenderスキャンが原因の
  可能性が高い。**実CDN/静的ホスティングではHEADは速いはずだが、
  「29MB級ファイルへのHEADで初期表示をゲートする」設計自体が脆弱**。
- 衛星テクスチャのフェッチ開始が**プローブ完了までずれ込む**
  （prod実測: プローブ完了6.8s直後の7.06sにsatellite.jpg開始）。

## 2. 原因の整理（寄与度順）

| # | 原因 | 寄与（富士・コールド） | 場所 |
|---|------|------|------|
| 1 | バリアントHEADプローブが初期ロードをブロック | dev 9s / prod 6s | `dataLoader.ts` `loadInitialReplay()` の `Promise.all` + `satelliteVariants.ts` `probeSatelliteVariantsForTrack()` |
| 2 | dev server固有: モジュール変換とデータ配信の競合 | 〜8s（devのみ） | vite dev。本番buildでは消滅 |
| 3 | トラック構築が同期でメインスレッド占有 | dev 8.5s（実GPUでは数秒程度と推定） | `TrackBuilder.ts` `buildSatelliteGround()` のドレープループ（37k頂点 × `projectPointToCenterline`）ほか |
| 4 | 大判アセット | 初期セット約10MB（sat 3.4 + terrain 3.5 + features3d 1.0 + 車 1.4 + JS 0.4gz）。shizuoka選択時+29MB、コリドーx2選択時+36MB | `public/data/tracks/fuji/` |
| 5 | StrictModeの二重フェッチ | devのみ、プローブ・JSON全部×2 | `main.tsx` / `App.tsx` |

補足（問題でないもの）: scene.glb 40〜56MBは「Drive on AC」有効時のみの遅延
ロード済み。imagegen_trials 129MBはトグルONまで未取得。ラップ切替もシーン
再構築なし（`setLap` 分離済み）。features3d.json はpreviewのgzipで178KBに縮む。

## 3. 改善方針

### P0 — プローブをクリティカルパスから外す（効果最大・変更最小）

1. **`loadInitialReplay()` の `Promise.all` からプローブを外す**。
   - 案A（推奨・恒久策）: パイプラインで各トラックに `variants.json`
     （利用可能バリアントの静的マニフェスト）を生成し、1つの小さなGETに置換。
     HEADプローブ機構は不要になる。
   - 案B（暫定・アプリ側のみ）: プローブは投げっぱなしにして結果は後から
     ストアへ反映（UIのバリアントボタンが数秒遅れて活性化するだけ）。
     初期バリアントの存在確認は、テクスチャロード失敗時に default へ
     フォールバックすることで代替（`loadOrApplySatelliteVariant` の
     onErrorに1分岐追加）。
   - 期待効果: 富士の初期3D表示が **dev −9s / prod −6s**。
2. **体感評価・デモは本番ビルドで行う**（`npx vite build && npx vite preview`）。
   dev serverはDCLまで+7秒、データ配信遅延も加わり、体感が3倍悪い。

### P1 — アセットの軽量化（転送とデコードのヒッチ削減）

3. **terrain.png（3.5MB, 1756×1795 RGBA）**: 高さ場なので16bitグレースケール
   PNG化＋必要なら解像度半減で1MB未満に。読み込み側のデコード対応が必要。
4. **satellite_shizuoka.jpg（29MB, 8014×8192）**: 初期表示は4096版（≈7MB）を
   使い、選択後にフル解像度へ差し替える2段ロード。または品質を保った
   WebP再エンコード（−40〜60%）。8192²テクスチャはGPUアップロード＋
   ミップ生成で数百ms〜秒級のヒッチになるため、恒久策は**KTX2/Basis**
   （GPU圧縮のままアップロード、VRAMも1/4〜1/6）。
5. **`THREE.ImageBitmapLoader`（createImageBitmap）への切替**で大判JPEGの
   デコードをメインスレッド外へ。バリアント切替時のカクつきにも効く。
6. 実ホスティングでは **gzip/brotli圧縮＋長期キャッシュヘッダ** を確認
   （features3d.json 1MB→178KB実証済み）。`<link rel="preload">` で
   track.json / laps.json / satellite.jpg をJSパース中に先行取得。

### P2 — 構築処理のオフロードと体感演出

7. **ドレープ計算のWorker化 or 事前計算**: `buildSatelliteGround` の
   37k頂点ループ（roadY/terrainY/lateralDistance）を (a) Web Workerで計算して
   結果のFloat32Arrayだけ受け取る、または (b) パイプラインでバイナリ
   サイドカー（.drape.bin）として事前計算し fetch するだけにする。
   トラック追加時の再現性の観点では (b) が本命。
8. **進捗表示**: 現状は「Loading replay data...」の静的テキスト→黒画面→
   突然表示。フェーズ表示（データ取得→地形構築→テクスチャ）や
   フォールバック地面の早期表示だけでも体感は大きく改善する。
9. （任意・devのみ）StrictMode二重実行対策として `loadInitialReplay()` に
   モジュールレベルのプロミスキャッシュを持たせる。

## 4. 検証メモ

- 計測生データ: セッションscratchpadの `profile-result.json`（dev）/
  `profile-prod.json`（preview）。スクリプトは既存 `perf_profile.mjs` を無改変で使用。
- dev serverはユーザー起動中の5199を利用。previewは5299で新規起動
  （`vite build` は tsc をスキップして実行、46.5sで成功）。
- 未検証: 実GPU環境での構築ロングタスクの実測値（SwiftShader計測のため
  過大評価の可能性）、実CDN上でのHEADプローブ所要時間、P0改善後の再計測。
