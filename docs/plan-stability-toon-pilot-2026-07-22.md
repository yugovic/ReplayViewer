# Stability AI トゥーン化パイロット実行計画（2026-07-22）

## 要旨

Stability AI の画像編集 API で富士12地点のトゥーン化パイロットを行う具体計画。
既存の ImageGen パイロットキット（`pilot_01〜12_src.png`・ゲート評価CLI・
compare.html・Replayトライアルタイル）を最大限流用し、**新規実装は API 呼び出し
スクリプト1本とゲートのトゥーン用プロファイルのみ**。2系統（Style Transfer
with Gemini画風参照 / Structure Control + プロンプト）×設定振りで約60〜72枚、
**費用 $4〜6（¥1,000以下）、所要2日**。判定は位置ゲート12/12 + seed再現一致 +
実画面目視。合格系統があれば全周展開設計へ、Style系のみ合格なら
ローカルControlNet、全滅ならベクター描き起こしへ分岐する。

---

## 0. 前提と流用資産

| 資産 | 場所 | 流用内容 |
|------|------|----------|
| 12地点タイル | `public/data/tracks/fuji/imagegen_trials/pilot/pilot_01〜12_src.png` | グレーディング済み native 0.20m/px、512px切り出し済み。on-track 8 + Coca-Cola 1 + off-track 3 |
| 座標・ゲート定義 | 同 `index.json` | 各枚の座標・UV・sha256・ゲート定義（provenance） |
| 評価CLI | `pipeline/imagegen_pilot.py evaluate` | ブロックシフト・エッジ位置・回転/拡縮・色差・LR一貫性の計測 |
| 画風参照 | `imagegen_trials/ai_illustrated_tile.png` | Gemini で得た良好なトゥーン1枚（Style Transfer の style 入力） |
| 比較ページ | `imagegen_trials/compare.html` + `pilot/index.html` | 生成物の目視比較 |
| Replay実画面A/B | manifest.json のトライアルタイル差し替え（Trial tilesトグル） | Coca-Cola地点（pilot_09互換）で走行視点確認 |

## 1. Phase 0 — 準備（30分 + ユーザー作業）

1. **APIキー取得（ユーザー）**: platform.stability.ai でキー発行、
   初回クレジット購入は最小（$10）で足りる。`STABILITY_API_KEY` を環境変数で
   渡す（リポジトリ・ログへは書かない）。
2. **エンドポイント仕様の確定（実装前に必ず docs 実確認）**:
   - Style Transfer 系: `v2beta/stable-image/control/style`（画風参照画像+prompt）
     ※名称・パラメータは実装時に公式リファレンスで確認
   - Structure Control: `v2beta/stable-image/control/structure`
     （`image` + `prompt` + `control_strength` 0..1 + `seed`）
   - 商用利用条件（従量APIの出力の商用可否）を pricing/ToS で確認し
     メタデータに記録する。
3. **予算上限の設定**: スクリプトに `--max-calls` を付け、暴走しても
   $10 を超えない構造にする。

## 2. Phase 1 — 実行スクリプト（半日）

新規 `pipeline/stability_toon_pilot.py`（requests のみ、venv-sr 流用）:

```
python pipeline/stability_toon_pilot.py run \
  --pilot-dir public/data/tracks/fuji/imagegen_trials/pilot \
  --style-ref public/data/tracks/fuji/imagegen_trials/ai_illustrated_tile.png \
  --variants style:0.3,0.5,0.8 structure:0.5,0.7,0.9 \
  --seed 42 --max-calls 80
```

仕様:

- 12枚 × 2系統 × 3設定 = 72呼び出し（+失敗リトライ、上限80）。
- 出力は `pilot/stability/<系統>_<設定>/pilot_XX_gen.png` に番号一致で保存
  （既存 evaluate がそのまま読める形）。
- 全呼び出しの `endpoint / パラメータ / seed / レスポンスID / 料金` を
  `pilot/stability/manifest.json` に記録（provenance 規約準拠）。
- プロンプトは既存 `PROMPT.txt` の骨格（位置不変・物体追加削除禁止）+
  トゥーン指示（フラットな色面・鮮やかな緑・赤白縁石維持・白線維持）。
  日英併記、全系統で同一文面。
- **再現性テスト**: pilot_09 のみ同条件で2回呼び、出力の一致
  （画素差）を計測して記録する。

## 3. Phase 2 — 評価（半日）

