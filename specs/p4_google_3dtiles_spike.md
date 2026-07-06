# P4仕様: Google Photorealistic 3D Tiles 検証スパイク

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 目的

Google Photorealistic 3D Tiles(フォトグラメトリ3Dメッシュ)が
Barber Motorsports Park と富士スピードウェイでどの程度の品質・カバレッジか、
「写実観戦モード」として本体に統合する価値があるかを**1〜2日のスパイク**で判定する。
本体アプリの置き換えではない。判定材料を作るのが目的。

## 前提知識

- ルートタイルセット: `https://tile.googleapis.com/v1/3dtiles/root.json?key=API_KEY`
- 無料枠: 月1,000ルートタイルリクエスト(1リクエスト≒3時間セッション)。
  スパイク用途では実質無料。
- **ToS上、タイルの保存・焼き込み・オフライン利用は不可**。ランタイム
  ストリーミング表示のみ。スクリーンショットは評価目的でOK(帰属表示を残すこと)。
- 推奨ライブラリ: `3d-tiles-renderer`(NASA-AMMOS製、three.js用。npmにある)。
  `GoogleCloudAuthPlugin`と`TilesFadePlugin`、グローブ表示系のサンプルが
  リポジトリにある: https://github.com/NASA-AMMOS/3DTilesRendererJS

## 成果物

`replay-viewer-v2/spike/g3d/` に**完全に独立した**ミニアプリを作る:

- 独自の`package.json`(three + 3d-tiles-renderer + vite)。
  **本体の`package.json`・`vite.config.ts`・`src/`には一切触らない**
- `npm run dev`(spike/g3d内)でブラウザが開き、以下ができること:
  1. URLパラメータ`?key=XXX`またはlocalStorage(`g3d_api_key`)からAPIキーを読む。
     キー未設定時は入力欄を出してlocalStorageに保存(キーをコードにコミットしない)
  2. `?track=barber`(既定)/`?track=fuji`で対象を切り替え
     - Barber: lat 33.53252, lng -86.61931
     - Fuji: lat 35.37170, lng 138.92560
  3. 3D Tilesをその地点中心に表示、OrbitControlsで操作
  4. **レーシングラインのオーバーレイ**: 本体の
     `../../public/data/tracks/{barber,fuji}/track.json`のセンターライン
     (points[]の lat/lng/alt)をECEFに変換してLineオブジェクトで重畳表示。
     タイルメッシュとのXY位置ズレ・高さズレを目視評価できるようにする
  5. 画面に帰属表示(タイルセットが返すcopyright文字列)を表示(ToS要件)
  6. `S`キーでスクリーンショットをダウンロード

## 評価レポート(スパイクの本体)

`specs/reports/p4/REPORT.md`(日本語)に:

1. **カバレッジ判定**: Barber/富士それぞれ、フォトリアル3Dメッシュ
   (フォトグラメトリ)の範囲か、粗い衛星ドレープ地形しか無いか。
   ズームレベル別のメッシュ品質のスクリーンショット
   (`specs/reports/p4/shots/`に保存)
2. **位置整合**: レーシングラインとタイルメッシュのズレ(XYで何m、高さで何m)。
   本体はEGM96ジオイド前提の標高、Google 3D TilesはWGS84楕円体高なので
   **ジオイド高の差(Barber約-30m、富士約+40m前後)が出るはず**。観測値を記録
3. **性能**: 表示時のFPSとタイル読み込み挙動(MacBook想定)
4. **統合難易度の所見**: 本体の独自ライティング/SSAO/車両描画と共存できるか、
   「観戦モード」として組むなら何が必要か、ToS制約で何ができないか
5. **判定**: 統合する価値あり/なし/条件付き、の推奨

## 制約

- APIキーが無い場合: キー入力UIまで実装し、`curl`等でエンドポイントの
  疎通だけ確認して「キー待ち」とレポートに書く(キーはユーザーが用意する)。
  実装とキー無しで出来る検証をすべて終わらせること
- スパイク品質でよい(テスト不要、型は最低限)。ただしREADME.md(起動方法)は書く
- 本体のnpmワークスペースに影響を与えない(spike/g3dは独立インストール)
