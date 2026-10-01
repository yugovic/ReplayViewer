# 富士GPS・CG位置ずれ調査：別端末・別担当者への引き継ぎ

## 要旨

最初に[経緯と採否](fuji-gps-investigation-history-2026-10-01.md)、次に[現在の方針](policy-gps-cg-alignment-2026-09-20.md)、数値が必要なら[完了報告](fuji-gps-accuracy-improvement-2026-09-22.md)を読む。Gitには表示用の2日10周データ、補正結果、修正コード、比較用の旧入力、選別した監査結果とSHA検証材料がある。クローンだけで現在の配布入力の同一性と計算済み結果を追跡できる。一方、原XRK・元動画・映像フレーム・元オルソ一式は公開Gitにないので、原媒体からの全工程再生成や真位置の独立検証までをクローンだけで完了したとは言えない。

## 5分で現状を掴む

1. このリポジトリの `master` を取得し、[経緯](fuji-gps-investigation-history-2026-10-01.md)で旧案の採否を確認する。9/20以前の数値は履歴値。
2. [完了報告](fuji-gps-accuracy-improvement-2026-09-22.md)の「全10周」「日による差」「映像同期」「映像から確定できる距離の限界」を読む。
3. リポジトリ直下で `python scripts/quality/verify-fuji-gps-handoff.py` を実行する。配布中の2登録JSONと依存入力のSHA、4周+6周、保存済み検証マニフェストの整合を標準ライブラリだけで検査する。これは当時のブラウザ・元XRK解析の再実行ではない。
4. CG表示を使う場合は `npm ci`、`npm run dev`。画面の「元GPS」は**前処理済み配布緯度経度**で、原XRKの未加工標本ではない。HUD/Gキーで補正と比較し、対象ラップと補正有効状態を確認する。

## Git内の証拠地図

| 調べたいこと | Git内の入口 | 注意 |
|---|---|---|
| 正式な結論・限界 | [完了報告](fuji-gps-accuracy-improvement-2026-09-22.md)、[方針](policy-gps-cg-alignment-2026-09-20.md) | 4.821mは2日の推定差であり真のGPS誤差ではない |
| 旧案の採否 | [経緯](fuji-gps-investigation-history-2026-10-01.md)、[作業ログ](../WORKLOG.md) | Git公開版の作業ログは最終実施と収録監査が中心。9月の試行はこの経緯文書で採否を追う |
| 入力・補正の同一性 | [SHAマニフェスト](../artifacts/gps-accuracy-2026-09-22/verification-manifest.json)、[wet登録](../public/data/races/fuji_aim_01/gps_registration.json)、[dry登録](../public/data/races/fuji_aim_2020_07_30/gps_registration.json) | いずれかの入力が変わったら保存補正を再推定する |
| 処理前後の監査 | [前処理](../artifacts/gps-accuracy-2026-09-22/preprocessing/rebuild-audit.json)、[境界連続性](../artifacts/gps-accuracy-2026-09-22/preprocessing/boundary-continuity-audit.json)、[旧版スナップショット](../artifacts/gps-accuracy-2026-09-22/baseline/) | baselineは旧処理の比較対象で、現行値ではない |
| 道路・推定・感度 | [道路修正](../artifacts/gps-accuracy-2026-09-22/road/reviewed-road-fixes.json)、[全周/CV](../artifacts/gps-accuracy-2026-09-22/registration/analysis-summary.json)、[条件感度](../artifacts/gps-accuracy-2026-09-22/registration/sensitivity-audit.json) | 道路はオルソ由来で独立測量ではない |
| 描画結果 | [実Chrome記録](../artifacts/gps-accuracy-2026-09-22/runtime/runtime-verification.json)、[車両諸元設定](../public/data/vehicles/mazda2-dj.json) | 車体外観・実タイヤ・アンテナ位置の実測ではない |
| 再推定の実装 | [推定器](../pipeline/register_gps_to_track.py)、[解析](../scripts/quality/analyze-fuji-gps-registration.py)、[ブラウザ検証](../scripts/quality/verify-fuji-gps-registration.mjs) | 解析にはPython依存と一部ローカル画像が必要 |

配布データは `public/data/races/fuji_aim_01/`（7/29、4周）、`public/data/races/fuji_aim_2020_07_30/`（7/30、6周）、`public/data/tracks/fuji/` にある。表示時の移動はGPS緯度経度を上書きせず、投影後に `gps_registration.json` のラップ別値を加える。東が+x、南が+z。補正ファイルはラップ、道路geometry、track定義のSHAを照合して失効する。

## 再現可能な範囲を分ける

**Gitクローンだけで可能:** 配布JSON・登録値・SHAの同一性確認、保存済み解析表と実描画記録の読解、現行コードのテスト、配布済み道路とラップからの登録再推定。後者はPythonの `numpy` と `scipy` が必要で、入力に変更がないことを先に確認する。`pipeline/register_gps_to_track.py` は結果ファイルを書き直すため、別の作業ブランチまたはクリーンな複製で試す。

**別途の原資料が必要:** [前処理再生成器](../scripts/quality/rebuild-fuji-gps-preprocessing.py)による元XRKからの10Hz配信データ再生成、元動画の時計・パケット監査、元オルソからの道路再抽出。元XRK/MOVはユーザー提供のローカル資料で公開Gitにない。元オルソもライセンス・出典メタデータに従って別途取得・保管する。映像のローカル参照許諾は公開再配布許諾ではない。担当者間の原媒体共有は権限付き保管で行い、提供元・日時・SHA・使用許諾・測地系を照合する。個人端末の絶対パスを資料の前提にしない。

元動画の同期監査JSONやフレームは公開Gitへ収録していない。公開した数値要約は[完了報告の「動画同期」](fuji-gps-accuracy-improvement-2026-09-22.md#動画同期とログ時刻の検証)にある。原資料がない担当者はこの結果を**保存済み監査の報告値**として扱い、独立再実行済みとは記さない。

## 新たな調査の進め方

1. 変更前に配布入力のSHA検証を実行し、どのcommit・どのラップ・道路・車両定義かを記録する。補正値や画像だけを比べない。
2. 比較量を先に決める。現行の道路外超過は4輪外端とCG境界の距離で、位置の真値ではない。合わせ込み地点と評価地点、wet/dryの使用可能域、ピット除外を固定する。
3. アンテナの軸距中央からの前後・左右位置、実装タイヤ、カメラ位置と歪みを実測する。両日同一取付という申告を記録しつつ、数値を0と確定したことにしない。
4. 同一測地基準の測量済み地表点、または解品質を保存したRTK等を用意する。補正に使わない地点・周回・日付で、位置誤差と時間誤差を別々に検証する。
5. 新入力で前処理→道路生成→登録再推定→半周hold-out→実ブラウザ検証を順に行い、旧結果と同じ条件で差を報告する。根拠なく地点別の手合わせ、速度相関だけからの時刻シフト、時間変動補正を加えない。

**現時点の結論:** 表示上の道路整合は改善している。両日の4.821m差は現実的なGNSSセッション偏りと整合するが、その固有原因と絶対位置精度は未確定。20〜30cm級の保証が必要なら、独立した測量・高精度GNSSの取得が次の必須作業となる。
