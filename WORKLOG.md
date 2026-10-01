# 作業ログ (WORKLOG)

運用ルール:
- 新しいエントリを**一番上**に追加する(逆時系列)。
- 1エントリ = `## YYYY-MM-DD HH:MM — タイトル` + 箇条書き(やったこと / 作った・変えたファイル / 未解決)。
- セッションの区切りや大きな作業の完了時に Claude が追記する。

---

## 2026-10-01 21:37 — GPS原因追及と精度改善の実施（9/22開始、完了）

- 依頼/目的: 富士CGとGPSのズレの原因を追い、確認できる実装誤差を修正して精度向上施策を完遂する。アンテナは両日共通、車両はMazda2諸元、映像とロガーはおおむね同期というユーザー情報を作業前提とした。追加質問「これらはGit管理か」についても実態を確認した。
- 2026-09-22 15:30以降の実施順: 変更前データを `artifacts/gps-accuracy-2026-09-22/baseline/` に保存→ラップ端GPSの前後文脈を使う平滑化へ修正→出典とライセンスを記録した静岡県オルソだけで画素中心/局所白線候補を修正→Mazda2車輪寸法を推定/描画に共通化→実道路world境界へのラップ単位位置合わせと配布入力SHA失効検査を追加→動画タイマー/元XRKパケット時計を監査→全10周・半周CV・実Chromeを評価した。Googleタイルは処理していない。
- 2026-10-01 21:20以降の追加判断: 中心線の最近傍station切替で境界評価が1mmの車両移動に対し約23cm飛ぶ例を確認し、道路/縁石の描画線分への連続距離でPython推定とChrome評価を統一。保存した補正値（小数3桁）で配布JSONの評価値を再算出。全境界からの最近傍距離を総当たり計算した400点監査でも最大誤差は約5.6e-14m。
- 証拠/結果: ラップ端処理由来変位の最大4.928→0.0316m。補正セッションは7/29（東−1.627,南+1.931）m、7/30（東−0.221,南−2.680）mで差4.821m。全10周の実Chrome道路外超過はwet24.2→3.7秒、dry51.8→6.5秒。半周hold-out 20/20改善、中央値93.6%、最小53.0%のコスト減。同じアンテナ仮定を両日に使う例と両日同じ路面条件でも4m超の日差が残る。これらはトレース道路への整合性であり、真位置誤差や日差の固有原因の証明ではない。映像タイマーとログの同期はおおむね成立したが、カメラ未校正のため独立したメートル級距離真値には使えない。GPS時刻の任意のシフトと時間変動位置補正は証拠不足で不採用。
- 主な変更/生成ファイル: `pipeline/build_race.py`, `pipeline/register_gps_to_track.py`, `pipeline/test_*.py`, `scripts/quality/build_fuji_cg.py`, `scripts/quality/road_edge_candidates.py`, `scripts/quality/verify-fuji-gps-registration.mjs`, `src/replay/gpsRegistration.ts`, `src/replay/dataLoader.ts`, `public/data/vehicles/mazda2-dj.json`, 富士2レースの`laps.json`/全10lap JSON/`gps_registration.json`, `public/data/tracks/fuji/cg_study/geometry.json`, `docs/fuji-gps-accuracy-improvement-2026-09-22.md`, `docs/policy-gps-cg-alignment-2026-09-20.md`, `docs/README.md`, `docs/viewer.html`, `BACKLOG.md`, `artifacts/gps-accuracy-2026-09-22/`の監査記録とスナップショット。
- 検証: Python pipeline 33件・道路候補3件、Vitest 48ファイル368件、本番build成功。実Chrome全10周0.1秒刻みの位置/車輪/元GPS切替、ハッシュ失効、ページエラー0、66画面を確認。配布入力SHAと保存補正の評価値を全件再照合し `verification-manifest.json` に保存。DocsViewerライブ版とローカルスナップショット（35 md + 24 html）を再生成した。
- Git状態/未解決: リポジトリ自体はGit管理。旧ラップJSONや`pipeline/build_race.py`等は追跡済みで変更中だが、新しい文書・登録スクリプト・補正JSON・車両プロファイル・生成`artifacts/`等は未追跡。2026-10-01 21:37の調査完了時点ではステージ/コミットしていない。`artifacts/`には動画由来画像や大きい中間データを含むため一括追加しない。DocsViewerのArtifact再公開はこのCodex環境では対応ツールがなく、既存`.docsviewer-url`先は今回のスナップショットへ更新できていない。絶対精度の確定にはアンテナ実測、カメラ校正、独立測量/高精度GNSSが必要。

## 2026-07-23 — ランドマークビルダー（グランドスタンド/キャノピー）実装

- ユーザー方針「グランドスタンド風アセットを配置できるビルダー。検討=Fable、
  実装=Opus」。Fableが既存資産を調査（features3dのタグ経路・押し出し基盤が
  流用可、grandstandタグ1棟/roofタグ3棟）し設計、Opusサブエージェントが実装。
- 新規 `src/engine/track/LandmarkBuilder.ts`: 最小外接矩形+中心線ヒントで
  向きを決め、grandstand=観客席ティア6段(色ストライプ)+背面壁+柱付き屋根
  スラブ、roof=柱+屋根のみのキャノピー。Features3DBuilder がタグで分岐、
  全建物で「メッシュ1+輪郭線1」の2ドローコール維持。
  buildFeatures3dGroup に省略可能な frontHintAt を追加（互換維持）。
- 検証: tsc 0エラー、全35ファイル308テストPASS（+4本）、Playwright実画面で
  S/F左手に「段々の観客席+片持ち屋根」を確認（Temp/landmark-t4.png）。
  コミット 8a33edb。
- 未解決: 屋根/座席のトゥーンテクスチャ化（任意）、他トラックへの適用
  （タグがあれば自動で効く）。

## 2026-07-23 — 建物地形修正ステップ3（3D箱の建物化+ユーザー表示ON）完了

- Features3DBuilder.buildBuildings を強化（コミット 5eacdf8）:
  ①トゥーン風シルエット（EdgesGeometry の輪郭線、+1ドローコールのみ）
  ②castShadow 有効化（地面への落ち影で設置感）
  ③壁の基部を暗くする頂点AO ④日陰面が真っ黒になるのを防ぐ微小emissive。
  戻り値 Mesh→Group 化に伴いテスト更新（全304テストPASS、tscエラー0）。
- USER_VIEW_LAYER_PRESET の showFeatures3d を true に変更 — ステップ1/2で
  写真の屋根を除去した今、3D箱が唯一の建物表現のため必須。
- 実画面確認: S/Fストレートで「写真の丘」だった左手が灰色の建物ボリューム+
  輪郭線に置換。右手の並木・ピット建物も良好（Temp/step3c-t4.png）。
- 残る磨き込み候補（任意）: 屋根へのトゥーンテクスチャ（Gemini数枚≈$0.5）、
  グランドスタンド観客席テラスの表現、SR/トゥーンタイル内の建物像の扱い、
  鈴鹿・岡山への同処理適用。

## 2026-07-23 — 建物地形修正ステップ2（衛星写真から屋根をインペイント除去）

- 新規 `pipeline/neutralize_building_roofs.py`: features3d の91フットプリント
  （2mバッファ+北西6mの焼き込み影ストリップ）をマスクし、周囲から
  インペイント（TELEA+ソフト化）。対象は satellite_shizuoka.jpg と 4k webp
  （バックアップ *.pre-neutralize.*）。コミット e399bd5。
- 実画面確認（t=4）: ピット・小建物の屋根写真は消滅。S/F左手の段々の稜線は
  **グランドスタンドの観客席テラス**で、地形としては実在（座席段が正しく
  地表扱い）+座席の写真テクスチャのため大きくは変わらず。屋根キャノピー分は
  ステップ1+2で除去済み。
- 既知の限界: インペイント跡は至近距離では灰色スマッジ（走行距離では目立たず）。
  SRコリドータイル・トゥーンタイル内の建物は未処理（ステップ3の3D箱と
  合わせて要判断）。
- 未解決: ステップ3（3D箱の建物らしさ強化+ユーザーモードでの表示方針）。

## 2026-07-23 05:45 — P1起動最適化: ベース地面テクスチャ(satellite_shizuoka)のWebP軽量化

- 依頼: 起動時に取得する29.8MBのベース地面テクスチャ
  `satellite_shizuoka.jpg`(8014×8192, ~0.22m/px)を縮小/再圧縮して起動を軽くする。
  **原本は残し、原本↔最適版を比較できるようにする**。
- 生成した最適版: `public/data/tracks/fuji/satellite_shizuoka_4k.webp`
  (4007×4096, WebP q80, method6, **5.20MB** ← 29.76MBから約5.7分の1)。
  Pillow 12.2.0(`pipeline/.venv-sr`)でLanczos縮小。原本jpgは無変更で保持。
  併せて `satellite_shizuoka_4k_meta.json` を新設(bboxは原本と同一、
  imageWidth/Height=4007/4096, derivedFrom注記, effectiveRes~0.44m/px)。
  ※ satelliteの地面ジオメトリはbboxのみ使用(TrackBuilder buildSatelliteGround)。
  imageWidth/HeightはTerrainSamplerが別ファイル(terrain_meta)で使うだけで、
  satellite画像の画素寸法は幾何に影響しない → 寸法変更は安全と確認。
- 配線 (`src/replay/satelliteVariants.ts`):
  - variant "shizuoka" と "shizuoka_x2"(ユーザー既定=fuji preset)の filename を
    webp / meta を4k_metaへ変更 → **ユーザーモード起動は自動でwebpを取得**。
  - 比較用に新variant **"shizuoka_orig"**(label「静岡 20cm 原寸」)を追加。原本jpgを指す。
    SatVariantId型・SAT_PARAM_TO_VARIANT に追加。
  - **原本への切替え方法**: devモードでURLに `?dev=1&sat=shizuoka_orig` を付ける
    (例 `http://localhost:5199/?track=fuji&race=fuji_aim_01&dev=1&sat=shizuoka_orig`)。
    通常ユーザーモードは常にwebpを表示。
  - three.js TextureLoaderはWebP対応(拡張子ハードコード依存なし)を確認。
- 変更ファイル: satelliteVariants.ts, satelliteVariants.test.ts(期待値更新),
  新規 satellite_shizuoka_4k.webp / satellite_shizuoka_4k_meta.json。
- 検証(実施済み): `npx tsc --noEmit` 通過、`npx vitest run` 35ファイル304テスト成功。
  Playwright(:5199, `Temp/verify_sat_optim.mjs`):
  - **ユーザーモード起動でwebp(5.20MB)を1回取得のみ、jpgは取得0、consoleエラー0**。
  - devモードの原本比較でjpg(29.76MB)取得を確認(比較用途)。
  - 削減量: 起動クリティカルパスから **約24.6MB削減(~82.5%)**。ローカルdev鯖のため
    秒数は代表性低いが、初回レスポンス開始はwebp t≈383ms(jpgは全転送が重い)。
  - 証跡: `Temp/sat-opt-top.png`/`sat-opt-chase.png`(最適版)、
    `sat-orig-top.png`/`sat-orig-chase.png`(原本)、`Temp/sat-optim-report.json`。
    ※chase比較はdevプリセットの追加レイヤ(3D地物/木)差でシーンが異なる点に注意
    (ベーステクスチャ単体の差ではない)。
- 画質評価(正直): 2倍ダウンスケール+webp q80で、原本対比 PSNR≈27.3dB /
  平均絶対差 7.7/255(~3%)。**高周波の細部(個々の木・地面のざらつき)は softening**
  するが、色調・大局形状はほぼ保存。このベース写真は走路±51〜140mでSRトゥーン
  コリドーに不透明に覆われ、実際に見えるのはコリドー外の遠景のみ。遠景は視距離が
  大きくソフト化はほぼ知覚不能で、通常視聴では劣化は目立たない。
- 未解決: 本番previewビルドでの総転送量再実測、原本jpgのgit LFS/除外方針は未検討
  (現状は両ファイルとも public に存在)。コミットは未実施(依頼どおり)。

## 2026-07-23 — チェックポイントコミット + 建物地形修正ステップ1（DTM平坦化）

- ユーザー指示「全部コミットしてロールバック可能にしてから着手」→
  Temp/ を .gitignore に追加のうえ全1,236ファイル(約1GB)をコミット
  （b7d56e9 + 23208ce）。git identity をローカル設定（yugovic）。
  ロールバックは `git reset --hard 23208ce`。
- ステップ1実装: 新規 `pipeline/flatten_buildings_dtm.py` —
  features3d.json の建物フットプリント内の DTM セルを外周リング(5px)の
  地表高中央値で置換（2pxバッファ+3pxフェザー、bump<0.3mはスキップ）。
  出力は同じ terrain.png（Terrarium）でビューア無改修、バックアップ
  terrain.pre-flatten.png。コミット b55caa5。
- 結果: 21/91棟に混入bump検出・平坦化（最大=メイングランドスタンド
  19,673セル・平均1.68m）。実画面 t=4 前後比較（Temp/flatten_compare_t4.png）
  で左手の稜線が低減。ただし**残る"山"の大部分は実地形の斜面+写真の
  屋根ドレープ**であり、ステップ2（屋根像の除去）・3（3D箱仕上げ）で
  本命の改善が出る見込み。
- 未解決: ステップ2/3 の実装、鈴鹿・岡山への適用要否。

## 2026-07-22 12:10 — 「建物が丘に見える」問題のbest-of-N提案（Opus×3並列）

- ユーザー依頼「建物が山のようになる地形の改善方法を複数案で（/bestofn 相当）」。
  Opusサブエージェント3体（前処理/ランタイム/後処理AI）を並列でコード調査させ
  統合。**真因=VS2019 LPの2クラス分類で大型屋根が class2(地表) に誤混入し
  DTM自体が隆起 + その丘にオルソ屋根がドレープ**。既存資産（建物footprint
  91件 + Features3DBuilder の3D箱押し出し）で解決可能と判明。
- 統合提案を `docs/proposal-building-terrain-2026-07-22.md` に保存
  （案カタログ A1-A3/B1-B4/C1-C4 の比較表、推奨=A1 DTM平坦化→A2 屋根像除去→
  B3+C1+C3 仕上げ、総工数3〜5日・API費$5未満）。README索引更新、
  DocsViewer再公開（25md+2html、同URL）。
- 未解決: 実装着手判断（推奨は A1 から）。

## 2026-07-22 09:56 — OFFレイヤ資産の遅延ロード実装＋衛星写真の用途調査

- 依頼A（実装）: 前回レポートで指摘した「ユーザーモード起動時にOFFレイヤの資産を
  常時取得」する無駄を解消。各資産をレイヤが可視になったとき初めてfetchし、以降の
  トグルでは再取得しない遅延ロードへ変更。dev既定ONのレイヤは従来どおり起動時に
  ロード。fuji以外（ファイル欠損）はcatchで無害化を維持。
- 対象資産とレイヤ対応: features3d.json→3D Features、features.json→OSM Features、
  road_edges.json+overlays.json→Track lines、ac_overlay.json→AC Overlay/Drive on AC。
- 変更ファイル:
  - `src/engine/track/TrackBuilder.ts`: `ensureOsmFeatures/ensureFeatures3d/
    ensureTrackLines` を新設し、対応する `setFeaturesVisible/setFeatures3dVisible/
    setTrackLines` から可視化時のみ起動（*Requestedガードで冪等）。features/features3d
    はfeatureDrape（衛星＋地形ロード後）依存のため、drape確定時に可視なら再起動。
    road_edgesを初期 `Promise.all` から除去、overlaysの常時ロードブロックも撤去し
    `ensureTrackLines` へ集約。scene.glb遅延ロードの既存イディオムに倣った。
  - `src/engine/ReplayScene.ts`: `loadAcOverlay` を `ensureAcOverlay()`（冪等）へ移し、
    `setAcOverlayVisible(true)` と `setDriveOnAc(true)`（groundedYのリボンfallback用）
    から遅延取得。load()では取得せずフラグリセットのみ。
  - `src/ui/CreditOverlay.tsx`: 起動時に残っていた features3d.json 1.06MB 取得の
    真犯人。クレジット表示のため `source` 文字列だけ欲しいのに全体をDLしていた。
    `showFeatures3d` が真のとき（=実際に3D地物を表示中）のみ取得するようゲート。
    トグルONで即クレジット再解決。※前回レポートはこの取得を TrackBuilder:953 と
    誤認していた（TrackBuilder側は別経路）。
