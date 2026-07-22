# ImageGen 12枚パイロット 実施手順

- 記録日: 2026-07-16
- 目的: 生成AI編集（ChatGPT / Gemini アプリ等、サブスク枠内の手動運用）が
  忠実度ゲートを通せるかを代表12枚で検証する
- キット: `pipeline/imagegen_pilot.py`（prepare/evaluate）
- 関連: [evaluation-4x-patch-and-imagegen-options-2026-07-16.md](evaluation-4x-patch-and-imagegen-options-2026-07-16.md)（経路×課金の整理）、
  [evaluation-imagegen-fidelity-2026-07-16.md](evaluation-imagegen-fidelity-2026-07-16.md)（前回フリー生成の失敗計測）

## 対象画像（準備済み）

`public/data/tracks/fuji/imagegen_trials/pilot/` に `pilot_01_src.png`〜`pilot_12_src.png`。
**グレーディング済み native 0.20m/px** から 512px（=102.4m四方）で切り出し済み。

| # | 種別 | 内容 |
|---|---|---|
| 01–08 | on-track | 周回1/8刻み（S/Fストレート、ブリッジ、赤白縁石、グリーンランオフ、グラベル境界） |
| 09 | on-track | 既存トライアル互換の1519.2m地点（Coca-Colaコーナー） |
| 10–12 | off-track | パドック建物 / 林 / ランオフ（±70m横。**採用本命はこのコース外系**） |

- 一覧: `contact_sheet.jpg` / ブラウザ:
  `http://127.0.0.1:5175/data/tracks/fuji/imagegen_trials/pilot/index.html`
- `index.json` に各枚の座標・UV・sha256・ゲート定義を記録（provenance用）

## 手順

1. `pilot_XX_src.png` を生成ツールへアップロード
2. 同フォルダの **`PROMPT.txt`**（日英併記）を添える。要点:
   位置・形状を1pxも動かさない / 物体の追加・削除禁止 / 構図・縮尺・回転不変 /
   色調は入力に厳密一致 / 質感（アスファルト粒・芝・砂利）のみ精細化 /
   正方形・2048px以上
3. 結果を同フォルダへ **`pilot_XX_gen.png`**（番号一致）で保存
4. 評価:

```powershell
# ゲート判定のみ（torch不要・軽い）
python pipeline\imagegen_pilot.py evaluate --no-iqa
# MUSIQ/MANIQA比較つき（venv使用）
pipeline\.venv-sr\Scripts\python.exe pipeline\imagegen_pilot.py evaluate
```

画像ごとに PASS/FAIL、`pilot_XX_eval.json`（数値）、`pilot_XX_panel.jpg`（並置比較）が出力される。

## 合否ゲート（事前固定・SR同型）

| ゲート | 基準 | 参考値 |
|---|---|---|
| LR一貫性（縮小往復PSNR） | **≥30dB** | SR実績40dB / 前回フリー生成17.6dB |
| ブロック位置シフト p95 | **≤0.5px（=10cm）** | SR実績0.16px |
| 平均色差（RGBノルム） | ≤8 | 周囲と馴染むか |
| コントラスト比 | 0.8〜1.2 | 同上 |

## PASS後の適用

- `index.json` の `uv` をそのまま `imagegen_trials/manifest.json` のエントリに使える
  （既存のオーバーライドタイル機構で表示。priority・境界フェードは既存方式に従う）。
- 適用は**コース外3枚（#10-12）を優先**。走行ライン近傍はSR＋ベクトル線を正とする
  方針を維持（CLAUDE.md: AI強化画像は可視化レイヤーであり測量真値にしない）。
- 採用時は manifest に provenance（生成モデル・プロンプト・入力sha256）を記録する。

## 備考

- 全滅でも「編集モードの実力値」が数値で確定するため収穫になる。
- Google Map Tiles はこのワークフローに一切使用しない（表示専用）。
- 自動化が必要になった段階で API 従量（12枚 $0.5〜3規模）へ移行検討。
