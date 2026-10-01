# docs/ の構成ガイド

ReplayViewer のドキュメント置き場。**このファイルが docs/ のインデックス**なので、ドキュメントを追加・削除したら必ずここの一覧も更新する。

## プロジェクト全体のドキュメント地図

| 場所 | 役割 |
|------|------|
| [../WORKLOG.md](../WORKLOG.md) | 作業ログ（逆時系列）。セッション区切りや大きな作業の完了時に追記 |
| [../BACKLOG.md](../BACKLOG.md) | 改善バックログ（P1〜P3、出典・追加日つき）。完了しても消さずにステータス更新 |
| `docs/`（ここ） | 上記2つに収まらない単発ドキュメント: 評価レポート、調査結果、設計メモなど |

## docs/ 内の一覧

| ファイル | 種類 | 概要 |
|----------|------|------|
| [fuji-gps-investigator-handoff-2026-10-01.md](fuji-gps-investigator-handoff-2026-10-01.md) | GPS調査引き継ぎ | 別端末での読み順、Git内の証拠地図、SHA検証、原媒体が必要な工程、次の独立検証手順 |
| [fuji-gps-investigation-history-2026-10-01.md](fuji-gps-investigation-history-2026-10-01.md) | GPS調査の経緯・採否 | 9/8〜10/1の固定移動・2軸・地点別・全周推定の判断履歴と、後で撤回した解釈 |
| [fuji-gps-accuracy-improvement-2026-09-22.md](fuji-gps-accuracy-improvement-2026-09-22.md) | GPS・CG精度改善の実施結果 | 端点処理4.93m→3.2cm、Mazda2寸法/道路境界を統一し全10周再推定。半周CV20/20改善、実Chrome確認。映像1294時計点と原GPSパケット監査、日差4.821mの感度解析、測定限界と不採用判断 |
| [policy-gps-cg-alignment-2026-09-20.md](policy-gps-cg-alignment-2026-09-20.md) | GPS位置合わせ方針（9/22改訂） | 修正済み入力のラップ単位平行移動、寸法/道路の共通化、3入力SHA検証。絶対精度・映像距離・型式の旧断定を訂正。完了報告を正本として参照 |
| [viewer.html](viewer.html) | ツール | docs閲覧用DocsViewer（ライブ表示）。この索引と各docの`## 要旨`から自動生成。`python -m http.server` 等で `docs/viewer.html` を開く。更新は `/docs-viewer` スキル（共有用スナップショットのURLは `.docsviewer-url`） |
| [report-startup-performance-2026-07-22.html](report-startup-performance-2026-07-22.html) | 実測レポート | 起動遅延のPlaywright実測（Fujiユーザーモード）。支配要因=satellite_shizuoka.jpg 29.76MB（総転送の72%・地面が出るまでcold約3.5s）。scene.glb/trials/SRタイル/他バリアントは起動時0件（遅延・視錐台ゲート・プローブ無効化で回避済）。無駄=features3d.json 1.06MB（OFFでも取得）他。削減候補P1=衛星軽量化と並行化で−18〜22MB/−1.2〜2.5s |
| [proposal-building-terrain-2026-07-22.md](proposal-building-terrain-2026-07-22.md) | 方針提案（best-of-N統合） | 「建物が丘に見える」問題。Opus3体の独立調査を統合: 真因=LP点群2クラス分類で大型屋根がDTMに誤混入+写真ドレープ。既存資産（建物footprint91件+3D箱押し出し）で解決可。推奨=①フットプリントでDTM平坦化②屋根像の除去③3D箱の輪郭・影・トゥーン屋根で仕上げ。総工数3〜5日・API費$5未満 |
| [report-toon-corridor-gemini-2026-07-22.md](report-toon-corridor-gemini-2026-07-22.md) | 実装レポート | トゥーンコリドー全周完成の総括。Stability 2方式不合格→Gemini 3.1-flash-image 採用（シフト≤0.4px・重なり91〜100%）、59窓+クリーン化17窓、黒帯はオフライン合成で解消、芝色#BCCF25決め打ちの実証、総コスト≈$10、残課題7件 |
| [plan-stability-toon-pilot-2026-07-22.md](plan-stability-toon-pilot-2026-07-22.md) | 実行計画 | Stability API トゥーン化12地点パイロットの具体計画。既存ImageGenキット流用、2系統（Style Transfer with Gemini参照 / Structure Control）×3設定=72枚・$4〜6・2日。位置ゲート維持+色ゲート参考値化、seed再現テスト、隣接ペア連続性の先行検証、判定分岐と全周展開見取り図つき |
| [proposal-toon-next-steps-2026-07-21.md](proposal-toon-next-steps-2026-07-21.md) | 方針提案 | トゥーン化の断念（ロジックベース3方式の実地検証で品質不足→全ロールバック）と次方針。推奨=Stability API 12地点パイロット（数百円）とベクター描き起こしモックを並行制作して見た目で決定、ローカルControlNetはその結果待ち |
| [investigation-load-time-2026-07-17.md](investigation-load-time-2026-07-17.md) | 調査・原因究明 | 初期ロードが遅い原因の実測分析（dev/本番previewで perf_profile.mjs 計測）。最大要因=衛星バリアントHEADプローブ（29MBファイルへ5.8〜8.9s）が初期ロードをブロック。改善方針P0（プローブ非ブロック化で−6〜9s）/P1（terrain 16bit化・shizuoka段階ロード・KTX2）/P2（ドレープWorker化/事前計算・進捗UI） |
| [plan-repo-file-organization-2026-07-17.md](plan-repo-file-organization-2026-07-17.md) | 整理計画 | リポジトリのファイル整理・リファクタリング計画。現状診断（ルート散在37本/一時フォルダ3系統/.bak66個/未コミット87件）と6フェーズ実施計画（先コミット→scripts/集約→Temp/一本化→.bak退避→データ管理方針→規約整備）。所要2〜3時間 |
| [business-exit-debate-2026-07-17.md](business-exit-debate-2026-07-17.md) | 討論記録 | 4サブエージェント（拡大派/懐疑派/業界アナリスト/出口設計）2ラウンドの出口ディベート統合。B2C棄却・権利者を顧客にする構図・生成AIタイルの売り物除外・「IP+人」承継型出口（vTelemetry PROコンプ）・出口価値ゲート5条件。07-14討論の続編 |
| [implementation-suzuka-okayama-2026-07-17.md](implementation-suzuka-okayama-2026-07-17.md) | 実装記録 | プレイブック初適用で鈴鹿・岡山国際を同日構築。GSIシームレス写真z18採用（e-Gov一次情報でCC BY 4.0互換・同梱可を確認）、OSM+地理院DEMの中心線ブートストラップ（鈴鹿40ウェイ結合・公式長誤差0.1%）、SR知覚ゲート通過（LPIPS −60〜69%）、合成プレビュー走行で表示可能。制約=0.49m級・OSM精度 |
| [guide-circuit-imagery-playbook-2026-07-17.md](guide-circuit-imagery-playbook-2026-07-17.md) | 手順書（プレイブック） | サーキット地面画像の用意方法の決定版。実測に基づく優先度付け（P0権利ソース→P2グレーディングが費用対効果最大→SR→近接ディテール→生成AI限定→実データ更新）、新トラック追加の8ステップ手順（コマンド・合格ゲート付き）、実際に踏んだ落とし穴8件と対策 |
| [design-exit-strategy-2026-07-17.md](design-exit-strategy-2026-07-17.md) | 設計メモ | 出口戦略設計。6類型（IP売却/ライセンス/事業譲渡/acqui-hire/OEM/OSS化）の比較表と相場根拠（Flippa実勢・vTelemetry PRO買収事例）、権利チェーン台帳の証拠化を軸としたパッケージング、既存Go/Killゲートを分岐点に流用した18ヶ月ロードマップ |
| [persona-review-2026-07-13.md](persona-review-2026-07-13.md) | 評価レポート | 4ペルソナ（ドライバー本人/エンジニア/ファン/初心者）のサブエージェントが実プレイして課題を洗い出した記録。横断分析とP0〜P3改善ロードマップ、P0対応後の事実訂正の追記を含む |
| [buyer-map-motorsport-2026-07-17.md](buyer-map-motorsport-2026-07-17.md) | 調査・分析 | 3Dリプレイ資産の買い手/提携先マップ（日本モータースポーツ業界、実名ベース）。上位3ターゲット=富士圏走行会→FSW / STMO(S耐) / スクール教材、SFgoは競合兼提携先。確認済事実と推測を区別 |
| [business-debate-2026-07-14.md](business-debate-2026-07-14.md) | 討論記録 | Claude×Codex(gpt-5.6-sol xhigh)4ラウンドの事業性ディベート全文。最終判定=条件付きYes 58%（スクール向け「動画任意デブリーフ基盤」）、6か月Go/Killゲート、出口候補リスト、R4=プロモーター連携・特許戦略の判定つき |
| [research-track-line-enhancement-2026-07-16.md](research-track-line-enhancement-2026-07-16.md) | 先行研究・実装 | トラック白線・舗装境界を鮮明にする線状構造抽出、structure-preserving SR、富士の可変エッジ線レイヤー実装と検証 |
| [research-ground-resolution-strategy-2026-07-16.md](research-ground-resolution-strategy-2026-07-16.md) | 調査・提案・実装記録 | 地面解像度向上の3層戦略（近接ディテール描画/SRパイプライン刷新/真の高解像度ソース）。Opusサブエージェント3系統の調査結果と段階ロードマップ、非推奨事項の整理。Phase 0〜2の同日実装結果と発見バグ2件を追記 |
| [aerial-imagery-quality-research-ja.md](aerial-imagery-quality-research-ja.md) | 調査 | 富士/鈴鹿/茂原の航空写真の品質・利用条件調査。Googleはオンデマンド表示のみ可、固定画像は富士20cm（導入済）/鈴鹿15cm（要承認）/茂原10cm（要照会）の権利整理 |
| [google-maps-live-trial-ja.md](google-maps-live-trial-ja.md) | 手順書 | Google Map Tiles APIの2D衛星タイルをReplay地面へライブ表示する実装・API設定・課金単位・利用制約のガイド |
| [imagery-enhancement-fuji-2026-07-16.md](imagery-enhancement-fuji-2026-07-16.md) | 実装記録 | 富士トラック周辺のReal-ESRGAN 2×コリドータイル生成（119枚, 0.1096m/px格子）とPSNR/SSIM検証、Replay表示への接続 |
| [image-generation-tile-cost-estimate-2026-07-16.md](image-generation-tile-cost-estimate-2026-07-16.md) | 試算 | 生成AI(GPT Image 2)でコリドータイルを再処理する場合の枚数・費用試算。位置精度リスクから限定運用を推奨 |
| [evaluation-imagegen-fidelity-2026-07-16.md](evaluation-imagegen-fidelity-2026-07-16.md) | 評価レポート | ImageGen試験タイル（富士middle sector）の忠実度計測。LR一貫性17.6dB・局所シフトp95≈2.8m・幻覚事例（車出現/ロゴ再描画等）を確認し、SR+ベクトルを正・生成AIはコース外限定の方針と再試行時のP0〜P2改善策を提示 |
| [evaluation-4x-patch-and-imagegen-options-2026-07-16.md](evaluation-4x-patch-and-imagegen-options-2026-07-16.md) | 評価・選択肢整理 | 中盤4×試験パッチの定量評価（忠実42.1dB・MUSIQ+10だがグレーディング前画像由来でトーン不整合R+20/G+11）と推奨（コア4×リング実装が本命）。ImageGen不適用の二段理由（helper障害+忠実度）と実行経路×課金の整理（API直叩きはサブスク枠充当不可の従量） |
| [evaluation-imagegen-pilot-results-2026-07-16.md](evaluation-imagegen-pilot-results-2026-07-16.md) | 評価レポート | ImageGen 12枚パイロットの結果。素の生成物は0/12 PASS（主因=色ずれ、色補正で解消）だが知覚品質は現行SR比で全12地点勝ち（MUSIQ +2〜+15）。向き異常は画像・表示の全数照合で不検出。Replay比較接続と復元手順 |
| [guide-imagegen-pilot-2026-07-16.md](guide-imagegen-pilot-2026-07-16.md) | 手順書 | ImageGen 12枚パイロットの実施手順。対象12枚（on-track 9+off-track 3、グレード済み0.20m/px）、プロンプト要点、合否ゲート（LR≥30dB/シフト≤0.5px/色差≤8/コントラスト0.8-1.2）、PASS後のmanifest適用方法 |
| [claude-fable5-track-alignment-consultation-2026-07-15.md](claude-fable5-track-alignment-consultation-2026-07-15.md) | 相談記録 | 実車オンボード映像とCG走行ラインの位置合わせ検討。暫定オフセットX=-3.5/Z=-4.4の維持とX=-10.45案の棄却判断 |
| [track-alignment-video-analysis-summary-2026-07-15.html](track-alignment-video-analysis-summary-2026-07-15.html) | 分析資料 | 上記位置合わせ相談に付随する映像分析サマリー（HTML） |
| [CLAUDE-worklog-policy-template.md](CLAUDE-worklog-policy-template.md) | テンプレート | 他リポジトリへ作業ログ規約を導入するためのCLAUDE.mdコピー元 |
| [viewer.html](viewer.html) | ツール | docs閲覧用DocsViewer（ライブ表示）。この索引と各docの`## 要旨`から自動生成。`python -m http.server` 等で `docs/viewer.html` を開く。更新は `/docs-viewer` スキル（共有用スナップショットのURLは `.docsviewer-url`） |