- 検証（実施済み）:
  - `npx tsc --noEmit` 通過、`npx vitest run` 全35ファイル304テスト成功。
  - Playwright（:5199 dev、`Temp/verify_lazy_layers.mjs`）: ユーザーモード起動で
    features3d/overlays/road_edges/ac_overlay/features の5件とも**取得0**（startupPass
    true）。各レイヤをストア経由でONにすると対応ファイルを取得、Track linesで
    overlays.jsonも同時取得、consoleエラー0。証跡 `Temp/lazy-user-startup.png`,
    `Temp/lazy-toggle-*.png`, `Temp/lazy-verify-report.json`。
  - devモード起動（`Temp/verify_dev_startup.mjs`）: ac_overlay/features3d/overlays/
    road_edges を起動時取得（dev既定ON）、features.json のみ未取得＝dev preset の
    showOsmFeatures:false と一致（回帰でない）、consoleエラー0。証跡
    `Temp/lazy-dev-startup.png`。
- 削減量（ユーザーモード起動の重要パスから除去、非圧縮）: features3d 1,056,438 +
  ac_overlay 61,623 + features 60,695 + road_edges 49,519 + overlays 1,188 ＝
  約1.23MB（うち約1.06MBがfeatures3d）。ネットワーク総量の再実測（cold/warm）は
  未実施（未解決）。
- 依頼B（調査のみ・衛星写真の用途）: satellite_shizuoka.jpg は地面ベース
  テクスチャ（8014×8192、静岡20cm オルソ、bbox約1.75×1.79km、~0.219m/px）。
  ±51mのトゥーンコリドーSRタイルはその上に重畳（下の写真ピクセルは隠れるが取得済み）。
  コリドー外の遠景・山はこの1枚が唯一のソースで、地形ハイトフィールドにドレープ。
  詳細は本セッション回答に記載。satellite取り扱いは無変更。
- 未解決: 本番previewビルドでの総転送量の再実測。CreditOverlayのfeatures3dは表示中
  のみ取得＝3D地物OFF時はhoverクレジットに Features 行が出ない（表示中データのみ
  帰属という整理）。

## 2026-07-22 11:30 — t=52 イン側の緑エプロン修正（境界バグ特定）

- ユーザー指摘「52秒地点のイン側が緑」。調査は難航（タイル再生成・スナップ
  拡張・カバレッジ追加のどれでも画面が変わらず）。レイヤー分離テストで
  トライアルタイル由来と確定後、**真因=窓028のエプロンが「色相ちょうど50の
  純緑」で、スナップ条件（緑=hue>50 / オリーブ=hue<50）の境界をすり抜けて
  いた**ことを特定。
- 実施修正（gemini_toon_corridor.py）: ①スナップ範囲を重複させ境界穴を封鎖
  （緑=hue≥45） ②芝上のくすんだオリーブ影を平坦領域ガード付きで #BCCF25 に
  フラット化（明度保存写像では暗いまま残るため明度ごと統一） ③コカ・コーラ/
  ヘアピン内側のカバレッジ穴へ補充窓5枚（corridor_100/101/200-202、
  重なり0.97〜1.0） ④stitchアルファ立ち上がり8倍（フェザー帯の実写透け防止）
  ⑤019-021 を #BCCF25 ピン留め再生成。
- 検証: 3時点比較 `Temp/apron_progress.png`（鮮やかな緑→黄緑に統一）、
  スナップ単体テスト（028: greenish 20%→0%）、93グリッド走査で残存緑1枚1.8%のみ。
- 教訓: 生成AIのフラット色は特定の色相値に集中する。**色域フィルタの境界は
  必ず重複させる**。デバッグは最初にレイヤー分離テストをやるべきだった。
- 未解決: エプロンにわずかな明度勾配（許容か要確認）。ab比較ページ/レポートの
  画像が旧版のまま。

## 2026-07-22 09:13 — 起動パフォーマンス実測レポート（Playwright）

- 依頼: Webアプリの起動が遅い原因を実測し、日本語HTMLレポート化（測定のみ・
  アプリ改変なし）。対象は重量級アセットのある Fuji（`?track=fuji&race=fuji_aim_01`、
  ユーザーモード）。既存の :5199 dev サーバを利用（本タスクでは新規起動せず）。
- 計測: `Temp/measure_startup.mjs`（Playwright/Chromium, resource+navigation timing、
  `.loading-screen`消滅で判定、networkidle+4s静定）を cold/warm 2回実行。
  生データ `Temp/startup-{cold,warm}.json`、集計 `Temp/analyze.mjs`。
- 主な実測知見:
  - 支配要因は1ファイル `satellite_shizuoka.jpg` 29.76MB（総転送41.6MBの約72%）。
    DL約2.0s、しかも開始が t≈1.5s（JSON直列依存後）。地面が正しく出るまで cold 約3.5s。
  - スピナー(onReady)は初フレームで約0.2s消滅＝体感遅延の実体は地面ポップイン。
  - 起動時に取得0件を確認（懸念の否定）: scene.glb(40MB)/scene.mod.glb(56MB)、
    imagegen_trials(440MB,91枚)、corridor_x2 SRタイル(141枚)、他衛星バリアント。
    それぞれ遅延ロード/視錐台ゲート/プローブ無効化で回避済（2026-07-17指摘の
    HEADプローブブロックも解消を確認）。
  - 無駄: features3d.json 1.06MB を showFeatures3d:false でも常時取得
    （TrackBuilder.ts:953）。overlays/road_edges/ac_overlay/features の小JSONも
    OFFレイヤなのに取得。dev の StrictMode で各JSONが2〜4回fetch（本番非該当）。
  - 削減候補 P1=衛星軽量化(−18〜22MB/−1.2〜1.5s)＋取得並行化(−0.5〜1s)、
    P2=features3d遅延化(−1.06MB)/terrain軽量化、P3=OFFレイヤ小JSON遅延化。
- 作成: `docs/report-startup-performance-2026-07-22.html`（自己完結・要旨・
  ウォーターフォール・表・コード根拠・削減候補）。docs/README.md 索引更新。
- 検証: cold/warm 2回の実測値と TrackBuilder/ReplayScene/dataLoader/replayStore の
  コード照合（file:line 引用）で各主張を裏付け。dev サーバ計測のため JS転送量と
  二重fetchは本番非該当と明記。本番previewでの再計測は未実施（未解決）。
- 未解決: 本番ビルドでの再計測、P1施策の実装・効果検証。

## 2026-07-22 08:30 — 窓005色決め打ちトライアル・前後比較ページ・総括レポート

- 開始4秒地点の濃緑帯（窓005、17窓リストから漏れ）を「芝は #BCCF25 決め打ち」
  プロンプトで再生成（重なり0.98）→ 実画面で周囲と一致を確認。
  色の16進指定が相対指定より確実と実証。#BCCF25 は良好窓034の実測中央値。
- 前後比較ページ `toon_corridor/compare.html` 新規（実画面t=4sの前後 +
  修正19窓の前後表、修正内容注記つき）。改善前はgen_sheet.jpgから切り出し。
- 総括レポート `docs/report-toon-corridor-gemini-2026-07-22.md` 作成
  （経緯表・パイプライン構成・実証結果・コスト≈$10・残課題7件）。
  README索引更新、DocsViewer再公開（24md+1html、同URL）。
- 追記: 実画面ベースの比較要望を受け `toon_corridor/ab/index.html` 新規 —
  同一時刻・同一カメラで「実写(Trial tiles OFF) vs トゥーン(ON)」を6地点
  （Top俯瞰+チェイス5点）並置、hoverでその場A/B切替。Playwrightで全画像
  ロード確認済み。
- 未解決: レポート§6の残課題（パレットスナップ恒久化が最優先推奨、
  006/007色ドリフト、024/021精査、路面ロゴ仕分け、コース外遠景、
  ワンタッチ化、鈴鹿・岡山横展開）。

## 2026-07-22 07:45 — トゥーンコリドー磨き込み: 黒帯解消・色統一・路面クリーン化

- ユーザー指摘「つなぎ目の黒帯が不快」→ 原因はランタイムのアルファフェザー
  越しに下の写真地面が透けること。`gemini_toon_corridor.py stitch` を新設し、
  59窓をオフラインで1枚に合成（トゥーン同士でクロスフェード）→ 重なりの無い
  不透明1024pxグリッド91枚に切り直し。黒帯・Topの市松模様とも解消を実画面確認。
- ユーザー決定（黄緑維持/車除去は走行路面上のみ/影は路面上のみ除去）を受け、
  該当17窓（濃緑芝9・路面影7・路面上車3、重複あり）をクリーン化プロンプトで
  再生成（全成功、重なり0.94〜1.00）→ 再stitch。Top/チェイスで濃緑パッチ・
  路面横断影・路面上車両の解消を確認。
- 途中2回 Gemini 支出上限429で中断、ユーザーが都度引き上げ。コスト累計≈$8。
- 検証: Playwright実画面（Temp/final-top.png, final-chase-52.png）、
  consoleエラー0。素のトゥーン表示は 3D Road/AC Overlay/Detail Texture/
  Track lines をOFF。
- 未解決: コース外遠景の写真とのトーン差（全面展開は別判断）、
  gen_sheet の再精査（軽微な残置物）、docs計画書への結果反映、
  トゥーン表示のワンタッチプリセット化（レイヤー4つ手動OFFが必要）。

## 2026-07-22 03:20 — 全周トゥーンコリドー完成（Gemini、59窓）

- ユーザー承認「flash-lite でなく 3.1-flash 採用版で、トラック周辺を中心に生成」
  を受け、新規 `pipeline/gemini_toon_corridor.py`（prepare/run/finalize）。
  中心線に沿い 76.8m 間隔で 512px 窓 59 個（128px オーバーラップ、周長4,556m）。
- run: 59/59 成功（呼び出し67回=再試行8回、エッジ重なりゲート0.90、
  自動再試行で良い方を採用）。ゲート未達残は corridor_024=0.79, 021=0.87 の2窓。
- finalize: 96px 線形アルファフェザー付与 → imagegen_trials/manifest.json を
  59タイルで再構築（provenance に edgeOverlap 記録）。
- Playwright実画面: チェイス視点は完全なセルルック（グレー路面・赤白縁石・
  緑芝帯・白線）を確認（`Temp/corridor-clean-52.png` 等）。素のトゥーン確認は
  3D Road / AC Overlay / Detail Texture / Track lines を OFF にする。
  consoleエラー0。
- 既知の課題: ①Topビューでタイル重なり部に市松状の暗パッチ（フェザー領域で
  写真地面と半透明合成されるため）②コース外遠方は写真のまま（コリドー幅
  ±51m）③024/021 の低ゲート窓の目視確認未了。
- コスト: 67呼び出し ≈ $4.5。Gemini累計 ≈ $7。

## 2026-07-22 02:30 — Geminiトゥーン化パイロット: 位置忠実で12地点合格、Replay表示まで到達

- Stability不合格を受け Gemini API へ切替（画風の実績がある側）。新規
  `pipeline/gemini_toon_pilot.py`（gemini-3.1-flash-image、src+スタイル参照
  +地物固定プロンプト、temperature 0、manifest記録）。APIキーは `~/.gemini_key`。
- スタイル参照は `ai_illustrated_tile_nologo.png`（Gemini製の良品からロゴ部を
  切除。初回はロゴが全タイルへ転写されたため）。
- **結果: 位置忠実度は数値合格** — 位相相関シフト全12タイル≤0.4px@512
  （≈0.16m）、エッジ重なり率91〜100%。重ね合わせ画像 `pilot/gemini/overlay_check.jpg`。
  Stabilityの「トラック描き替え」は発生せず。
- 幻覚の潰し込み: tile02の架空F1ロゴ→プロンプト強化v4で解消（重なり98%）。
  tile12の「架空の橋/看板」疑い→重ね合わせで実在の方形パッチと判明（97%、幻覚
  ではない）。v2→v4/v3差し替え済み。
- **Replay表示**: imagegen_trials/manifest.json を12枚のGeminiタイルで再構築
  （旧版は manifest.pre-gemini.json 退避）。Trial tilesトグルで表示、Playwright
  でTop/チェイス確認、consoleエラー0。
- モデル比較: gemini-3.1-flash-lite-image（約1/3価格）を3枚試行 — 09/11は
  重なり1.00で健闘（芝の緑はむしろ好色）、02は色ブロブ幻覚で不安定。
  全周展開時に再判断。
- コスト実測: 1枚$0.067（出力1120tok×$60/M）。累計約$2.4/33枚。全周展開見積
  $6〜7.5（バッチで半額可）。ユーザーがspend cap引き上げ済み。
- 比較ページ: `pilot/gemini/compare.html` 新規（12地点×4列=元写真/採用トゥーン
  (hoverでA/B)/重ね合わせ/lite、overlap数値・注記つき、ライトボックス）。
  全採用タイルの overlay_XX.png と compare_stats.json も生成。Playwrightで
  全画像ロード確認済み。
- 未解決: 全周展開の設計（1024px窓+オーバーラップ+境界フェザー、タイル間
  色統一）、コース外の広域植生の扱い、docs計画書のGemini結果反映。

## 2026-07-22 01:15 — Stabilityトゥーン化パイロット Day1: 12地点×3設定スイープ完了

- 計画（docs/plan-stability-toon-pilot-2026-07-22.md）の Phase 0〜1 を実施。
  APIキーは `~/.stability_key`（リポジトリ外）に保存、残高1000クレジット確認。
- 新規 `pipeline/stability_toon_pilot.py`（seed固定・manifest記録・--max-calls
  ハードリミット・失敗時はAPIエラー本文を記録）。
- 試し撃ち結果: **Structure Control は構造全壊で不採用**（別の場所を創作）。
  **Style Transfer（Gemini画風参照+composition_fidelity 0.95）が本命**。
- 本スイープ: 12地点 × style_strength 0.3/0.5/0.8 + 再現性テスト = 37呼び出し
  全成功、288クレジット（≈$2.9）。**同条件2回の出力は画素差0（決定論確認）**。
- 成果物: `pilot/stability/style_*/pilot_XX_gen.png`、`manifest.json`、
  `contact_sheet.jpg`（src vs 3設定の12行×4列）。
- 所見: 道路ジオメトリは全設定でおおむね保持。0.3〜0.5が画風良好、0.8は
  くすみ+文字幻覚増。課題=①赤ロゴ→赤い車の幻覚（tile09、プロンプト禁止でも
  再発）②架空スポンサー文字の創作 ③赤白縁石の単色化 ④森林タイル(11)が
  舗装+芝に置換される大幻覚。①②③はベクター上書きで吸収可能、④は
  コース外タイルの扱い要検討。
- **判定（ユーザーレビュー）: 不合格**。全設定でトラック自体のジオメトリが
  描き替わっており（白線本数・位置、ランオフ形状、芝/舗装境界、歩道創作）
  使用不可。機械ゲート評価は省略（目視で明白なFAIL）。Stability API 経路は
  ここで打ち止め（計画の判定分岐: 位置FAIL → ローカルControlNet or ベクター）。

## 2026-07-21 23:55 — 次方針提案のドキュメント化とDocsViewer公開

- ロールバック後の方針整理を `docs/proposal-toon-next-steps-2026-07-21.md` に
  保存（要旨つき: 経緯3方式の失敗表 / A=Stability APIパイロット・B=ベクター
  描き起こし・C=ローカルControlNet の3案と推奨実行順）。docs/README.md 索引更新。
- DocsViewer: viewer.html 更新+スナップショット再公開
  （https://claude.ai/code/artifact/0fc6a284-c2f9-4ef0-afa1-03f4753324eb 、
  22 md + 1 html）。
- 2026-07-22 追記: ユーザーとの議論を受け §4「Gemini 1枚テストの位置づけ」
  （画風は証明済み・未証明は再現性/連続性/位置精度の量産3課題）と §5
  「Stability AIの強み」（構造拘束・seed決定論・ローカル移行路・Style Transfer
  で Gemini画風×Stability量産のいいとこ取り）を追記。パイロットは
  「Gemini そのまま / Stability Style Transfer」の2系統比較に修正。
  DocsViewer 再公開（同URL）。
- 2026-07-22 追記2: Stability前提の具体計画を
  `docs/plan-stability-toon-pilot-2026-07-22.md` として作成（既存ImageGen
  12枚キット流用、2系統×3設定=72枚・$4〜6・2日、判定分岐・全周展開見取り図・
  リスク表つき）。README索引更新、DocsViewer再公開（同URL、23md+1html）。
