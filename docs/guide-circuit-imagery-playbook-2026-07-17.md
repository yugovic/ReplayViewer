# サーキット地面画像プレイブック — 優先度と手順

- 記録日: 2026-07-17
- 対象読者: 新しいサーキット（鈴鹿・茂原・その他）をReplayViewerへ追加する将来の自分/チーム
- 根拠: 富士スピードウェイで2026-07-16に実施した一連の取り組みの実測値
  （各詳細は文末の資料マップ参照）

## 要旨

3Dリプレイの「地面の画質」は単一の手法では決まらず、**①権利クリーンなソース
②無劣化の取り回し ③グレーディング ④超解像 ⑤近接ディテール＋ベクトル線**の
積み重ねで決まる。富士での実測では、**費用対効果の最大はグレーディング（無料・
30分）**であり、次いで近接ディテール描画、SR刷新の順。生成AIは鮮鋭度で勝つが
細部を描き直すため限定運用。真の解像度向上（10cm級以下）はデータ更新
（交付・ドローン・サーキット保有データ）でのみ達成できる。本書はこの優先度と、
新トラックを追加する際の具体的手順・合格基準・落とし穴を1本にまとめたもの。

## 1. 優先度付け（実測に基づく）

| 優先 | 手法 | 効果（富士実測） | 費用/工数 | 判断 |
|---|---|---|---|---|
| **P0** | 権利クリーンな公的オルソの確保（20cm級, CC BY等） | すべての土台。Google/Bingは表示専用で加工不可 | 無料〜数万円 / 調査1日 | **必須** |
| **P1** | 無劣化パイプライン（native GeoTIFF直結、JPEG中間排除） | 実情報+10%線形＋圧縮ノイズ除去（SR入力品質向上） | ¥0 / 0.5日 | 必須 |
| **P2** | **トーングレーディング**（WB→レベル→CLAHE→彩度） | **見た目の差の主因を解消**。「Googleの方が良く見える」の大部分は解像度でなく仕上げだった | ¥0 / 実行30分 | **最優先で効く** |
| **P3** | SR（RealPLKSR＋知覚ゲート） | LPIPS −64%・DISTS −46%（Lanczos比）、幻覚ガード付き | ¥0 / GPU10分 | 高効果 |
| **P4** | 近接ディテール＋ベクトル線・縁石 | 情報の天井（GSD）を超える唯一のリアルタイム手法。白線/縁石はズームしても劣化しない | ¥0 / 実装済みで自動 | 高効果 |
| **P5** | 生成AI編集（パイロット済み） | MUSIQ +2〜+15で現行SR超えだが、LR一貫性24〜30dBで細部を描き直す | 手動=サブスク枠内 / API従量$0.5〜3(12枚) | **任意**（コース外限定・ゲート必須） |
| **P6** | 実データ更新（10cm交付/ドローン2〜3cm/サーキット保有データ） | 情報量の天井そのものを引き上げる唯一の手段 | ¥3万〜800万 / 交渉・委託 | **条件付き**（下記トリガー） |

**区分**: P0〜P4が新トラック追加時に毎回実施する**標準工程**（すべて無料・
合計1〜2日）。P5・P6は**必要に応じて**の追加札で、着手トリガーを決めておく:

- P5トリガー: コース外（観客席・パドック等）の見栄えが商談・デモで課題に
  なったとき。着手しても採用はゲート通過分のみ、走行ライン近傍は対象外。
- P6トリガー: 事業ゲート（Go/Kill）通過や特定サーキットでの商用運用決定。
  例外として**茂原の10cm交付（¥3万）だけは安価なので運用開始時に即実施**
  してよい。富士のドローン/保有データ照会は有料顧客が付いてから。

補足:
- P2がP3より上なのは実測順序の教訓。グレーディング前のSRタイルは「高精細だが
  霞んで浮く」状態になり、後からトーンを直すと全タイル再生成になる。
  **必ずグレーディング→SRの順**。
- P4のベクトル線は「画像の見た目」と「線の位置」を別レイヤーに分離する設計思想
  （位置の真実はベクトルが持つ）。生成AIを検討する際もこの前提が安全弁になる。

## 2. 手順（新トラック追加のステップバイステップ）

### Step 0. ソース調査と権利確認（P0）

1. 公的オープンデータ（県・市の点群/オルソ事業、G空間情報センター）→
   国土地理院 → 自治体交付制度 → 民間既製品の順に、
   **GSD・撮影年・派生加工とWeb同梱の可否**を確認する。
2. チェックリスト: □出典とライセンスが文書で確認できる □タイル化して
   Webアプリに同梱できる □商用利用可（または承認手続が明確） □GeoTIFF/
   ワールドファイル等の座標情報つき
3. **不可のもの**: Google Map Tiles / Bing 等の地図サービス画像の保存・加工・
   SR入力・生成AI入力（表示専用）。「15cm HD」等のAI補間衛星画像を測量真値と
   して扱うこと。