## 追加時のルール

- **命名**: `<種類>-<トピック>-YYYY-MM-DD.md`（日付が本質でないものは日付なしの `<トピック>.md` でよい）
  - 種類の例: `persona-review`（ペルソナ評価）, `investigation`（調査・原因究明）, `design`（設計メモ）
- **1ファイル1テーマ**。継続的に更新するリスト類は docs/ ではなく BACKLOG.md / WORKLOG.md へ
- 事実誤認が後から判明した場合は、本文を書き換えずに**追記セクションで訂正**する（persona-review の「追記: P0対応結果と事実訂正」が例）
- 追加したら、この README の一覧表に1行足す

## 再整備のルール（docs が育ってきたら適用）

- **陳腐化した文書は削除せず `docs/BK/` へ移動**する。判断基準: 内容が現状と食い違い追記訂正では追いつかない / 対象機能・方針が廃止された / 後続ドキュメントに完全に置き換えられた。移動時はこの README の一覧から「BK（陳腐化）」セクションへ行を移し、**陳腐化の理由と後継ドキュメントへのリンク**を一言添える
- **同じ種類が5件を超えたらサブフォルダ化**する（例: `reviews/`, `investigations/`, `designs/`）。その際は README の一覧もセクション分けし、ファイル名から種類プレフィックスを外してよい（フォルダが種類を表すため）
- 再整備（移動・フォルダ化）を行ったら WORKLOG.md に1行記録する
- 現状（2026-07-14）: 文書1件のみのため再整備は不要。上記は増えてきたときの基準