- 未解決: APIキー取得（ユーザー作業）と着手GOの判断待ち。

## 2026-07-21 23:40 — トゥーンシェーディング全面ロールバック（品質不足で断念）

- ユーザー判断「ロジックベースToonは品質が低すぎるので諦める。ロールバックを」
  に対応。シェーダ版（Toon groundトグル一式）と焼き込みバリアント（下記エントリ）
  の両方を削除。
- 削除したコード: groundDetail.ts の TOON_GLSL/uToonStrength（+test）、
  TrackBuilder/ReplayScene/ViewerCanvas の setToonEnabled 連鎖、replayStore の
  showToon/toggleToon、LayersPanel「Toon ground」行、satelliteVariants の
  "toon" バリアント（+test）。
- 削除したファイル: pipeline/toonify_ortho.py、
  public/data/tracks/fuji/satellite_toon.jpg / _meta.json、Temp/verify-toon-*。
- 検証: tsc --noEmit エラー0、全35ファイル304テストPASS、本番ビルドPASS、
  Playwright実画面で dev UI からToon系ボタン消滅・consoleエラー0を確認。
- 教訓（BACKLOG起票対象）: 0.2m空撮のロジックベースToonは「まだら」か
  「単調グレー」の二択になりやすく、写真の情報量とセル画の色面設計は
  フィルタだけでは埋まらない。次はControlNet系ローカルAIが第一候補。

## 2026-07-21 23:20 — トゥーン表示の改善: オフライン焼き込みバリアント試作（→ロールバック済み）

- ユーザー指摘「Toon Shaderの見た目が汚い（まだら）」→ 原因はフル解像度写真の
  ピクセル単位輝度量子化（groundDetail.ts の gdToonColor）。量子化前の空間平滑化が
  必要と判断し、オフラインで「トゥーン版オルソ」を焼く方式を試作。
- 新規 `pipeline/toonify_ortho.py`。初版は Lab K-means 14色だったが、実画面で
  ユーザー指摘「赤・緑が消え全体が灰色、路面も不均一」→ 頻度ベースK-meansは
  多数派の灰色がクラスタを独占する構造欠陥と判断し、ルールベースに全面改稿:
  bilateral×3 → グレーワールド補正（青カブリ除去、中央値シフト±12上限）→
  低彩度は固定5階調グレーへ強制（路面が一様になる）/高彩度のみ色相12ビン×
  明度2段で保持（縁石の赤・緑地は面積に関係なく生存、彩度下限28、青系のみ
  閾値1.8倍でカブリ耐性）→ 最頻値フィルタ7px → Sobelインク線0.2。
  富士 satellite_shizuoka.jpg (8014×8192, CC BY 4.0) から
  `public/data/tracks/fuji/satellite_toon.jpg`（21MB）+ meta（derived記録）を生成。
- アプリ側: satelliteVariants.ts に "toon" バリアント追加（ラベル「トゥーン」、
  ?dev=1&sat=toon）。既存テスト2件を新バリアント込みに更新。
- 検証: satelliteVariants 15テストPASS。Playwright実画面
  （Temp/verify-toon-variant.mjs / verify-toon-photoroad.mjs、fuji + sat=toon +
  Toon groundシェーダOFF）で、3D Road ON のコカ・コーラコーナーと 3D Road OFF
  写真路面（1:18地点）の両方で、まだら消失・路面一様グレー・縁石赤白と白線の
  生存を目視確認、consoleエラー0（`Temp/toon-variant-chase.png`,
  `Temp/toon-variant-photoroad.png`）。
- 未解決: 静岡SRコリドータイル(shizuoka_x2)のトゥーン化未対応。トゥーン
  バリアント選択時にシェーダToonを自動OFFにするUX。他トラック（鈴鹿・岡山）への
  適用。パレット・インク線パラメータの好み調整。

## 2026-07-18 07:58 — 岡山i-boxオルソ置換＋鈴鹿画像処理改善（Opus 4.8 subagent×2並行実装）

- ユーザー指示「i-boxを入手してトライ、鈴鹿は画像処理で改善、それぞれ
  Opus 4.8のsubagentに実装させる」に対応。2体並行で実装まで委任。
- **①岡山（i-box）**: サブエージェントがi-boxの独自CMSから公開エンドポイント
  `/api/mesh_resources`（9,993メッシュ索引）を発見し、国土基本図図郭コード
  （平面直角V系）を解読・実GeoTIFF座標で検証。サーキットは**6メッシュで
  被覆100%**（05ND9734等、S3直リンクDL・認証不要）。実測**0.25m/px・2024年
  計測・CC BY 4.0**。GSI 0.49m版を全面置換: ベース5014×7521、SRコリドー
  60タイル（強度0.85、LR 35.4dB/シフト0.135px、LPIPS 0.374→約0.13）。
  新規 `pipeline/fetch_ibox_ortho.py`。帰属「おかやまインフラボックス…を
  加工して作成（CC BY 4.0）」をmeta記録（アプリのクレジット行で表示確認）。
  旧GSI版は pipeline/cache/gsi_okayama/ に温存。
- **②鈴鹿（画像処理のみ）**: サブエージェントが3系統を計測比較 —
  グレーディング再調整4候補（既定が最良でMUSIQ低下、据え置き）、SR前unsharp
  （DISTS微減もMUSIQ低下でワッシュ、不採用）、**出力格子x2→x4（採用）**。
  x4=0.123m/px・2048pxタイル32枚・強度0.45（x4ではline-shiftゲートが実質
  厳格化するためガード通過の最大値）。**実利用の画面上MUSIQ 32.7→42.2
  （+29%）**、LR 34.4dB/シフト0.309pxで全ゲートPASS。パイプラインはadditive
  拡張（grade_ortho.pyのパラメータCLI化+provenance、enhance_track_corridor.py
  に `--output-scale`/`--pre-sharpen-*`、新規テスト4件）。容量22→34MB。
  情報解像度は0.49mのまま（表示格子の高精細化）とmetaに明記。
- 統合検証: 全35ファイル304テストPASS、本番ビルドPASS、python unittest
  20件PASS。Playwright実画面: 岡山=60タイル認識・i-box画質で走行、
  鈴鹿=32タイル(x4)認識・近景シャープ化を目視確認、両者consoleエラー0。
- 変更: 上記の両トラックアセット一式、`pipeline/fetch_ibox_ortho.py`(新規),
  `pipeline/grade_ortho.py`, `pipeline/enhance_track_corridor.py`,
  `pipeline/test_*`(+4件)。パネル類はTemp/（grade-okayama-ibox,
  ibox-okayama-overlay, suzuka-improve-final-*等）。
- 未解決: 鈴鹿の真の高解像度化は有償15cm購入ルートのみ（画像処理は打ち止め）。
  岡山のi-box撮影年の地点別確認。fuji/barberへのx4格子適用は未判断。

## 2026-07-17 22:06 — ブートストラップトラックは航空写真の路面上を走る既定へ

- ユーザー要望「コース画像の上を走るように。コーステクスチャのオーバーレイは不要」
  に対応。
- 実装: `TrackData` に任意フィールド `defaultLayers.road3d` を追加し、
  App.tsx のロード時に一度だけ store へ適用（以後の手動トグルは自由）。
  `bootstrap_track_from_osm.py` が `road3d: false` を出力するようにし、
  鈴鹿・岡山の既存 track.json にも即時パッチ。富士・Barber は無指定で従来どおり。
- 効果: 鈴鹿/岡山ではプロシージャルの黒いロードリボンを出さず、車が
  グレード済み航空写真の路面そのものの上を走る（3D Road OFF時の
  0.08mクリアランス再ドレープを既定化した形）。
- 検証: 全35ファイル294テストPASS（並行セッションのUI再編後の構成）、
  本番ビルドPASS。Playwright実画面: suzuka=showRoad3d false / fuji=true を
  確認、consoleエラー0、鈴鹿チェイスで写真路面上の走行をスクリーンショット確認
  （`Temp/photoroad-suzuka.png`）。
- 変更: `src/replay/types.ts`, `src/App.tsx`,
  `pipeline/bootstrap_track_from_osm.py`,
  `public/data/tracks/{suzuka,okayama}/track.json`(パッチ)。

## 2026-07-17 16:30 — 鈴鹿・岡山国際のトラックデータ作成（プレイブック初適用）

- ユーザー依頼「鈴鹿・岡山国際を対象にデータ作成、subagentで並行作業」に対応。
  以後のsubagentはOpus明示指定（ユーザー指示、メモリuse-opus-subagentsに保存）。
- **Step0（Opus subagent 2体並行）**: 両サーキットの権利調査。採用=GSI
  シームレス写真z18（≈0.49m/px）。**e-Gov一次情報で政府標準利用規約2.0
  （CC BY 4.0互換）＝加工・商用・同梱可・承認不要を確認**。不採用=三重県M-GIS
  （二次利用禁止規約）、PLATEAU（両市とも未整備）。将来枠=鈴鹿15cm購入
  （約2,245円/図郭+測量法承認）、岡山i-box LPオルソ2024（CC BY 4.0、被覆要照合
  →BACKLOG追加）。
- **新規パイプライン3本**: `fetch_gsi_ortho.py`（bboxタイル→モザイク+meta）、
  `bootstrap_track_from_osm.py`（OSM racewayの閉ループ組み立て=直進優先ウォーク
  +名前除外+公式長ゲート、地理院DEM標高付与、track.json生成、ODbL/可視化用
  provenance明記）、`make_preview_race.py`（合成プレビュー走行、SYNTHETIC明記。
  laps.jsonのlaps[]必須をデバッグで発見し対応）。
- **生成物**: 鈴鹿=中心線831点/5,811m（公式比+0.1%、40ウェイ結合、標高17〜58m）、
  ベース5632×3840、SR32タイル強度0.6（LR34.1dB/シフト0.11px/LPIPS−60%）、22MB。
  岡山=530点/3,700m（単一ウェイ、標高246〜275m）、ベース2560×3840、SR20タイル
  強度0.7（LR34.5dB/シフト0.10px/LPIPS−69%）、15MB。
  中心線オーバーレイ目視で両コースとも路面正確トレース、S/Fはメインストレート。
- **アプリ変更**: TRACK_CATALOG+2（「コース確認 · 合成走行」）、コリドー表示
  ゲート一般化（fuji=shizuoka_x2/他=default）、衛星バリアントプローブを
  「画像+プローブ資産の両方必須」化（鈴鹿への「静岡 20cm SR」誤表示防止）。
- 検証: 全36ファイル301テストPASS、本番ビルドPASS。Playwright実画面:
  鈴鹿32/32・岡山16/20タイルロード、console/HTTPエラー0、チェイス+俯瞰
  スクリーンショット取得、GSI帰属表示がクレジット行に自動表示されるのを確認。
- 記録: [docs/implementation-suzuka-okayama-2026-07-17.md](docs/implementation-suzuka-okayama-2026-07-17.md)
  新設+README索引、プレイブックへGSI/ブートストラップ追記、BACKLOGへ
  i-box照合1件追加。
- 制約: 元GSD0.49m級（富士20cmの約1/2.4）、中心線はOSM由来1〜5m精度・
  カント無し・幅概算12m（実テレメトリ運用時に要整備）。撮影年は地点別に要確認。

## 2026-07-17 07:40 — リポジトリ ファイル整理計画の作成

- ユーザー依頼「フォルダがかなり散らかっている印象。リファクタリング・ファイル
  整理計画を立てて」に対応（計画立案のみ、実施は未着手）。
- 現状を実測: ルート直下に .mjs 29本（snap/probe/perf/demo、gitignore済）+
  日付つき .py 4本 + 動画2本35MB + 実験HTML2本 + scan-debug.png が散在。
  一時フォルダ3系統併存（Temp/ 85件56MB, tmp/ 6件, scratchpad/ 71件、
  いずれも gitignore されていない）。.bak ファイル66個（fuji の
  scene.native.bak.glb 51MB 含む≒54MB + pipeline スクリプトの手動バックアップ）。
  git はコミット3件のみで未コミット変更87件。pipeline/ 9.2GB の大半は
  .venv-sr 5.6GB + cache/ 3.4GB（意図的配置、整理対象外と判定）。
- [docs/plan-repo-file-organization-2026-07-17.md](docs/plan-repo-file-organization-2026-07-17.md)
  を新設（`## 要旨` 付き）。6フェーズ構成: Phase 0=未コミット87件を先に
  論理コミット（.bakが唯一の履歴のため移動前の安全網、絶対条件）→
  Phase 1=scripts/ へスクリプト集約（docs内コマンドパスの更新が必要）→
  Phase 2=一時フォルダを Temp/ に一本化+gitignore → Phase 3=.bak を
  _archive/YYYY-MM-DD/ へ退避（削除しない）→ Phase 4=public/data 436MB の
  git方針確定（JSONとメタは追跡、大容量バイナリはignore）→ Phase 5=CLAUDE.md
  への再発防止規約追記。所要目安2〜3時間。
- docs/README.md 索引へ1行追加。
- 検証: なし（調査・計画ドキュメントのみ、ファイル移動・コード変更なし）。
- 未解決: 計画の実施全フェーズ。未決3点（scripts/ の git追跡切替可否、
  動画の退避先、日付つき .py の残置/退避）はユーザー判断待ち。

## 2026-07-17 09:30 — 初期ロード時間の原因分析（実測）と改善方針策定

- 依頼: 「コンテンツのロード時間が結構かかる印象。原因分析と改善方針を」。
- 計測: 既存 `perf_profile.mjs` を無改変で使用し、dev server（ユーザー起動中の
  :5199）と本番ビルド preview（:5299、`npx vite build` 46.5s成功→起動）の両方で
  コールドキャッシュ計測。headless Chromium（SwiftShader）のため描画系絶対値は
  過大な点を明記。生データはセッションscratchpadの profile-result.json /
  profile-prod.json。
- 主要結論: 最大要因は `loadInitialReplay()` が衛星バリアント6種のHEADプローブを
  Promise.allで待つブロッキング構造。29MBの satellite_shizuoka.jpg へのHEADが
  実測 dev 8.9s / preview 5.8s（devではStrictModeで×2）で、その間シーンが
  一切マウントされない。次点: dev server固有オーバーヘッド（DCL 7.7s、データ
  配信遅延）、トラック構築の同期8.5sロングタスク、大判アセット
  （terrain 3.5MB / shizuoka 29MB）。scene.glb 40-56MBは遅延ロード済みで無罪。
- 改善方針: P0=プローブ非ブロック化（variants.jsonマニフェスト化 or
  投げっぱなし+onErrorフォールバック、効果−6〜9s）、P1=terrain 16bit化・
  shizuoka 2段ロード・ImageBitmapLoader・KTX2、P2=ドレープWorker化/事前計算・
  進捗UI。詳細は docs/investigation-load-time-2026-07-17.md（要旨つき）。
- 作成/変更: `docs/investigation-load-time-2026-07-17.md`（新規）、
  `docs/README.md`（索引1行）、`BACKLOG.md`（P2×2件+P3×1件追加）。
  コード変更なし（分析のみ、修正は未着手）。
- 検証: 上記2環境での実測のみ。実GPU環境・実CDNでの再計測とP0改善後の
  効果測定は未実施。

## 2026-07-17 07:05 — サーキット地面画像プレイブック作成（優先度+手順の決定版）

- ユーザー依頼「これまでの取り組みを踏まえ、手法の優先度付けと『サーキットの道
  （地面画像）をどう用意するか』の手順化資料を」に対応。
- [docs/guide-circuit-imagery-playbook-2026-07-17.md](docs/guide-circuit-imagery-playbook-2026-07-17.md)
  を新設（`## 要旨` 付き）。内容:
  - 実測に基づく優先度表 P0〜P6（P0=権利クリーンソース必須、
    **P2グレーディングが費用対効果最大**（実証）、P3 SR、P4近接ディテール+
    ベクトル、P5生成AIはコース外限定、P6実データ更新が天井上げ）
  - 新トラック追加の8ステップ手順（Step0権利チェックリスト→native→グレード→
    SR→ベクトル線→ディテール→検証→生成AIパイロット→データ更新判断。
    各コマンド・合格ゲート・富士実測値つき）
  - 実際に踏んだ落とし穴8件と対策表（PSNRゲート/グレード順序/textureLod/
    sRGB平均/キャッシュ世代/カメラドリフト/SwiftShader/実在構造物誤認）
  - 既存docsへの資料マップとBACKLOG連動の残タスク