4. 参考相場: 富士=県オープンデータ20cm無料 / 鈴鹿=15cm購入+測量法承認 /
   茂原=10cm ¥30,000交付。

> **追記（2026-07-17 初適用）**: 鈴鹿・岡山国際で本プレイブックを実証済み
> （[implementation-suzuka-okayama-2026-07-17.md](implementation-suzuka-okayama-2026-07-17.md)）。
> 公的高解像度オルソが無い地域では **GSIシームレス写真 z18（政府標準利用規約2.0
> =CC BY 4.0互換、e-Gov確認済）** が「今日使える」既定ソース:
> `pipeline/fetch_gsi_ortho.py`。中心線が無いトラックは
> `pipeline/bootstrap_track_from_osm.py`（OSM+地理院DEM、可視化用精度）と
> `pipeline/make_preview_race.py`（合成走行）でブートストラップできる。

### Step 1. native モザイク生成（P1）

```powershell
# 例: 富士（fetch_shizuoka_ortho.py を新ソース用に複製・調整）
python pipeline\fetch_shizuoka_ortho.py --skip-download `
  --out-image pipeline\cache\<track>\native_mosaic.png `
  --out-meta pipeline\cache\<track>\native_mosaic_meta.json `
  --max-long-edge 16384
```

- 原データ（GeoTIFF）→ EPSG:3857 へ warp、**PNG（無劣化）**で保存。
  8192px上限に縮めた JPEG を処理系の入力にしない。
- 合格基準: effectiveResolution ≒ 原GSD、coverage 100%。
- 出典・ライセンス・SHA-256 を meta に必ず記録（CLAUDE.md 画像方針）。

### Step 2. トーングレーディング（P2）

```powershell
python pipeline\grade_ortho.py `
  --input pipeline\cache\<track>\native_mosaic.png `
  --meta pipeline\cache\<track>\native_mosaic_meta.json `
  --out-mosaic pipeline\cache\<track>\native_mosaic_graded.png `
  --out-meta pipeline\cache\<track>\native_mosaic_graded_meta.json `
  --base-jpeg public\data\tracks\<track>\satellite_<src>.jpg `
  --base-meta public\data\tracks\<track>\satellite_<src>_meta.json `
  --panel Temp\grade-before-after.jpg
```

- 内容: gray-world WB（±8%上限）→輝度パーセンタイルストレッチ→CLAHE
  （LAB L, 約25mタイル）→彩度+12%。**全て位置保存**（幾何を変えない）。
- **8K下地とSRタイルは必ず同一グレードから作る**（色継ぎ目防止）。
- 合格基準: before/afterパネルの目視（アスファルトの黒・芝の分離）+
  metaに toneEnhanced 記録。

### Step 3. コリドーSR（P3）

```powershell
# 3a. 検証（強度の自動選定とゲート）
pipeline\.venv-sr\Scripts\python.exe pipeline\enhance_track_corridor.py `
  --mode validation --engine spandrel `
  --input ...native_mosaic_graded.png --meta ..._graded_meta.json
# 3b. タイル生成（validationが推奨した強度で）
pipeline\.venv-sr\Scripts\python.exe pipeline\enhance_track_corridor.py `
  --mode tiles --engine spandrel --sr-strength <推奨値> `
  --input ...native_mosaic_graded.png --meta ..._graded_meta.json `
  --output public\data\tracks\<track>\satellite_corridor_x2