1. **機械ゲート（トゥーン用プロファイル）**: `imagegen_pilot.py evaluate` に
   `--profile toon` を追加:
   - 維持（PASS必須）: ブロック位置シフト ≤0.5px / 白線・縁石エッジ位置 /
     回転・拡縮・鏡映なし / 新規物体の出現・既存物体の消失なし
   - 参考値化（FAILにしない）: 色差 / コントラスト比 / LR一貫性 / MUSIQ等
     （トゥーンは意図的に色を変えるため。計画書の方針どおり）
2. **目視比較**: compare.html を系統×設定のグリッドに拡張し、
   Gemini 基準画像と並べる。
3. **実画面A/B**: 最良設定の pilot_09 をトライアルタイル manifest に載せ、
   Replay の Coca-Cola 地点をチェイス視点で確認（Playwrightスクリーンショット
   + ユーザー目視）。
4. **タイル連続性の先行チェック**: pilot_01 の隣接タイル1枚を追加生成し、
   同一設定・同一seedで境界の色味段差を計測（全周展開の最大リスクの先行検証）。

## 4. Phase 3 — 判定基準と分岐

| 結果 | 判定 | 次アクション |
|------|------|--------------|
| いずれかの系統で 位置ゲート12/12 + 画風がGemini基準に匹敵 + 再現一致 | **合格** | Phase 4（全周展開設計）へ |
| Style Transfer の画風は良いが位置ゲートFAIL | 部分合格 | ローカルControlNet（方法3B）に同じ画風参照で挑戦 |
| 位置は保つが画風が魅力不足 | 部分合格 | プロンプト/参照画像を変えて1ラウンドだけ再試行（+$5上限）。改善なければベクター描き起こし |
| 全滅 | 不合格 | ベクター描き起こし（提案書§2-B）を本命化 |

## 5. Phase 4 — 全周展開の見取り図（合格時。別途詳細計画）

- 元モザイク 8,014×8,192 を **1,024px窓 + 128pxオーバーラップ**で分割
  （約80〜90窓）、全窓同一 seed・同一プロンプト・同一設定。
- 出力は中央 768px のみ採用し 64px フェザーで合成 → 既存UVで119枚へ切り出し。
- 全数を `--profile toon` ゲートに通し、FAIL窓のみ再試行。
- 費用目安: 90窓 + 再試行 ≈ 110呼び出し ≈ **$6〜9**。
- 成果物は新バリアント `satellite_toon.jpg`（または SRコリドー相当のタイル層）
  として配信。AI加工である旨と provenance を meta に記録
  （「AI画像は可視化レイヤーであり測量真値ではない」規約の明記）。

## 6. リスクと対策

| リスク | 対策 |
|--------|------|
| API出力が約1MP上限で 512px入力→1024px出力になる | 本パイロットは0.2m/pxのまま画風検証に集中。解像度はPhase 4で窓サイズと出力サイズの組み合わせを再設計 |
| 写真→トゥーンの大変換で Structure でも構造が崩れる | control_strength を高め(0.7-0.9)に振る。エッジ位置ゲートで機械検出 |
| Style Transfer が参照画像の構図まで転写する | 設定低めから振る。新規物体ゲートで検出 |
| タイル間の色味不連続 | Phase 2-4 の隣接ペア先行チェックで早期に定量化。必要なら全体色補正LUTを後段に追加（決定論なので安全） |
| ライセンス・商用条件 | Phase 0 で ToS 確認しメタに記録。不可なら Stability 自体を中止しローカル3Bへ |
| 費用暴走 | `--max-calls` ハードリミット + manifest に累計料金記録 |

## 7. スケジュール

| 日 | 内容 |
|----|------|
| Day 1 午前 | Phase 0（キー取得・仕様確認）+ スクリプト実装 |
| Day 1 午後 | 72枚実行 + 機械ゲート評価 |
| Day 2 午前 | compare.html 拡張・実画面A/B・隣接ペア検証 |
| Day 2 午後 | 判定・ユーザーレビュー・次フェーズ判断 |

## 8. 関連資料

- 方針: [proposal-toon-next-steps-2026-07-21.md](proposal-toon-next-steps-2026-07-21.md)
- 12枚キット手順: [guide-imagegen-pilot-2026-07-16.md](guide-imagegen-pilot-2026-07-16.md)
- 過去の幻覚計測: [evaluation-imagegen-fidelity-2026-07-16.md](evaluation-imagegen-fidelity-2026-07-16.md)
- 費用試算: [image-generation-tile-cost-estimate-2026-07-16.md](image-generation-tile-cost-estimate-2026-07-16.md)