- docs/README.md 索引へ1行追加。
- 検証: 文書作成のみ（コード変更なし）。記載コマンド・数値は本WORKLOGの
  各エントリおよび引用docsの実測から転記。
- 07:10 追記: docs-viewerスキルでライブ版 `docs/viewer.html` を再生成し、
  スナップショット（18 md + 1 html）を既存Artifact URLへ再公開
  （https://claude.ai/code/artifact/0fc6a284-c2f9-4ef0-afa1-03f4753324eb）。

## 2026-07-17 06:55 — 出口ディベート統合（4サブエージェント×2ラウンド）

- 依頼: コンテンツのビジネス可能性、特に出口（メーカー売却/レース事業者売却/
  権利全部売却/イベント連動）を、複数サブエージェントのディベート形式で検討。
- 構成: R1=拡大派・懐疑派・業界買い手マップ・出口設計の4体並行（Web裏取り
  つき）→ R2=拡大派↔懐疑派の相互反論（譲歩点と撤回条件を明示させる形式）。
- 主要決着: B2C細売り棄却（両者一致）/ 権利者（サーキット・スクール・主催者）を
  顧客にする構図で商標リスクは障壁へ反転 / 生成AIタイルは権利脆弱のため
  売り物から除外しデモレイヤーへ格下げ / 出口の実在はRacing Unleashed×
  vTelemetry PRO（2025-01、IP+人承継）で立証されたが「人ごと」が前提 /
  静岡限定論は反証（兵庫・東京・長崎等の点群公開拡大）だが県外実証は未了 /
  懐疑派の事実キャッチ=TGRはGAZOO Racing Recorder+Viewerを内製済みで
  標的は中小施設に絞る。全シナリオは「有償PoC 1件（50万円以上・書面）」の
  従属変数、という枠組みで両者収束。
- 成果物: `docs/business-exit-debate-2026-07-17.md`（統合判定・出口価値ゲート
  5条件・07-14討論/買い手マップ/出口設計との接続）、docs/README.md索引更新。
  同セッション内でサブエージェントが `docs/buyer-map-motorsport-2026-07-17.md`
  と `docs/design-exit-strategy-2026-07-17.md` を作成済み（下記2エントリ参照）。
- 検証: なし（調査・討論ドキュメントのみ、コード変更なし）。
- 未解決: 有償PoC獲得・県外1コース実証・PROVENANCE台帳整備・弁護士レビューは
  ゲート待ちの未着手タスク。

## 2026-07-17 — 出口戦略（Exit）設計ドキュメント作成

- 依頼: 個人開発資産（3Dリプレイビューア+コース半自動生成パイプライン）の
  出口戦略をM&A/IPライセンス実務観点で設計。
- Web調査: Flippa等のプレレベニュー実勢（$1K-5K）、Racing Unleashedによる
  vTelemetry PRO IP買収+人材承継（2025-01、直接コンプ）、TRANBI等国内
  プラットフォームの売上ゼロ案件の扱い、プレレベニュー評価手法。
- 既存 docs/business-debate-2026-07-14.md の出口候補リスト・Go/Killゲート
  （2027-01-14 最終判定）と整合させ、同ゲートを出口分岐点として流用する
  二段構え（Go→ライセンス+買収オプション型、Kill→即売却モード）を設計。
- 主要結論: コード単体は値が付かない。価値は①権利クリーンなパイプライン
  ②権利チェーン台帳の証拠化（PROVENANCE.md+サイドカーJSON）③顧客契約。
  90日パッケージング（第2コース実証含む）を最優先に。
- 作成/変更: `docs/design-exit-strategy-2026-07-17.md`（新規）,
  `docs/README.md`（索引に1行追加）。
- 検証: なし（調査・設計ドキュメントのみ、コード変更なし）。
- 未解決: 権利台帳の実装（PROVENANCE.md整備）、第2コース実証、買い手
  ロングリスト作成はロードマップ上のタスクとして未着手。

## 2026-07-17 (時刻はコミット時に確定) — 買い手/提携先マップ調査（業界アナリスト視点）

- 依頼: 3Dリプレイ資産の現実的な買い手・提携先を日本の実名ベースで分析。
- WebSearchで2025-2026年動向を確認: SFgo（JRP×パーソルクロステクノロジー
  2026/4提携、AR/VR・AI解析構想）、STMO発足（豊田章男理事長・支援10社）、
  FSW富士モータースポーツフォレスト投資継続、HRS鈴鹿のロガー教育、
  GR Driving Experience 2026刷新、GR86/BRZ Cup(T.R.A.)、DigSpice 2.1万台、
  AiM国内代理店(Star5/XTRA/ベア)、モータースポーツジャパン横浜移転 等。
- 成果物: `docs/buyer-map-motorsport-2026-07-17.md`（買い手マップ、
  イベント連動形態、データ権利・肖像の注意点、上位3ターゲットの
  アプローチ手順。確認済【確】と推測【推】を区別）。docs/README.md索引更新。
- 未解決: 「計時の日本エアロスペース系」は検索で実在確認できず要再調査。
  S耐TVの映像制作会社名も未確認。

## 2026-07-17 06:35 — 全周俯瞰スイープによるSR配置の全数検証（ずれ実測ゼロ）

- ユーザー指摘「他の箇所でも複数発生。上から目視確認して修正を」に対応。
- 新規ブラウザ（キャッシュ無し）でコース全周を15ステーション（320m間隔、
  真上・北=上・footprint約400m）撮影し、各地点でグレード済み航空写真と
  位相相関照合（`Temp/sweep/d****.png`、コンタクトシート
  `Temp/sweep-contact.jpg`）。
- 結果: **全15地点のずれ 0.25〜0.75m（相関ピーク最大0.98）**。数十m級の
  配置ずれは1箇所も無し。0.3〜0.5mの一様な残差は照合窓の近似と遠近によるもの。
- 結論: フレッシュな状態ではSR配置は全周正常。ユーザー環境で複数箇所に
  見えるずれは 06:12 記載のキャッシュ世代混在（修正デプロイ済み、
  タブの再読み込みで解消）。
- コード変更なし（検証のみ）。

## 2026-07-17 06:12 — SR表示ずれの真因特定（ブラウザキャッシュ混在）とキャッシュバスティング修正

- ユーザー提供の同一時刻2枚比較（7/30 Lap5 t=5.68、ホームストレート）:
  下地「静岡県20cm」は正常、「静岡20cm SR」は道が数十m左へずれて表示。
- 調査: ①全140タイルをグレード済みモザイクの期待矩形と位相相関照合→
  実内容ありの全タイルが一致（p50 0.01px。無相関の9枚は完全透明の
  フェード端タイルで無害）②同時刻を新規ブラウザで再現→SR/下地とも正常で
  **再現不能**。
- **真因: ブラウザキャッシュの世代混在**。マニフェスト/タイルを
  `cache: "force-cache"`（再検証なし）で取得しており、かつ本日の再生成で
  タイルのファイル名（格子インデックス）は同一のまま座標系が
  8014px/0.2192m→8782px/0.20m格子へ変化。開きっぱなしのセッションでは
  「新マニフェスト+旧キャッシュタイル」等が混ざり、同名タイルが数十m
  ずれた場所に貼られる（昨夜の「向きがおかしい」報告も同根の可能性大）。
- 修正: ①EnhancedCorridorGroundのマニフェスト取得を `no-cache`（再検証）へ
  ②enhance_track_corridor.run_tiles と imagegen_pilot.apply がタイルエントリへ
  生成時刻の `?v=` スタンプを付与（世代毎に別キャッシュキー、タイル自体は
  force-cacheのまま効率維持）③稼働中の2つのmanifest.json（コリドー140+
  パイロット12）へ `?v=` を即時パッチ。
- 検証: 全36ファイル301件PASS、本番ビルドPASS、Playwright実画面で
  タイルが `?v=g20260716T1832` 付きHTTP 200・エラー0、同時刻のSR表示正常。
- ユーザー向け即時対処: 開いているタブは一度ハードリロード
  （Ctrl+Shift+R）すれば混在キャッシュが解消。以後は通常リロードで安全。
- 変更: `src/engine/track/EnhancedCorridorGround.ts`,
  `pipeline/enhance_track_corridor.py`, `pipeline/imagegen_pilot.py`,
  `public/data/tracks/fuji/satellite_corridor_x2/manifest.json`(パッチ),
  `public/data/tracks/fuji/imagegen_trials/manifest.json`(パッチ)。
- 未解決: 下地JPEG等の非タイル画像（satellite_*.jpg）は通常のHTTPキャッシュ
  依存のまま（差し替え時は同様の混在リスク。必要ならmeta経由の?v付与を検討）。

## 2026-07-16 23:39 — 「130R先の道がおかしい」報告の調査（実在構造物と確認・非バグ）

- ユーザー報告スクリーンショット（7/30 Lap5 t=45.489、chase、マスク系レイヤー全OFF）
  の前方の縞状地形を調査。
- 位置特定: dist≈1758m、モザイク画素(5763,5292)、進行方向は西北西。
- 航空写真の真値確認: 車の前方250mには**実在の段々擁壁・排水ステップ・伐採斜面の
  筋模様**が写っており（`Temp/anomaly-photo-truth.jpg` / `anomaly-ahead-300m.jpg`）、
  スクリーンショットの縞と内容一致。さらに当該コーナー先は実物も下り
  （クレスト）で、低いカメラからは道が稜線の向こうへ消える。
- 同一フレームをPlaywrightで再現（`Temp/130r-repro-bare.png`）: 道は正常に
  クレストまで続き、縞=遠景の擁壁/伐採斜面と確認。3D Features+Detail Texture
  をONに戻すと樹木にマスクされ自然な見え方（`130r-repro-with-features.png`）。
- 結論: **データ破損・タイル向き/配置の不具合ではない**。2Dドレープが急峻な
  擁壁を表現できず、グレージング角で写真が伸びる既知の限界＋全レイヤーOFFの
  組合せで顕在化したもの。
- 対応: BACKLOG P2へ「急斜面・擁壁のドレープ伸び対策」を追加（擁壁の簡易3D化/
  ビルボード化、地形局所細分化）。コード変更なし。
- 補足フラグ: 並行作業で追加された `satellite_bing.jpg`/`satellite_bing_sr.jpg`
  （Bing画像の固定化+ML加工）は、CLAUDE.mdの画像方針（派生加工可能な出典記録
  のある画像のみ処理）に照らしライセンス確認が必要。

## 2026-07-16 23:28 — Replay内「Trial tiles」トグル実装（生成タイル⇔現行SRのin-place A/B）

- ユーザー要望「Replay内で20cm SRの生データと生成データを比べられるように」へ対応。
  境界比較や外部ギャラリーでなく、**同一視点のままON/OFFで切り替えるトグル**を実装。
- `EnhancedCorridorGround`: overrideマニフェスト由来タイルへ `override` フラグを
  付与し、`setOverrideTilesVisible()` を追加（OFF中は表示停止に加えフェッチも抑止、
  読込中に完了したタイルも現在状態で生成）。標準SRタイルは不変。
- 配線: TrackBuilder `setTrialTilesVisible`（生成前の状態ラッチ対応）→
  ReplayScene → replayStore `showTrialTiles`（既定ON）→ ViewerCanvas
  （初期適用+subscription+キー `I`）→ LayersPanel「Trial tiles」ボタン。
- 検証: 全36ファイル301件PASS、本番ビルドPASS。Playwright実画面:
  表示中オーバーライドメッシュ ON=1→OFF=0→再ON=3（OFF中ロード分の遅延表示も
  正常）、ボタンaria-pressed連動、consoleエラー0。
  スクリーンショット `Temp/trial-toggle-on/off.png`。
- 変更: `src/engine/track/EnhancedCorridorGround.ts`, `TrackBuilder.ts`,
  `ReplayScene.ts`, `state/replayStore.ts`, `engine/ViewerCanvas.tsx`,
  `ui/LayersPanel.tsx`,
  `docs/evaluation-imagegen-pilot-results-2026-07-16.md`（トグル追記）。
- 未解決: ユーザーの実機A/B判断（採用/棄却）、「向きがおかしく見えた」タイルの
  番号特定待ち。

## 2026-07-16 23:08 — パイロット結果のmd化・向き全数検証・比較基準を静岡20cm SRへ変更

- ユーザー指示3点「mdで残す / 画像の向きがおかしいものがあるので確認 /
  比較基準はソース原画でなく現行ベスト(静岡20cm SR)に」へ対応。