```

- モデル: 4xNomosWebPhoto_RealPLKSR（CC BY 4.0）。×4推論→2×縮小。
- **合格ゲート（自動判定）**: DISTS argmin選定 / LR一貫性 ≥30dB /
  線シフト p95 ≤0.5px。PSNR/SSIMはゲートに使わない（ボケ側が常に勝つため）。
- 富士実測: 強度0.5、LPIPS 0.163→0.059、LR 40.0dB、シフト0.16px、
  140タイル/36MB/10分（RTX 3090）。
- 前提: track.json（中心線）が先に必要。コリドー幅は core 80m / fade 140m。

### Step 4. ベクトル線・縁石（P4）

- `pipeline/export_road_edges.py` で手修正済みエッジ→ `road_edges.json`
  （左右リボン白線）。overlays.json で縁石。
- 位置の真実はこのレイヤーが持つ。SR/生成画像の線とズレたら**ベクトルが正**。

### Step 5. 近接ディテール（P4・自動）

- `groundDetail.ts` が下地とSRタイルへ自動適用（hex-tiling反復抑制、
  in-shader ExG 舗装/芝判定、CC0テクスチャ、`D`キーでON/OFF）。
- トラック固有作業は不要。見た目が強すぎ/弱すぎならratio boost・τを調整。

### Step 6. 検証（必須）

1. 単体テスト+ビルド: `npx vitest run` / `npm run build`
2. Playwright実画面: console/page/HTTP/シェーダーエラー0、タイルロード数
3. **全周俯瞰スイープ**: 300m間隔で真上・北=上撮影→航空写真と位相相関照合。
   合格: 全地点シフト ≤1m（富士実測 0.25〜0.75m）
4. **前後比較はカメラを凍結してから**（chaseは静止後も数秒イージングする。
   Freeカメラ切替+コントローラ無効化）

### Step 7.（任意）生成AI編集パイロット（P5）

```powershell
python pipeline\imagegen_pilot.py prepare    # 12枚切り出し（graded native から）
# 手動生成（ChatGPT/Gemini等）→ pilot_XX_gen.png 保存
pipeline\.venv-sr\Scripts\python.exe pipeline\imagegen_pilot.py evaluate
python pipeline\imagegen_pilot.py apply      # 色一致+フェザー+manifest（Replayで I キーA/B）
```

- 採用条件: LR ≥30dB / シフト ≤0.5px / 色差 ≤8 / コントラスト比0.8〜1.2。
- **適用はコース外から**。走行ライン近傍はSR+ベクトルを正とする。
- provenance（モデル・プロンプト・入力sha256）をmanifestに記録。

### Step 8. データ更新の意思決定（P6）

- 順序: ①サーキット運営会社へ保有測量データを照会（無料。AC公認富士MODに
  提供前例）→ ②自治体交付（茂原型、数万円）→ ③ドローン写真測量委託
  （GSD2〜3cm、150〜200haで¥300〜800万、飛行許可が関門、再舗装等の後に）。
- 判断は事業ゲート（Go/Kill）と連動。取得データはTrack Tools（自前トラック）
  方向性とも共用できる。

## 3. 落とし穴（富士で実際に踏んだもの）

| 落とし穴 | 症状 | 対策（実装済み） |
|---|---|---|
| PSNR/SSIMで強度選定 | 常にボケ側が勝ち、過剰なブレンド保険 | DISTS/LPIPS+無参照IQA+幻覚ガードへ |
| グレーディング前にSR | 後からトーンを直すと不整合パッチ（R+20明るく浮く） | 必ずグレード→SRの順 |
| シェーダー正規化をミップ依存に | textureLodが環境により黒→地面全体×2明化 | 平均色はCPU事前計算のuniformで渡す |
| sRGB平均の順序 | 平均→デコードは凸性で過小評価→全体明化 | texel毎デコード→平均 |
| タイル名固定+force-cache | 再生成で座標系が変わると、旧キャッシュ混在で数十mずれ | manifest no-cache化+タイルURLに`?v=`世代スタンプ |
| chaseカメラで前後比較 | 静止後も数秒動き、別フレーミングを比較して誤診 | Freeカメラ凍結+8方位NCC照合 |
| ヘッドレス検証の過信 | SwiftShaderは実GPUと挙動差（textureLod等） | 最終合否は実GPU目視を併用 |
| 実在構造物を不具合と誤認 | 段々擁壁・伐採斜面がドレープで縞に見える | 航空写真の真値照合を先に。緩和は3D Features ON |

## 4. 資料マップ（詳細の一次資料）

| テーマ | 資料 |
|---|---|
| 3層戦略・SR調査・実装記録 | [research-ground-resolution-strategy-2026-07-16.md](research-ground-resolution-strategy-2026-07-16.md) |
| ソース権利調査（富士/鈴鹿/茂原） | [aerial-imagery-quality-research-ja.md](aerial-imagery-quality-research-ja.md) |
| ベクトル線の設計と実装 | [research-track-line-enhancement-2026-07-16.md](research-track-line-enhancement-2026-07-16.md) |
| 生成AIパイロット手順/結果 | [guide-imagegen-pilot-2026-07-16.md](guide-imagegen-pilot-2026-07-16.md) / [evaluation-imagegen-pilot-results-2026-07-16.md](evaluation-imagegen-pilot-results-2026-07-16.md) |
| 生成AIの経路×課金・4×パッチ評価 | [evaluation-4x-patch-and-imagegen-options-2026-07-16.md](evaluation-4x-patch-and-imagegen-options-2026-07-16.md) |
| Googleライブ表示（表示専用の代替） | [google-maps-live-trial-ja.md](google-maps-live-trial-ja.md) |
| 費用試算（生成AIタイル） | [image-generation-tile-cost-estimate-2026-07-16.md](image-generation-tile-cost-estimate-2026-07-16.md) |

## 5. 残タスク（BACKLOG連動）

- コア4×リング（中心線±30mを0.05m/px化）— 4×パッチ評価で有効性実証済み
- neosr自前ファインチューニング（グレード済みオルソ教師、SR天井上げの本命）
- タイルKTX2/UASTC化（VRAM 1/4）、スプラット高度化（砂利/縁石分離）
- 照会系: 富士SW保有データ / ドローン見積 / 茂原10cm仕様
- Bing系固定画像（satellite_bing*.jpg）のライセンス確認