- **向き検証: 幾何学的な異常は不検出**。①画像レベル: 生成12枚全数を
  ソースと8方位(回転4×鏡映2)NCC照合→全て正方向が最良(0.994〜0.998)、
  取り違えもなし ②表示レベル: FreeCameraコントローラを無効化して真上・
  北=上の正射スクリーンショットを4地点(#01/04/09/10)撮影し8方位照合→
  全て正方向 ③表示経路は標準タイルと同一コードでタイル毎に向きが変わる
  余地なし。知覚要因（生成AIが描き直した影・芝目の方向矛盾）の可能性を提示。
- **比較基準の変更**: `imagegen_pilot.py` に `compare` サブコマンドを追加。
  出荷中の静岡20cm SR相当（グレード済み→RealPLKSR×4→2× s=0.5）を
  表示等倍で並置する `pilot_XX_panel.jpg`（ラベル付き）と `compare.html`、
  `compare_summary.json` を再生成。結果: **生成側がMUSIQで全12地点勝ち
  （+2.3〜+15.0）、MANIQAも全勝**。#09はCoca-Colaロゴが判読可能になる等
  目視でも明確。
- 記録: [docs/evaluation-imagegen-pilot-results-2026-07-16.md](docs/evaluation-imagegen-pilot-results-2026-07-16.md)
  を新設（ゲート結果・SR比IQA表・向き検証・Replay接続/復元手順・採用パターン）。
  docs/README.md索引へ1行追加。
- 検証: 向き照合スクリプト実行結果、Playwright正射撮影4枚、compare実行の
  IQA数値、panel目視（#09）。コード変更は imagegen_pilot.py の compare 追加のみ。
- 未解決: ユーザー目視での「向きがおかしく見えた」タイル番号の特定（指摘が
  あれば個別精査）、採用/棄却判断。

## 2026-07-16 22:49 — ImageGen成果物12枚を評価しReplay比較表示へ接続

- ユーザーが `pilot/成果物/` へ12枚（1254×1254）を格納。「リプレイで比較できるように」
  に対応。
- 評価（素の生成物）: **0/12 PASS**。ただし前回フリー生成より大幅良化 —
  幾何は11/12がシフトゲート内（p95 0.20〜0.71px、前回は≈2.8m）、
  LR一貫性20.4〜27.4dB（ゲート30、前回17.6）、MUSIQ +5〜+20。
  失敗主因は**色ずれ（Δ10.7〜32.3、生成側の再グレーディング）**とテクスチャ再合成。
- `imagegen_pilot.py` に `apply` サブコマンドを追加: ソース統計への
  per-channel色一致（決定論・位置保存）+ 24px縁フェザーのRGBAタイル化 +
  provenance付きオーバーライドmanifest生成。色一致後は Δ色0.8〜1.4、
  LR 24.2〜30.1dB（#04は30dB超）へ改善。
- `imagegen_trials/manifest.json` をパイロット12枚構成へ差し替え
  （旧1枚構成は `manifest.pre-pilot.json` へバックアップ。復元はコピー1発）。
- 検証: Playwright実画面（Top視点）で総タイル152=標準140+パイロット12を認識、
  パイロットタイルHTTP 200、priority付きメッシュ描画、console/HTTPエラー0。
  近い順ストリーミングのため俯瞰12秒時点でロード済みは3枚（挙動正常）。
- 生成・変更: `pipeline/imagegen_pilot.py`(apply追加),
  `imagegen_trials/pilot/pilot_XX_gen.png`(成果物コピー)・`_tile.png`(12枚)・
  `_eval.json`/`_panel.jpg`, `imagegen_trials/manifest.json`(差し替え+バックアップ)。
- 判定メモ: 幾何・色は比較表示に耐える水準になったが、LR一貫性が示す
  「20cmスケールの描き直し」は残存。走行ライン近傍の採用は引き続き非推奨、
  画面上での主観比較はユーザー判断へ。
- 未解決: ユーザーの実機目視比較、採用/棄却の判断（コース外#10-12優先）、
  比較終了後のmanifest復元。

## 2026-07-16 22:40 — docs-viewerスキル新設（グローバル）+ docs直下規約のグローバル化

- ユーザー指示「skill化するとともに、グローバルでいつでもdocsはプロジェクト直下に
  置くようにする」に対応。
- **グローバルスキル** `C:\Users\PC_User\.claude\skills\docs-viewer\` を新設:
  `SKILL.md`（手順書）+ `viewer-template.html`（単一ソーステンプレート）+
  `build_snapshot.mjs`（依存ゼロのNode生成スクリプト）。どのプロジェクトでも
  `/docs-viewer` で docs/ をビューア化できる。
- 方式: テンプレートは「ライブ（docs/をfetch、保存→リロード即反映）/
  スナップショット（全文埋込の自己完結HTML、Artifact共有用）」の2モードを
  1ソースで持つ。一覧・種類・概要は docs/README.md 索引から、各docの要旨は
  冒頭 `## 要旨`（結論/概要/TL;DR可）から自動抽出。md簡易レンダラ内蔵
  （表・コード・ネスト箇条書き・doc間リンクの#ハッシュ変換対応）。
- **グローバル規約** `C:\Users\PC_User\.claude\CLAUDE.md` を新規作成:
  「ドキュメントは常にプロジェクト直下 docs/」「README.md索引」「冒頭に## 要旨」
  「公開URLは docs/.docsviewer-url に保存し同一URLへ再公開」。
- 本プロジェクトへ適用: `docs/viewer.html`（ライブ版）設置、`docs/.docsviewer-url`
  作成、README索引へviewer.htmlの行を追加。スナップショット（124KB、13md+1html、
  21:51追加の guide-imagegen-pilot も含む）を既存URLへ再公開:
  https://claude.ai/code/artifact/0fc6a284-c2f9-4ef0-afa1-03f4753324eb
  （手作り要約版v1は版履歴に残存。以後は自動生成の要旨+全文形式）。
- 検証: builderが13md+1html認識。Playwright headless Chromiumで両モード確認 —
  タブ15（概要+14）、カード14、要旨ボックス・表描画（評価docで3表）、
  ←→キー遷移、console/pageエラー0（`Temp/check_docsviewer.mjs`、スクリーンショット
  目視で概要タブのデザイン確認済み）。HTML資料タブのiframe表示は自動確認未実施。
- 未解決: 実ブラウザでの人手操作確認。他プロジェクトへの適用は各プロジェクトで
  `/docs-viewer` 実行時（新セッションからスキルが一覧に載る想定）。

## 2026-07-16 21:47 — ImageGen 12枚パイロットキット（ソース切り出し+合否評価スクリプト）

- ユーザー依頼「imagen対象画像とスクリプトを提示、自分でやってみる。12枚だったか」に対応。
  対象は既存試算どおり代表12枚で確定。
- `pipeline/imagegen_pilot.py` を新設（prepare/evaluateの2サブコマンド）。
  prepareは**グレード済みnativeモザイク(0.20m/px)**から512px=102.4m四方を12枚切り出し:
  on-track 9枚（周回1/8刻み8点+既存トライアル互換の1519.2m地点）+
  off-track 3枚（±70m横: パドック建物/林/ランオフ）。
  `public/data/tracks/fuji/imagegen_trials/pilot/` に pilot_XX_src.png、
  index.json（座標・UV・sha256・ゲート定義）、PROMPT.txt（JA/EN編集指示）、
  contact_sheet.jpg、index.html（手動アップロード用ギャラリー）を生成。
- evaluateは pilot_XX_gen.* を自動検出し、SR同型ゲートで合否判定:
  LR一貫性≥30dB / ブロック位相相関シフトp95≤0.5px(=10cm) / 平均色差≤8 /
  コントラスト比0.8〜1.2。--no-iqa無しならMUSIQ/MANIQA比較も出力。
  panel/evalのJSONを画像ごとに保存。
- 検証: prepare実行で12枚生成、contact sheet目視で全地点妥当（S/F・縁石・
  Coca-Colaコーナー・コース外含む）。evaluateは同一画像の自己テストで
  PASS判定（LR 39.7dB/シフト0/色差0）を確認し、テスト生成物は削除。
- 生成: `pipeline/imagegen_pilot.py`, `public/data/tracks/fuji/imagegen_trials/pilot/`
  （src 12枚+index.json+PROMPT.txt+contact_sheet.jpg+index.html）。
- 未解決: ユーザーによる手動生成（ChatGPT/Gemini等・サブスク枠内）と
  evaluate実行、PASS分の適用判断（コース外優先の方針は維持）。
- 21:55 追記: 実施手順を [docs/guide-imagegen-pilot-2026-07-16.md](docs/guide-imagegen-pilot-2026-07-16.md)
  として文書化し、docs/README.md 索引へ1行追加。

## 2026-07-16 21:42 — DocsViewer（docs全13件の要約切替ビューア）をArtifactとして作成

- ユーザー依頼「docsの内容をまとめて横に切り替えてみるDocsViewerを作って」に対応。
- docs/README.md索引の全13件を対象に、未読9件はサブエージェント3体（Explore）で
  並列要約し（TLDR/要点/主要数値/未解決の固定スキーマ）、既読4件
  （費用試算・ImageGen忠実度評価・4×パッチ評価・索引）は本文から直接要約。
- HTML Artifactとして公開: https://claude.ai/code/artifact/0fc6a284-c2f9-4ef0-afa1-03f4753324eb
  構成=概要タブ（docs地図+13カード+運用ルール要点）+13ドキュメントタブ。
  タブレール・← →キー・スワイプ・前後ボタンで横切替、location.hashで位置保持、
  種類別チップ（評価/調査・研究/実装/試算/討論・相談/手順/運用）、
  アクティブタブは赤白縁石ストライプのインジケータ。reduced-motion対応。
- 注記した正確性配慮: imagery-enhancement(2×SR)は同日Phase 1刷新で置き換え済みの旨を
  要約に備考として明記。スナップショット生成時刻と「正は原本ファイル」をフッターに記載。
- 検証: Artifact公開成功（URL取得）。ブラウザでの表示・操作の実機確認は未実施。
- 変更: リポジトリ変更なし（本WORKLOG追記のみ。HTML本体はセッションscratchpad）。
- 未解決: docs更新時はビューアの再生成が必要（静的スナップショット方式のため）。

## 2026-07-16 19:05 — 中盤4×試験パッチの定量評価（助言・読み取り専用）

- ユーザー質問「4×パッチは効果あるか、もっといい手は」に対し読み取り専用で計測。
- 忠実度: 健全（LR一貫性42.1dB、幻覚なし。生成AI版の17.6dBとは別物）。
- 見かけ解像度: 表示等倍比較でMUSIQ 53.0 vs 現行2×タイル42.3、MANIQA 0.276 vs
  0.237 — 極近景のエッジ鮮鋭度は確かに向上。
- ただし**トーン不整合が大**: パッチは18:35グレーディング前の旧0.2192m/px JPEGから
  生成されており、グレード済みの周囲に対し平均R+20.5/G+10.8明るく、
  コントラスト(std)34.5 vs 49.7（−30%）。現状では明るく霞んだ四角として浮く。
  比較パネル: `Temp/trial-vs-shipped.jpg`（左=パッチ、右=現行タイル拡大）。
- 助言: 短期=グレード済みnativeから同座標で再生成 / 本命=標準パイプラインへ
  「コア4×リング」実装（全周一貫・優先タイル不要）/ 生成AI(ImageGen)は
  sandbox helper障害で編集モード（ソース条件付き）が使えずフリー生成しか
  通らないのが不適用の技術要因。API直叩き（base64入力の編集モード）なら
  helper非依存で試行可能、ただし採用はSR同型ゲート通過を条件とする。
- 19:20 追記: 上記評価と選択肢・課金整理を
  [docs/evaluation-4x-patch-and-imagegen-options-2026-07-16.md](docs/evaluation-4x-patch-and-imagegen-options-2026-07-16.md)
  として文書化し、docs/README.md 索引へ1行追加。API直叩きはChatGPT Plus/Codex等の
  サブスク枠を充当できない従量課金（12枚$0.5〜3規模）である旨を明記。
- リポジトリ変更: docs 1件新規 + README索引1行（コード・データ変更なし）。

## 2026-07-16 18:35 — トーングレーディング/車挙動カクつき修正/保存先D:統一

- ユーザーFB「改善は感じるが劇的でない。Googleの方がまだ良く見える。他の打ち手は？
  車の挙動が前後にかくつく。関連物はC:でなくD:へ保存」の3点に対応。
- **①画質（トーングレーディング）**: Googleとの見え方の差の大部分は解像度でなく
  仕上げ（グレーディング）と判断し、`pipeline/grade_ortho.py` を新設。
  gray-world WB(±8%上限)→輝度パーセンタイルストレッチ→CLAHE局所コントラスト
  (LAB L, 25mタイル)→彩度+12%。全て位置保存のピクセル局所処理で、Google画素は
  入力にも較正基準にも不使用。native モザイクをグレードし、同一グレードから
  8K下地(`satellite_shizuoka.jpg`再生成、toneEnhancedをメタ記録)とSRタイル
  140枚(36MB)を再生成（継ぎ目の色差なし）。グレード入力での知覚検証:
  強度0.5自動採用、LPIPS 0.163→0.059(−64%)、DISTS −46%、LR一貫性40.0dB、
  線シフトp95 0.16px、ガード一発通過。単体テスト6件新設PASS。
- **②車挙動のカクつき**: 実データ診断（lap3: 正確な10Hz、重複0、後退ステップ0、
  GPS/ログ速度一致）でデータは無罪と確定。真因は(a)位置の区分線形補間による
  100ms毎の速度段差 (b)サンプル境界でスナップする区分一定ヘディング
  (c)ChaseCameraの固定0.15/frame lerp（フレームレート非正規化）の複合。
  対応: `interpolation.ts` にラップ毎モーションキャッシュ（ローカル座標+
  サンプル毎heading/pitch事前計算）を導入し、位置をC1三次エルミート
  （非一様Catmull-Rom、全GPSサンプル厳密通過）、heading/pitchをアンラップ
  済み配列の連続補間へ。`ChaseCamera` はτ=100msの指数平滑（dt正規化）へ変更。
  新テスト6件（C1連続・サンプル通過・±πシーム・フレームレート非依存等）PASS。
- **③保存先D:統一**: enhance_track_corridor.pyがTORCH_HOME/HF_HOMEを
  `pipeline/cache/` へデフォルト設定。venvへpip.ini(cache-dir=D:)。
  Playwrightブラウザ690MBを `pipeline/cache/ms-playwright` へ移設し
  `PLAYWRIGHT_BROWSERS_PATH` をsetx（既存セッションからは要明示env）。
  C:残量 3.8→4.2GB。メモリ(c-drive-nearly-full)へ運用を記録。
- 検証: 全36ファイル301件PASS、本番ビルドPASS。Playwright実画面（凍結Free
  カメラ+グレード済みタイル）: 141タイル認識、console/page/HTTP/シェーダー
  エラー0、ディテールON/OFF差12.9%。グレード前後比較 `Temp/grade-before-after.jpg`
  とスクリーンショットで、アスファルトの黒・ラバー痕・縁石の分離が大幅改善を目視。
- 変更: `pipeline/grade_ortho.py`(新規), `pipeline/test_grade_ortho.py`(新規),
  `pipeline/enhance_track_corridor.py`(cacheデフォルト), `pipeline/.venv-sr/pip.ini`,
  `src/replay/interpolation.ts`, `src/replay/interpolation.test.ts`,
  `src/engine/cameras/ChaseCamera.ts`, `src/engine/cameras/ChaseCamera.test.ts`(新規),
  `public/data/tracks/fuji/satellite_shizuoka.jpg`+meta(グレード版),
  `public/data/tracks/fuji/satellite_corridor_x2/`(再生成)。
- 未解決: 実GPUでの動き/画質の主観確認（ヘッドレスはSwiftShader）、残る画質打ち手
  （neosr自前FT、KTX2化、決め絵diffusion、照会系3件）はBACKLOG/戦略doc参照。

## 2026-07-16 18:32 — ImageGen試験タイルの生成忠実度を計測評価（助言のみ・コード変更なし）

- ユーザー依頼「imagen生成→トラック適用テストへの改善点・アドバイス」に対応。
  並行セッションの18:17修正（配置UV修正・SR 2048タイル差し替え）とは独立に、
  生成画像そのものの品質を読み取り専用で計測した（scratchpadスクリプト4本、
  pipeline/.venv-sr使用）。
- 位置同定: 生成PNG（`middle-sector-256-imagegen-test.png`）の内容は修正後の
  枠B (x=5525..6037, y=3288..3800) に対応（位相相関ピーク0.057、旧枠A
  (中心6335,3883) とは0.012=無相関）。現行 `middle-sector-correct-source-512.jpg`
  は枠Bとシフト(0,0)・ピーク0.62で一致。→ 15:55時点の適用は「内容は枠B・
  配置は枠A」で枠中心間約142mずれていたことを独立に裏づけ（18:17の修正と整合）。
- 生成忠実度（gen 1254px→512縮小 vs source-512、グリッド0.2192m/px）:
  最良フィットはスケール0.98+シフト(-7,-6)px≈1.4m（ピーク0.11、往復対照0.98）。
  LR一貫性PSNR **17.6dB**（SRゲート≥30dB・SR実績41.8dB）。ブロック位相相関
  シフト p50 4.5px / p95 12.8px（**≈2.8m**、位置ずれ+描き直しの複合）。
  色は平均約-9%暗・コントラスト+8〜14%で隣接SRタイルと不連続。PNGはRGB
  不透明（フェザー用アルファ無し）。
- 目視で確認した内容改変: Coca-Colaロゴ再描画（向き・位置・書体）、隣接路の
  赤白エッジマークの青灰色化、路上への車様オブジェクト出現、照明ポール消失、
  縁石ブロック間隔・フェンストラス形状の創作。
- 助言（詳細は会話ログ）: 再試行時は ①フリー生成でなく忠実度制御つき編集モード
  ②生成後の自動レジストレーション（相似変換推定+残差ゲート）③SR同型ゲート
  （LR一貫性・線シフト・DISTS）の流用 ④sourceへの色ヒストグラム一致
  ⑤タイル端フェザー ⑥ロゴ・車両のマスク保護 ⑦適用先をコース外テクスチャへ
  限定（走行ライン近傍はSR+ベクトル線を正とする）⑧manifestへのprovenance記録
  （生成モデル・プロンプト・入力sha256等、CLAUDE.md画像方針）⑨12枚パイロット
  の合否基準の事前固定。
- 検証: 上記計測の実行のみ。リポジトリ変更は本WORKLOG追記のみ。
- 未解決: ImageGen再試行（18:17時点でsandbox helper障害により受け渡し失敗中）、
  助言の採否・パイロット実施判断。
- 18:45 追記: 本評価を [docs/evaluation-imagegen-fidelity-2026-07-16.md](docs/evaluation-imagegen-fidelity-2026-07-16.md)
  として文書化し、docs/README.md の索引へ1行追加。18:35のトーングレーディング
  再生成を踏まえ「色合わせ基準はグレード後下地から切り直す」「mlsr-2048タイルの
  旧トーン残留は要確認」を留意点に記載。

## 2026-07-16 18:17 — ImageGen試験パッチの位置ずれを修正しReplayで実ロード確認

- 15:55の試験適用が、古いvalidation reportの `centerPixel=(6335.34,3883.55)` を
  参照していたため、現在の富士センターライン中盤から約82mずれていたことを特定。
  旧矩形内のセンターライン標本は0点だった。
- 現行 `track-creator/tracks/fuji/track.json` の中盤点（control point 217、
  周回距離1519.2m、lat 35.37161976 / lng 138.93119257）から元画像座標を再計算し、
  静岡県20cmオルソを `(x=5525,y=3288,w=512,h=512)` で切り直した。
- ImageGenへのローカル画像受け渡しはWindows sandbox helper障害で失敗したため、
  位置修正を優先。単純4倍拡大を仮生成した後、既存検証でDISTS最適かつ
  LR一貫性/線シフトゲートを通過した4xNomosWebPhoto_RealPLKSR（RTX 3090、
  SR強度0.45）へ差し替え、2048px RGBAを生成。矩形境界112pxをフェードして
  既存SR層との継ぎ目を抑制した。
- `imagegen_trials/manifest.json` を正しいUV
  `(0.68941852,0.40136719)-(0.75330671,0.46386719)` へ更新。
  新矩形にはセンターライン標本17点、距離1463.2〜1575.2mが含まれる。
- 生成・更新:
  `middle-sector-correct-source-512.jpg`,
  `middle-sector-correct-mlsr-2048.png`,
  `imagegen_trials/manifest.json`, `imagegen_trials/compare.html`。
- 検証: manifest/元画像/高精細画像/比較ページのHTTP 200、比較画像の実寸
  512×512 / 2048×2048、`npm run build` PASS、投影テスト4件PASS。
  Playwrightで富士7/30・Lap 5の35.1秒へシークし、高精細PNGの再取得HTTP 200、
  trial画像の再取得HTTP 200・trial関連HTTPエラー0を確認。Chromeに応答URLを伴わない
  汎用404ログ1件は残る。画面キャプチャは保存したがsandbox helper障害で自動目視未実施。
- 15:55の旧エントリは監査用に残すが、その座標・旧PNGは現行manifestから未参照。

## 2026-07-16 16:40 — 地面解像度3層戦略のPhase 0〜2実装（SR刷新+近接ディテール）

- ユーザー承認「0,1,2について対応を」を受け実装開始（subagent起用も許可）。
- 環境整備: torchインストール中にC:が満杯(残281MB)となりDLLが破損。壊れたtorchと
  pipキャッシュを除去してC:を3.8GBへ回復し、以後の重量物はD:のvenv
  (`pipeline/.venv-sr`, torch 2.6.0+cu124)とpipeline/cache配下へ隔離。
  C:は慢性的に残量僅少のため今後も注意。
- Phase 0: `fetch_shizuoka_ortho.py` へPNG出力を追加し、キャッシュ済みTIFF42メッシュ
  からnative 0.200m/px・被覆100%の無劣化モザイク(8782×8977)を生成。JPEG中間を排除。
- Phase 1: `enhance_track_corridor.py` にspandrelエンジン
  （4xNomosWebPhoto_RealPLKSR、CC BY 4.0、RTX 3090 CUDA）を実装し既定化。
  50%ブレンド廃止。評価をDISTS argmin選定+LR一貫性≥30dB+位相相関線シフト
  p95≤0.5pxへ刷新（PSNR/SSIMは報告のみ）。結果: 強度0.45自動採用、
  LPIPS 0.122→0.039(−69%)、DISTS 0.083→0.042(−50%)、LR一貫性41.8dB、
  線シフトp95 0.156px、MUSIQ+8〜14。s≤0.5ではPSNR/SSIMも+1.4dB改善。
  140タイル/26MB/格子0.100m/pxを再生成し、モデル出所・sha256をマニフェスト記録。
- Phase 2: ディテールシェーダーを `groundDetail.ts` へ共有化し、SRタイル
  (EnhancedCorridorGround)へ適用。ゾーンはin-shader ExG+タイルalphaゲート、
  反復抑制はHeitz 2018 hex-tilingのper-tap mean-neutral比率実装。トグルは
  uniform共有で下地+タイル一括。
- 実装過程で潜在バグ2件を発見・修正: ①比率分母のtextureLod(lod7)が環境により
  ほぼ0を返し、全面が約×2明化（v2から潜在、SRタイルに隠れ未発覚）→分母を
  CPU事前計算のリニア平均uniformへ ②sRGB平均の順序誤り（平均→デコード）→
  texel毎デコード後に平均へ。
- 検証: 単体テスト35ファイル295件PASS（pipeline+8件、groundDetail+5件）、
  本番ビルドPASS。Playwright実画面（凍結Freeカメラ）: 141タイル認識（並行作業の
  ImageGen override 1枚含む）、タイル`corridor-tile-detail-v1`/下地
  `satellite-ground-detail-v3`の適用確認、console/page/HTTP/シェーダーエラー0、
  ディテールON/OFF画素差12.7%。ピクセル単位のレイヤー切替検証で、近景の茶色帯は
  AC Overlayリボン（別機能）でありディテール層と無関係と確認。
- 教訓: chaseカメラは静止後も数秒イージングするため前後比較はFreeカメラで凍結 /
  シェーダー正規化定数をミップに依存させない（SwiftShaderでtextureLodが黒）/
  ヘッドレスはSwiftShader描画のため最終合否は実GPU目視を併用。
- 変更: `pipeline/fetch_shizuoka_ortho.py`, `pipeline/enhance_track_corridor.py`,
  `pipeline/test_enhance_track_corridor.py`, `src/engine/track/groundDetail.ts`(新規),
  `src/engine/track/groundDetail.test.ts`(新規), `src/engine/track/TrackBuilder.ts`,
  `src/engine/track/EnhancedCorridorGround.ts`, `src/engine/track/detailTexture.ts`,
  `.gitignore`, `public/data/tracks/fuji/satellite_corridor_x2/`(再生成),
  `docs/research-ground-resolution-strategy-2026-07-16.md`(実装追記),
  `docs/README.md`, `BACKLOG.md`(2件✅)。検証補助 `verify_ground_detail.mjs` /
  `probe_ground_layers.mjs` は残置(*.mjsはgitignore)。
- 検証用Vite(4174)はPID・コマンド確認のうえ停止、ポート解放を確認。
- 未解決: 実GPUでの人手A/B目視、タイルKTX2/UASTC化、スプラット高度化、
  neosr自前FT、照会系3件（富士SW保有データ/ドローン見積/茂原10cm）。

## 2026-07-16 15:55 — ImageGen代表地点をReplay地形へ試験適用

- 富士middle sectorの生成AI高解像度画像を、元航空写真座標 `(6335.34, 3883.55)` を中心とする512px四方のUV範囲へ配置した。
- 既存119枚のSR回廊タイルは維持し、`imagegen_trials/manifest.json` の任意オーバーライドを追加読込みする方式にした。
- ImageGenパッチにはpriority 10を設定し、従来タイルより2cm上、道路メッシュより下へ投影して深度競合を防止した。
- 変更: `src/engine/track/EnhancedCorridorGround.ts`, `src/engine/track/TrackBuilder.ts`, `public/data/tracks/fuji/imagegen_trials/manifest.json`。
- 検証: `npm run build` 成功。`EnhancedCorridorGround.test.ts` 4件成功。テスト資産のHTTP取得は200。
- 表示条件: Satelliteで「静岡 20cm SR (`shizuoka_x2`)」を選択し、Google LiveをOFFにする。
- 注意: 生成画像は視覚品質試験用で、線形・測量精度を保証しない。試験範囲外は従来SR画像のまま。

## 2026-07-16 14:55 — 地面解像度向上のベスト手法調査と統合提案

- ユーザー要望「docsを確認のうえ、航空画像上を3D車で走るコンテンツの解像度向上の
  ベスト手法を提案。外部調査はOpusのsubagentへ依頼」に対応。
- docs全11件・BACKLOG・pipeline/エンジン実装を精査し、Opusサブエージェント3体で
  外部調査（SR最新手法 / 20cm超の画像ソース / 近接地面の描画技法）を並列実施。
- ローカル精査での発見: ①`pipeline/cache/shizuoka_ortho/` にnative 20cm無圧縮TIFF
  42メッシュが残存する一方、SR入力は8192px制限で0.2192m/px化+JPEG q94の劣化済み
  画像だった ②TrackBuilderの近接ディテールシェーダーがSRコリドータイル
  (EnhancedCorridorGround)に未適用で、最も見られる路面近景で質感が消えている
  ③PSNR/SSIMゲートは知覚-歪みトレードオフで常にボケ側が勝つ構造で、
  50%ブレンドという過剰な保険の真因。
- 調査結論: SRは4xNomosWebPhoto_RealPLKSR(CC BY 4.0)差し替え+DISTS/無参照IQA/
  LR一貫性ゲートが最良（4x-UltraSharpはCC BY-NCで商用不可、StableSR/SUPIRも
  非商用）。富士で20cmを真に超える既製ソースは不存在（地理院40cm/GEOSPACE 25cm/
  衛星native 30cm）で、現実解はドローン測量委託(2〜3cm, ¥300〜800万)か富士SW
  保有データ照会(AC公認MODの前例あり・打診無料)。近接はMSFS型のオルソ×微細
  ディテール合成+three-hex-tilingが定石で、これが情報の天井を超える唯一の
  リアルタイム手法。
- 提案を3層戦略（Phase 0: native直結の情報回収 / Phase 1: SR+評価ゲート刷新 /
  Phase 2: 近接ディテール描画 / Phase 3: 実データ更新）として
  [docs/research-ground-resolution-strategy-2026-07-16.md](docs/research-ground-resolution-strategy-2026-07-16.md)
  に記録。docs/README.md索引を全11件へ補完。BACKLOG P2へ5項目追加。
- 検証: ドキュメント作成のみでコード変更なし。テスト・ビルドは未実行（対象変更なし）。
- 未解決: Phase 0〜2の実装、富士SWへの照会実行、ドローン見積・茂原10cm照会、
  RealPLKSR実測A/B（DISTS/無参照IQAの実測値取得）。

## 2026-07-16 09:26 — 富士の手修正エッジを鮮明なベクトル線として表示

- 08:54 ユーザー要望を「地図全体の測量精度」より「トラック上の線をくっきり表示」へ再設定。
- 08:56〜09:05 Stegerのサブピクセル曲線検出、勾配誘導structure-preserving SR、航空画像の
  道路エッジ/標示抽出研究を調査。線位置と写真の質感を別レイヤーにする方針を採用。
- 既存白線シェーダーが全周一律左右6.5mで、富士の手修正済み非対称・可変幅エッジに
  追従していないことを確認。SRタイルより下にあり隠れる点も設計上の問題と判断。
- 09:06〜09:18 左右各2,280点・2m間隔のエッジをエクスポートし、0.20m幅、路面+55mm、
  描画順8の独立リボン2本として実装。SR/Google画像より上で `Track lines` に連動。
- 生成: `public/data/tracks/fuji/road_edges.json`（元定義SHA-256と用途を記録）。
- 検証: 対象3ファイル22件PASS、全34ファイル290件PASS、TypeScript+Vite本番ビルドPASS。
  既知のThree.js非index警告2件以外に新規エラーなし。
- Playwright実画面: `shizuoka_x2` 上で左右各4,558頂点、ON表示/OFF非表示、console/page/
  HTTPエラー0。スクリーンショット目視ツールはWindows sandbox helper障害のため未完了。
- 記録: `docs/research-track-line-enhancement-2026-07-16.md`、`docs/README.md`。
- 未解決: 線種（白線/舗装端/縁石/ピット線）の区間別分類と、実画面の人間による最終目視調整。
- 14:06 ユーザー添付の近景で、白線が強く発光して赤白縁石境界も軟らかい問題を再評価。
- 原因: 全画面Bloomが閾値0.62・強度1.8で通常の白ペイントまで拾い、縁石シェーダーも
  `fwidth` 平滑化を最大0.4まで許して近景境界を広げていた。
- `Effects.ts`: Bloom閾値0.92、平滑化0.025、強度0.7へ変更。通常白を発光対象から外し、
  ブレーキ灯等のHDR発光は維持する設計。
- `curbBuilder.ts`: 赤白境界AAを最大0.055へ制限し、白/赤輝度をBloom閾値以下へ調整。
- 検証: 対象2ファイル8件PASS、全34ファイル290件PASS、本番ビルドPASS。Playwrightで
  SR近景/俯瞰、左右線2メッシュ、縁石3メッシュ、console/page/HTTPエラー0を確認。

## 2026-07-16 08:51 — 富士SRコリドータイルをReplay表示へ接続

- 08:31 ユーザー要望「新たな画像を今回のコンテンツで表示」に対応開始。
  `agent-browser` / dev-server検証スキルを確認。CLIが環境にないため既存Playwrightへ
  フォールバックする方針を明示。
- 08:40 `EnhancedCorridorGround.ts` を追加。119枚のマニフェストを読み、
  視錐台内を最大6並列でロード、外れたタイルを即時解放、既存ground drapeと同じ
  road/terrain高さへ投影。透明WebPは静岡20cm下地へ15mm上で重ねる。
- 08:44 Satellite選択肢 `静岡 20cm SR`（ID `shizuoka_x2`）を追加。
  availability probeは画像ではなく `satellite_corridor_x2/manifest.json` の
  application/jsonを確認。URL `?sat=shizuoka_x2` にも対応。
- Google Ground有効中はローカルSRを非表示・GPU解放し、Google無効時に選択状態を
  復元する排他制御を追加。クレジットは元のVIRTUAL SHIZUOKAメタデータを継承。
- 08:44 対象テスト33件PASS、`npm run build` PASS。
- 08:49 Playwright実画面検証PASS: SR選択をUIに表示、119枚認識、初期視点12枚ロード、
  通常20cmへ切替時0枚、SR復帰時6枚+6枚ロード中。console error/page error/
  HTTP失敗/Google API通信はすべて0。
- 同一Top視点の通常/SRスクリーンショット差分は、3D表示領域で18,265px
  （1.691%）。ボタンUIだけでなく地形表示が変化していることを確認。
- スクリーンショットのローカル閲覧補助はWindows sandbox helper障害で失敗したため、
  目視確認とは記録しない。DOM、Three.js状態、ネットワーク、画素差で検証。
- 08:53 全テストスイート33ファイル・287件PASS、本番ビルドPASS。検証用Vite
  （127.0.0.1:4174）は所有PIDとコマンドを確認して停止、ポート解放を確認。
- 変更:
  `src/engine/track/EnhancedCorridorGround.ts`,
  `src/engine/track/EnhancedCorridorGround.test.ts`,
  `src/replay/satelliteVariants.ts`,
  `src/replay/satelliteVariants.test.ts`,
  `src/engine/track/TrackBuilder.ts`,
  `docs/imagery-enhancement-fuji-2026-07-16.md`。

## 2026-07-16 08:16 — 富士20cmオルソのトラック周辺2×高解像度化

- 08:01 ユーザー要望に基づき、Googleタイルを加工せず、派生加工可能なVIRTUAL
  SHIZUOKA 20cmオルソだけを入力にする方針で
  `pipeline/enhance_track_corridor.py` と単体テストを追加。
- 08:03 ホームストレート・中盤・終盤の3地点で初回Real-ESRGAN検証。GAN強度88%は
  全地点でLanczosよりPSNR/SSIMが低く、そのままの採用を中止。
- 08:05 0.05刻みの強度探索を追加。強度25%では3地点すべてでLanczosより改善
  （PSNR +0.84〜1.11dB、SSIMも全地点改善）。許容条件内の最大値50%を採用。
- 08:07〜08:15 RTX 3090のローカル推論で、中心線80m以内を高品質、80〜140mを
  フェードする全コリドーを生成。1024px WebP 119枚、16.84MB、
  出力格子0.1096m/px。Google API呼び出し/課金は0。
- 08:16 マニフェストと全119ファイルを検査。104枚に透過フェード、15枚は完全不透明、
  alpha全体0〜255、出典/ライセンス/SHA-256/ネイティブ情報解像度を保持。単体テスト
  2件PASS。比較画像のローカル表示ツールはWindows sandbox helper障害で未確認。
- 追加・生成:
  `pipeline/enhance_track_corridor.py`,
  `pipeline/test_enhance_track_corridor.py`,
  `public/data/tracks/fuji/satellite_corridor_x2/`,
  `docs/imagery-enhancement-fuji-2026-07-16.md`。
- ログ運用を `CLAUDE.md` に必須化し、配下の他PJにも適用。
  別リポジトリ用コピー元 `docs/CLAUDE-worklog-policy-template.md` も追加。
- 未解決: タイルは生成済みだが、Replayで視錐台内だけロードして地形へ重ねる
  ランタイムローダーは未接続。真の10cm情報が必要なら正規10cmオルソ/測量が必要。

## 2026-07-15 — 営業用デモ動画の作成（demo_fuji_3dreplay.mp4）

- 事業ディベートの「W1スクール向けデブリーフ基盤」ピッチに沿った61秒デモ動画を作成:
  リポジトリ直下 `demo_fuji_3dreplay.mp4`（H.264 1280x720 30fps 20MB）
- 構成: タイトルカード →①TVカメラ/ホームストレート ②チェイス/T1フルブレーキ(188km/h→)
  ③シネマティック/100R ④⑤チェイス/ダンロップ・セクター3ゴースト比較(L5ベスト vs L2)
  ⑥TV/最終コーナー→ エンドカード。各シーンに日本語キャプション、シーン間フェード
- 作り方: [demo_video.mjs](demo_video.mjs)（Playwright headless Chrome+swiftshader、
  `__replayStore.seek()` を1/30秒刻みで決定論コマ送り→JPEG 1680枚）→ scratchpadの
  encode_demo.py（cv2でカード合成・フェード・mp4v）→ imageio-ffmpeg同梱ffmpegでH.264化。
  probe用 demo_probe.mjs も残置（*.mjsはgitignore）
- 撮影時のUI調整: カメラ/レイヤーパネルとTelemetryトグルをCSS非表示、ゴーストはlap2を
  storeから直接ロード、テレメトリパネルは閉（チェイス視点で車を隠すため）
- 学び: Playwright同梱ffmpegはvp8のみ・image2/pipe非対応で流用不可。
  imageio-ffmpeg の静的ffmpeg(libx264)が確実
- 未解決: 車両モデルが黄色箱のまま(BACKLOG P2のメタデータ/車番表示と車種見た目改善が
  営業動画の質を直接上げる)。road面がやや灰色(既知のTTマテリアル課題)

## 2026-07-14 — Claude×Codex 事業性ディベート3ラウンド（収束・記録化）

- Codex (gpt-5.6-sol, xhigh) と3ラウンドの批判的討論を実施し、全文を
  [docs/business-debate-2026-07-14.md](docs/business-debate-2026-07-14.md) に保存
- **最終判定: 条件付きYes 58%**（確信度軌跡 40%→55%→58%で収束）。勝ち筋は
  「スクール/コーチ向け・カメラ任意のデブリーフ運用基盤」。3D単体B2C(なし85%)、
  VC型(なし90%)、ブートストラップ事業(あり75%)
- 主要な争点処理: 非RTKで絶対P95≤0.5mは不可→「認定トラック形状」と「軌跡信頼度」を
  分離、同一ロガー相対比較P95≤0.5mを検証目標に / 営業ゲートを個人リソースに合わせ緩和
  （ただし外部成果のみカウント、開発逃避の防止策つき）/ 出口はExit-aware非Exit-driven
  （デジスパイス・AiM代理店・ワンスマ・SIM事業者等の候補と成立条件を列挙）
- Go/Killゲート: 08-14接触25/面談5 → 09-30書面合意2/入金1 → 11-14有料パイロット3 →
  **2027-01-14 最終Go/Kill（3社有料・2社リピート・準MRR15万円、2社再購入なしならKill）**
- BACKLOG追加3件（2点位置補正+軌跡信頼度、共有URL、イベント一括インポート）、
  docs/README.md 一覧更新
- 未解決: 「市場接触でしか解消できない不確実性」11項目（討論記録の末尾に列挙）
- **R4追加（ユーザー発案の2論点）**: プロモーター連携=第2本線化せず(5%)、ワンメイク/
  参加型主催者をW2営業先(72%)として20組織CRMに4枠追加。権利は3層分離（クリーンコア/
  イベントデータ/公式ビジュアル）で方針B維持。特許=4案一括は反対、案1+2統合の
  「不確実性伝播」1件のみINPIT相談→調査→条件付き日本出願（総合58%は不変、
  今4件出願や上位カテゴリ専用開発に走れば50-52%に低下との判定）

## 2026-07-14 06:10 — docs/README.md 新設（インデックス+運用ルール）

- [docs/README.md](docs/README.md) を作成: プロジェクトのドキュメント地図
  (WORKLOG/BACKLOG/docsの役割分担)、docs内一覧表、追加時の命名ルール
- 再整備ルールを明文化: 陳腐化文書は削除せず `docs/BK/` へ移動(理由+後継リンク付き)、
  同種5件超でサブフォルダ化、再整備はWORKLOGに記録。現状は1件のみで再整備不要

## 2026-07-13 15:00 — ペルソナ評価P0対応4件 + バックログ整備

- ユーザー指示: P0は対応、P1-3はバックログ化して今後育てる
- **[BACKLOG.md](BACKLOG.md) 新設**: P1(分析コア)/P2(観戦体験)/P3(オンボーディング)を
  出典・追加日つきで登録。運用ルール(完了しても消さない等)を冒頭に記載
- **P0-1 BESTラベル修正**: HUDのBESTが現在ラップのタイムを表示していた。
  [dataLoader.ts](src/replay/dataLoader.ts) に `getVehicleBestLap`(is_best_vehicle優先、
  min正タイムfallback)を追加し、[Hud.tsx](src/ui/Hud.tsx) を
  「LAP TIME=再生中ラップ / BEST=車両セッション最速+L番号」の2行に分離。単体テスト4件追加
- **P0-2 衛星切替「無反応」**: エージェント報告の404は誤認(全200)。真因は
  **OSM Featuresの平面ポリゴン(fuji: wood16+grass101)が衛星写真を全面被覆**していたこと。
  レイヤーOFFで Esri↔Bing のピクセル95.6%が変化することをpixdiffで確認。
  対応: [replayStore.ts](src/state/replayStore.ts) で showOsmFeatures をデフォルトOFF
  (トグルで復帰可)。写真ベースになり見た目も大幅改善
- **P0-3 頭出しボタン**: [ReplayControls.tsx](src/ui/ReplayControls.tsx) に ⏮
  (Restart from the beginning) を追加。↺(ループ)との混同を解消
- **P0-4 浮遊する白・赤の板**: 正体は [TrackBuilder.ts](src/engine/track/TrackBuilder.ts)
  のレガシー手続き縁石(曲率閾値ポイントに孤立配置される赤白BoxGeometry)。
  レイヤー総当たりスクショで3D Road子要素と特定し、buildKerbs を削除
  (正規の縁石は overlays.json / Track lines 側)。computeCurvatures はブレーキマーカー用に存続
- 検証: tsc OK / vitest 264件全パス / Playwright実機で4件とも動作確認
  (HUD表示・⏮で0秒復帰・衛星切替82%ピクセル変化・板消滅・Barber退行なし)
- レポート [docs/persona-review-2026-07-13.md](docs/persona-review-2026-07-13.md) に
  事実訂正の追記(404誤認・板の正体)
- 未解決: BACKLOG.md の P1〜P3(デルタグラフ、テレメトリ全ch露出、メタデータ表示ほか)

## 2026-07-13 13:50 — ペルソナ別プレイテスト評価（Opus 4.8サブエージェント×4）

- 4ペルソナ(ドライバー本人/エンジニア/ファン/初心者)のサブエージェントが
  Playwright headless Chromeで実際にプレイして課題を洗い出し
  (富士 osaki #101 4ラップ + Barber GR86 6台、計150+スクショ、consoleエラーゼロ)
- 成果物: [docs/persona-review-2026-07-13.md](docs/persona-review-2026-07-13.md)
  (4ペルソナ詳細レポート + 横断分析 + P0〜P3改善ロードマップ)
- 主な発見:
  - **実質バグ2件**: BESTラベルが現在ラップのタイムを表示 /
    衛星レイヤー切替が404(参照 `satellite.jpg` vs 実ファイル `satellite_z19.jpg`)
  - 分析コアの欠落: デルタ対距離グラフ・セクター比較表・グラフホバー数値
    (steer/gear/G等のデータは記録済みでUI未露出 — UI作業のみで解消可能)
  - 全ペルソナ一致: Cockpitカメラが使用不可 / TV・Cinematicで自車を見失う /
    ツールチップ皆無 / 車両が生ID+黄箱表示
- 未解決: 改善案の実装はすべて未着手(レポートのロードマップ参照)

## 2026-07-13 12:00 — ユーザー指摘3件: コース外の高さ飛び/縁石/中央の赤線

- **① コース外で車が高所に飛ぶ — 真犯人はACリボンへのフォールバック**:
  groundedY の旧順序は 路面メッシュ→ACリボン(ac_overlay CSV)→解析値。実測で
  **ACリボンがTT世界と垂直最大4mズレ**(t=90s: ribbon -18.79 vs mesh -22.79)と判明。
  コリジョンを外れた瞬間リボン高さへジャンプしていた。
  修正([ReplayScene.ts](src/engine/ReplayScene.ts)): 順序を
  **路面メッシュ → 地形ハイトフィールド(TerrainSampler、O(1)、±8m sanity) → リボン → 解析値** に。
  [TrackBuilder.ts](src/engine/track/TrackBuilder.ts) に `terrainHeightAt(x,z)` を公開。
  検証: コース脇15/25/40mで連続した地形高さを確認(-2.26→-2.87→-3.23m 等)、verify PASS
- **②③ 縁石が見えない/コース中央の赤い線 — RACING_KERBの雑スタックが原因**
  (Opus 4.8サブエージェントに委譲して解決):
  TT ROADノードは middle+border カーブを出力し、RACING_KERB がその全部(中央線含む)に
  縁石を生成→中央の赤線+全周の赤帯。修正は
  [build_track_blender.py](tracktools-kit/automation/build_track_blender.py):
  - RACING_KERB を道路スタックから除去
  - **曲率からコーナー自動検出**(24m弧の方位変化→局所半径、閾値170m、±12m延長、16m未満破棄)
    → 12コーナー×両側=24縁石メッシュ、ストレートは無し
  - 縁石カーブは道路エッジにオフセットし、地形BVHレイキャストで高さ取得
  - kerb type=stepped(menu enum: smooth=0/stepped=1/custom profile=3)、幅1.15m
  - **赤白ストライプは16×16画像テクスチャ**(手続きノードはglTFに乗らないため。
    NEARESTサンプラでシャープさ維持、glb内に埋め込み確認済み)
  - UV規約: 縁石のUは長さ方向(~0.5/m)、Vは幅方向
- 既知(今回のスコープ外): 大きくシークした直後はチェイスカメラの遷移が地形を突き抜けて
  数秒間地中になることがある(車自体は正常)。気になるならカメラのテレポート処理を今後追加
- テスト: tsc OK / 260件全パス / verify PASS(4/4)

## 2026-07-13 10:30 — 要対応作業の一括処理: センター破線除去/読み込み高速化/テクスチャ/自動化

- ユーザーと次期作業の優先度を整理し、着手可能なものを一括実施
- **センターライン(破線)除去**: 正体はTT MARKINGSの `middle line type`(デフォルト dashed)。
  [build_track_blender.py](tracktools-kit/automation/build_track_blender.py) で `none`(enum 5)に設定。
  エッジ白実線(border: full)と縁石は維持。リビルド→verify PASS→Playwrightスクショで破線消失を確認
- **読み込み速度**: エージェント計測で犯人特定→3本柱で修正
  1. `projectPointToCenterline`(全セグメント線形走査)がドレープで~17秒
     → [interpolation.ts](src/replay/interpolation.ts) に**空間グリッド+拡張リング探索**を実装
     (結果は総当たりと完全一致、2000点の等価性テスト付き)。
     注意: 遠方クエリはリング走査がO(距離²)化するので**グリッド外±8セルは総当たりへフォールバック**
     (これを入れないと逆に悪化した — CPUプロファイルで発見)
  2. `sampleEdgeAlt`/`sampleTrackAltitude` の線形走査 → 二分探索化
  3. **scene.glb(15.7MB)+collision の遅延ロード** — Drive on AC 初回有効化時にのみ取得
     ([ReplayScene.ts](src/engine/ReplayScene.ts) `ensureAcAssets()`)。
     verify_track.js は「先に有効化→acSceneReady待ち」の順に修正(旧順序だとデッドロック)
  4. **ラップ切替のフル再構築を解消** — [ViewerCanvas.tsx](src/engine/ViewerCanvas.tsx) のeffectを
     [track] と [lap] に分割、`ReplayScene.setLap()` 新設(WebGL/ドレープ再構築なし)
  - 実測(実GPU・devサーバ): ダイアログ1.1s / 初期ビルド4.7s / ラップ切替 7.8s→2.7s /
    再生中フレーム平均6.6ms。ヘッドレス計測はSwiftShaderで数値が膨らむ点に注意
- **verify失敗の落とし穴**: `--race` 無しだと車が原点に置かれ mesh=NULL で全滅する。
  必ず `--race fuji_aim_01` を付ける
- **テクスチャ**: textures_cc0 が空だった(道路・草地が単色の原因)。
  fetch_textures.py の Asphalt025 が404 → **Asphalt031 に更新**して取得、テクスチャ込みリビルド
- **ワンコマンド化**: make_track.py に 1b ステージ追加(editorのroad.edgesがあれば
  edges_to_tuned.py を自動実行)。**手順書 [PIPELINE_ja.md](tracktools-kit/automation/PIPELINE_ja.md) 新規**
  (ファイル命名規約: scene.glb=自作TT世界が正、*.mod.glb=AC退避で不可侵、を明文化)
- テスト: 260件全パス(等価性・ベンチ・回帰テスト追加分含む)
- 未解決: ランオフ多角形レイヤー(次の大物)/ 車両3DCG差し替え(モデル選定待ち)/
  実動画との細部ズレ検証(素材待ち)/ 本番配信向けglb圧縮(meshopt/KTX2)は未着手

## 2026-07-13 00:20 — ユーザー指摘「写真とズレてる」の真因特定と修正: 非対称の切り捨て

- ユーザー報告: ビューアで道路が衛星写真から2〜2.5mズレて見える(エディタでは一致していたのに)
- 私の一次調査はシロ続き(画像3種の相互シフト0,0 / 投影式全系統一致 / origin3ファイル一致 /
  detailTextureはノイズのみ / buildSatelliteGroundは頂点単位で厳密)
- **独立エージェント(まっさら文脈)に事実一式を渡して監査させた結果、数値で真因特定**:
  - scene.glbの道路はセンターラインに対しズレ0.000m(ビルドは無罪)
  - **実路面はセンターラインに対し左右非対称**: (左-右)/2 が全周66%で|2m|超、
    ピットストレート実測 -4.9〜-5.3m(実路面は中心線の右)
  - エディタは track.json の非対称エッジで描くから写真と一致。
    **edges_to_tuned.py が幅=L+Rに潰し「中心線は動かさない」設計だったのが犯人**
    (=以前ノイズで却下したリセンターが、手修正済みのクリーンなデータでは必須だった)
  - 副産物: TTビルド出力が scene.glb を上書きしており「Drive on AC」の表示もTT道路だった
    (AC MOD世界はscene.mod.glb側に退避済み。名前分離は要検討)
- **修正**: edges_to_tuned.py が `centers[]`=(左-右)/2 も出力 →
  build_track_blender.py `effective_centers()`+`recenter_points()` で
  カーブ/コリジョン生成前に中心線を左法線方向へ変位(fuji: -7.64..+6.54m)
- **検証**: リビルド→verify PASS(高さ±0.25m 4/4)。断面測定でメッシュ中心=目標値に一致
  (dist0: -4.860m vs 目標-4.86 / dist1099: +1.750m vs +1.75 — cm精度)
- 残(既知の軽微): ビューアの手続き回廊・白エッジ線シェーダは対称幅13m前提
  (衛星ドレープ表示の残ズレ源。エッジをviewer track.jsonへ書き出す改修で解消可能)/
  scene.glb 名前衝突の分離 / ランオフ層
- 教訓: 「中心線を動かさない」判断はノイズ期の暫定だった。データが人手修正でクリーンになった
  時点で前提を見直すべきだった(独立監査が有効に機能した好例)

## 2026-07-12 22:50 — エディタ→実コンテンツの一気通貫が開通(未実装だった橋渡しを実装)

- ユーザーがエディタで下書き→手直し→保存を完了(track.json 22:38、幅 11.8/14.3avg/22.8m)。
  トレース用ワンタッチ切替(Tキー+📷ボタン、E2E検証済み)も追加した上での作業
- **橋渡し実装**(WORKLOG 7/10 残タスクの解消):
  - [edges_to_tuned.py](tracktools-kit/automation/edges_to_tuned.py) 新規 —
    track.json road.edges(2280駅×2m) → kit/tuned.json "widths"(dist-keyed)。
    幅=left+right(対称)、非対称は track.json に温存
  - [build_track_blender.py](tracktools-kit/automation/build_track_blender.py) —
    TT視覚道路にも点ごと幅を反映: make_centerline_curve に radii 追加
    (radius = width/base、ROADの実効幅=width×radius の実証済み仕様を利用)
- **フルビルド+検証**: make_track --force --verify → TT build OK
  (variable width 11.8-22.7m / radius 0.910-1.743)、scene.glb + road_ac_local.glb 配備、
  **verify PASS(t=8/40/90/150s、car-vs-mesh ±0.25m)**
- 運用手順(エディタで幅を更新したら):
  `python tracktools-kit/automation/edges_to_tuned.py --track fuji` →
  `python tracktools-kit/automation/make_track.py --track fuji --tracktools <TT.blend> --force`
- 残: ランオフ多角形レイヤー / (保留)リセンター橋渡しv2 / edges_to_tuned の make_track への統合

## 2026-07-12 15:50 — 追撃: ハイライトが「シーンから除去されていた」2つ目のバグ

- クリック選択修正後もユーザーには黄色が見えない → **ピクセルまで検証する方針に変更**
  (内部状態だけのテストが穴だった)
- 真因2: `sync()` が handles 配下を「エッジ点とマーカー以外全削除」する実装で、
  後付けの rangeHighlight も**起動直後に scene から外されていた**(`inScene:false` を実測)。
  keep リストに rangeHighlight を追加して修正
- 修正後、スクリーンショットで**黄色帯の描画を確認**(dist 500〜2500・1001点)。
  教訓: UI機能の検証は「状態」でなく「描画」まで見る(snap_rangevis.mjs に固定化)

## 2026-07-12 15:30 — 区間選択が効かないバグ修正(クリックがドラッグ判定に食われていた)

- ユーザー報告「選択しても点の色が変わらない」→ Playwrightでクリックをシミュレートして特定:
  **エッジ点の近くで mousedown すると「ドラッグ開始」扱いになり、動かさず離しても
  クリック選択に到達せず終了**していた(直したい暴れ=エッジ付近をクリックするので高頻度)。
  修正: 変異なし・移動≤5px のエッジ掴みはクリックとして選択処理へフォールスルー
  ([tools.ts](track-creator/editor/tools.ts) pointerUp 再構成)
- あわせて選択フローに常時フィードバック追加: 「始点を選択 — 終点をクリック」
  「区間を選択しました(N点・左)」等をステータスバーに表示
- 修正後の自動検証: 1クリック目=始点1点ハイライト、2クリック目=101点選択+ボタン有効化 ✓
- 関連UX(同日): 点削除の期待合わせ — 削除した点は**マーカーごと非表示**
  (値は両隣の中間に。Undo/モード切替/区間操作で再表示)。右クリック削除対応、
  当たり判定2倍、全アクションにステータス通知
- 資料: エッジ区間操作の使い分けガイド(HTML Artifact)を作成 — 症状パターン別
  (膨らみ→直線化 / ギザギザ→平滑化 / トゲ→右クリック / 全滅→基本幅、
  コーナーで直線化しないアンチパターン図解)

## 2026-07-12 14:30 — 解析画像オーバーレイ + エッジ区間操作ツール(手直しの中核)

- ユーザー指摘対応①「オーバーレイも高解像度に」: 従来の下絵は**GSIタイルz17(~1.2m/px)で
  解析画像より粗かった**。「解析画像」モードを追加([overlay.ts](track-creator/editor/overlay.ts)
  `AnalysisImageOverlay`) — 推定ソース(Esri/Bing)と連動し、検出器が見るピクセルそのものを
  下絵表示。Mercator非線形は8ストリップ分割で補正
- Bing georef検証: 位相相関で **Esri vs Bing ズレ(0,0)px** → メタは正しい。Bingでの
  ホームストレート悪化は座標でなく検出器挙動 → **検出器チューニングはここで打ち切り**。
  運用 = 下書きはEsri(2箇所以外GOOD実績)、下絵確認はBing、残りは手直し
- **エッジ区間操作ツール実装**([tools.ts](track-creator/editor/tools.ts)): コース幅モードで
  路面を2クリック→片側エッジ区間を黄色選択(閉ループ短弧・Esc解除)→
  **直線化**(選択外アンカー間の弦へ法線投影)/**平滑化**(3点移動平均×2)/**基本幅に**。
  E2E検証: 200m区間に4〜10mのノイズ注入→直線化で隣接差0.02mまで収束、smooth/reset正常、
  もちろんUndo対応(snapshot)
- 全60テスト+tsc パス。スクリプト: snap_edgeops.mjs
- 次: ユーザーがEsri下書き→手直し→保存 → Blender橋渡しv2(リセンター既定化) → ランオフ多角形

## 2026-07-12 13:45 — ユーザー目視×2箇所への対処: 複数注釈UI+Bing z19ソース切替

- ユーザー目視: 1コーナー○ / ホームストレート×(観客席側に食いつき) / 130R×(→白線で止まる
  仕様どおりの可能性が高い。ランオフは別レイヤー予定である旨を説明済み)。それ以外はGOOD
- **制限区間UIを複数行テキストエリア化**(1行=`開始,終了,左|右,最大m`、日本語/英語の側名対応、
  不正行はエラー表示して解析を止める)→ ピット左右両側に注釈可能に
- **画像ソース切替を追加**: Esri z19は placeholder(この地域は z18=0.49m/px が上限)と判明。
  一方 [satellite_bing.jpg](public/data/tracks/fuji/satellite_bing.jpg) が**ネイティブ Bing z19
  (7214×7374, 0.24m/px, SR加工なし)**で既存だったため、`satellite_bing_meta.json` を作成し
  エディタに「標準(Esri)/高解像度(Bing)」セレクタを追加
- E2E(Bing+白線+左右注釈、READ-ONLY): ピット右 p50 **7.12m**(真の走行面ハーフ幅に到達)。
  トレードオフ: 高信頼数は減少 L:1837 R:1562(高解像度で撹乱要素も解像するため)、
  幅 min 6.5m / absMax 30.7m の外れ值あり → プレビュー目視+ブラシ修正の対象
- 注: Google Maps 画像は規約上、形状抽出(=本解析)に使用不可のため不採用。合法代替の
  GSI 航空写真は z18 相当で解像度メリットなし
- 残: ユーザーがエディタで Bing+白線優先を目視→適用→保存 → Blender橋渡し。
  その後: エッジ線編集ツール / 橋渡しv2(リセンター) / ランオフ多角形レイヤー

## 2026-07-12 13:00 — Stage 2 実装: 白線ファースト検出(走行面をペイントラインで決める)

- ゴール確認(ユーザー合意): 「人間はエッジ線を直すだけ、あとは全自動」。走行面=左右エッジ線(リボン)、
  ランオフ=閉多角形レイヤという2層データモデルで進める
- **Stage 2 実装(全60テストパス・tsc OK)**:
  - [stripeEvidence.ts](track-creator/src/image-analysis/stripeEvidence.ts) 新規:
    輝度リッジ検出(両隣より明るい持続ピーク×道路より明るい×内側が路面色)で白線強度0..1
  - [widthEvidence.ts](track-creator/src/image-analysis/widthEvidence.ts):
    `objective: "boundary"|"stripeFirst"` 追加。stripeFirstでは境界候補スコアに白線ボーナス
    (STRIPE_SCORE_WEIGHT=9)を融合 — 白線が見えれば白線が勝ち、無ければ境界にフォールバック
  - UI「白線優先」チェック追加、`analysisHints.objective` として永続化
  - 合成画像テストで実証: 白線6m/舗装12mの合成路 → boundary=12m、stripeFirst=6m、線なし→一致
- **fuji実データE2E(READ-ONLY・未保存)**:
  - 白線のみ: 高信頼 L:2006 R:2155(境界模式比+8%/+32%)、低信頼18%→**11.5%**、
    ピット右 p50 15→12.5m(ピットレーン側の白線を拾う区間が残る)
  - 白線+ピット注釈(4224〜598右9m): ピット右 p50 **10.09m** / max 12.06m。
    全体 p50 13.82→15.25m(富士のストレート実測~15mと整合)、absMax 25.8m(T1/100R系の
    舗装ランオフ地帯 — ランオフ層 or 追加注釈 or ブラシ手直しの対象)
- スクリプト: snap_stage2_analysis.mjs(STRIPE=1/CORRIDOR=0|1 で切替、保存はしない)
- 残タスク(ゴールまで): ①エッジ線編集ツール(区間の直線化/平滑化/単純化)
  ②Blender橋渡しv2(クリーンデータでのリセンター既定化) ③ランオフ多角形レイヤー
  ④エディタで解析→保存→Blender一気通貫の実走確認

## 2026-07-12 08:30 — 幅解析 Stage 1 実装(corridor制約+信頼度永続化+低信頼補間)

- 背景: リセンター版ブリッジ(apply_edges_recenter.py)は幅データの汚れ(ピットレーン誤食いつき)を
  形の歪みとして露呈させ、ユーザー判定は「一律13mの方がマシ」→ restore_centerline_original.py で復元。
  根本対策として Plan サブエージェントで設計([白線ファースト検出]が本命 = Stage 2)
- **Stage 1 実装(track-creator、全テスト58件パス・tsc OK)**:
  - `CorridorConstraint`(dist範囲+側+maxOffset、ラップ対応)を解析オプションに追加
    → [widthEvidence.ts](track-creator/src/image-analysis/widthEvidence.ts) `effectiveMaxOffset()`
  - 低信頼区間を「現在値(6.5m)へ戻す」から**隣接採用値の線形補間**へ変更
    → [proposalBlend.ts](track-creator/src/image-analysis/proposalBlend.ts) `interpolateAcrossLowConfidence()`(閉ループの継ぎ目対応は回転方式)
  - 適用時に **confidenceLeft/Right を track.json に永続化**(従来は捨てていた)
    → [analysisController.ts](track-creator/editor/analysisController.ts) / `EdgeOffsets` 拡張
  - エディタUIに「制限区間」行を追加(ピット注釈、`def.analysisHints.corridors[0]` と往復)
- **実データ E2E 検証(snap_stage1_analysis.mjs、READ-ONLY・未保存)**:
  ピット右 p50 15m→**10.0m** / max 18→11.6m(corridor 4224〜598m・右・9m が効いた)。
  全体 absMax 28.75→24.0m(残る広幅はT1/100R等の舗装ランオフ=Stage 3 でランオフ層の材料)。
  信頼度 2280点永続化、低信頼 414点(18%)は補間に。track.json は未保存(ユーザー確認待ち)
- 方針決定(ユーザー確認済み): **ランオフも再現対象**。2層構造 = 走行面(白線検出→ROAD)+
  ランオフ(アスファルト境界検出→TT **EDGE**、interface実機ダンプで適性確認済み)
- Blender側の今日の教訓: 線カーブの **Bevel Depth≠0 だと ROAD が素通し**(ガイドに追記済み)/
  ROAD実効幅 = road width × 点radius(検証済み)/ Z offset 0 は地形とZファイト
- 残: Stage 2(白線ファースト検出 stripeEvidence.ts)、Stage 3(EDGEランオフ層)、
  エディタで再解析→保存→Blender橋渡し(apply_width_from_analysis.py は無変更で互換)

## 2026-07-11 07:17 — ユーザー指摘の「道路が切れる」を修正: 原因は地形への沈み込み

- 07:01 配備版はユーザー確認で**コーナーの道路が切れて見える**不具合(スクリーンショット提供)。
  即座にネイティブ版へロールバックした上で診断
- 私の事前チェックの反省: 連結成分・境界エッジの**トポロジー検査は「メッシュがつながっているか」
  しか見ておらず、「描画で見えるか」を見ていなかった**。巻き順(裏返り)は正常 → 高さ比較で
  **TT道路の頂点の56%が地形メッシュより下(最大63cm)** = 地形に埋まって切れて見えるのが真因
- 修正([build_track_blender.py](tracktools-kit/automation/build_track_blender.py)):
  Track Tools の **TERRAIN_SHAPE** を地形側に追加(target=道路カーブ、地形を道路に整合させる
  本来の想定手順が抜けていた)+ ROAD `Z offset` +0.08 / TERRAIN_SHAPE `Z offset` -0.10
  → 沈み込み 56.3%→**0.3%**、10cm超 31.6%→**0.0%**(最深 -9cm)
- 検証手法も強化: Blender ヘッドレスの Cycles レンダリングで**曲率上位コーナー4箇所の
  真上ビューを自動撮影**して目視確認(スクラッチ exp1/render_top.py)→ 切れ・ギザギザ解消を確認
- 再配備 + `verify_track.js` PASS 4/4 (d=0.000)。
  既知の無害な警告: ROAD(shrinkwrap→地形)と TERRAIN_SHAPE(→道路)の相互参照で
  depsgraph が dependency cycle を警告するが、出力は正常
- 教訓(次回から): GLB配備前チェックは「トポロジー+**沈み込み(sinkcheck)**+**コーナー
  レンダリング目視**」の3点セットにする

## 2026-07-11 07:01 — Phase 1 実験1・2 完了: TTマテリアルバグ完治+衛星幅解析を適用

- **実験1(GUIモード検証)→ 予想外の真因を特定して修正:**
  - GUIモードとヘッドレスのTTフルビルドは**完全に同一出力**(=ヘッドレス限定バグ説は棄却)
  - 診断の結果、評価済みメッシュにはマテリアルが正しく載っており、真因は **Blender 5.1 の
    glTFエクスポータが CURVE を realize する際にマテリアルスロットを落とす**こと
  - 修正: [viewer_export.py](tracktools-kit/automation/viewer_export.py) の `export_glb` に
    `_realized_copy` を追加(非メッシュは一時メッシュ化してからエクスポート)。
    → ヘッドレスTTビルドで asphalt/markings/kerb すべて正常エクスポート
  - コーナー連続性も数値検証: 道路は連結成分1・境界エッジ4560=2×2280駅(切れ目ゼロ)
  - 修正版TT scene.glb をビューワーに配備(旧版は scene.native.bak.glb)、
    `verify_track.js` **PASS 4/4 (d=0.000)**。**結論: GUIモード運用(B-1/B-2)は不要に**
- **実験2(衛星画像の幅解析)→ 適用・保存まで完了:**
  - Playwright E2E(snap_exp2_analysis.mjs)で実行: 提案2280点、高信頼 L81%/R88%、欠損0
  - 高信頼のみ適用(3856点)。幅: 一律13.00m → 中央値13.82 / 平均16.43 / 最大28.75m
    (最大はピットストレート、実態と整合)。地理院写真オーバーレイと目視でも一致
  - track.json 保存(バックアップ: track.json.bak-preanalysis)+ out/fuji.glb ビルド
- 実験結果は [gui_automation_design_ja.md](tracktools-kit/automation/gui_automation_design_ja.md) §6 に詳細記録
- 残タスク(Phase 1): 低信頼区間(~15%)の幅を手動微修正 / track-creator の edges を
  tracktools-kit の tuned.json widths へ橋渡しする変換(未実装)

## 2026-07-11 06:40 — Track Tools GUI自動化のレビューと確定設計をドキュメント化

- 「Track Tools を Claude の Computer Use で操作できるか?」の検討を確定版として文書化:
  [tracktools-kit/automation/gui_automation_design_ja.md](tracktools-kit/automation/gui_automation_design_ja.md)
  - 結論: Computer Use は**不採用**(3Dビューポートの精密ドラッグに不向き・遅い・実行環境も別途必要)。
  - 採用: **B-1** GUIモードのBlenderをワンショット bpy 実行(`blender staging.blend --python`、`-b`なし)で
    ヘッドレス限定バグ(マテリアル伝播)を回避 / **C** 画像解析による幅決定 / **D** Playwright数値操作。
- レビュー中の発見: 前回「作る価値あり」と提案した**衛星画像からの幅解析は実装済みだった**
  (`track-creator/src/image-analysis/` + エディタの解析UI、信頼度付きプレビュー・高信頼のみ適用・テスト付き。
  fuji 用の satellite.jpg / satellite_meta.json も配置済み)。残タスクは実装ではなく実走検証。
- 未解決 / 次のステップ:
  1. 実験1: GUIモードで同一ビルドを実行し、ヘッドレス出力とマテリアル/急コーナー連続性を比較
  2. 実験2: fuji で画像解析→高信頼のみ適用→GLB→ビューワー照合(本線幅 ~13 m と乖離チェック)
  3. B-2(Blender常駐コマンドポート)は実験1で反復需要が確認できた場合のみ
- このファイル(WORKLOG.md)を新設。以後の作業はここに日時つきで記録する。
