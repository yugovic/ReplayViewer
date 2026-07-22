# 地面解像度向上の総合戦略（調査と提案）

調査日: 2026-07-16
対象: ReplayViewer / 富士スピードウェイ（考え方は他コースにも共通）
目的: 「サーキット航空画像の上を3Dのクルマで走る」コンテンツにおいて、地面の
見た目解像度を最大化する手法を選定する。外部調査はOpusサブエージェント3系統
（SR手法 / 画像ソース / リアルタイム描画技法）で実施した。

## 結論（提案の要旨）

「解像度」は独立した3つの層で決まるため、単一の銀の弾丸はない。効果/費用比の
順に次の3層戦略を提案する。

| 層 | 手法 | 上限 | 費用 | 工数 |
|---|---|---|---:|---|
| 1. 知覚解像度（描画） | オルソ×近接ディテール合成（MSFS型） | 事実上なし | ¥0 | 2〜4日 |
| 2. 取り回し損失の回収＋SR刷新 | native 20cm直結＋RealPLKSR＋知覚評価ゲート | 20cm情報の完全活用 | ¥0 | 1.5〜2.5日 |
| 3. 実情報量の更新 | 富士SW保有データ照会 → ドローン測量委託 | 2〜3cm | ¥0〜800万 | 交渉・委託 |

**最優先は層1**。20cmデータをどう超解像しても、チェイスカメラ（地上1.5〜10m）
では情報が物理的に不足する。情報の天井を超えられる唯一のリアルタイム手法が
「オルソ＝低周波の色、タイリング微細ディテール＝高周波の質感」の距離ブレンド
であり、位置的な嘘を作らない（ディテールは輝度・法線のみで色と線の位置はオルソ
とベクトル層が保持する）。しかも本プロジェクトには既に基盤実装があり、後述の
「SRタイルへの適用漏れ」を埋めるだけで最も見られる路面近景の体感が変わる。

層2は同じ20cm情報からの引き出し方の改善、層3は天井そのものの引き上げで、
層1・2とは独立に価値が積み上がる。

## 現状の構成と、今回発見した3つの問題

現状: VIRTUAL SHIZUOKA 2019 LPオルソ20cm（CC BY 4.0/ODbL）→ 8014×8192 JPEG
（0.2192m/px）→ Real-ESRGAN x4plus 50%ブレンドで2×コリドータイル119枚
（出力格子0.1096m/px）→ その上にベクトル白線・縁石。Googleタイルは表示専用。

1. **SR入力が劣化済み画像**: `pipeline/cache/shizuoka_ortho/` に native 0.20m/px
   の無圧縮TIFFが42メッシュ分残っているのに、SRパイプライン
   （`enhance_track_corridor.py`）の入力は8192px制限で0.2192m/pxへ縮小し
   JPEG q94で再圧縮した `satellite_shizuoka.jpg`。線形解像度で約10%と
   JPEGアーティファクト分を失った状態でGANに渡している（GANは圧縮ノイズを
   増幅しやすい）。
2. **近接ディテールシェーダーの適用漏れ**: `TrackBuilder.ts` の
   `attachGroundDetailShader`（舗装/芝のnormal/roughness/albedo合成、60→160mフェード）
   は下地の衛星地面にのみ適用されており、その上へ不透明に重なる
   `EnhancedCorridorGround`（SRタイル）には未適用。つまり `静岡 20cm SR` 選択時、
   カメラが最も見るトラック近傍でせっかくの質感レイヤーが隠れている。
   SRタイルも同じ `MeshStandardMaterial` なので技術的には適用可能。
3. **評価ゲートの選定ミス**: 「フル強度GANがLanczosにPSNR/SSIMで負ける」のは
   知覚-歪みトレードオフによる構造的な現象（NTIRE 2025は復元トラックと知覚
   トラックを別建てにしている）。PSNR/SSIMをゲートにする限り常にボケ側が勝ち、
   50%ブレンドという過剰な保険はこのゲートの帰結だった。

## 調査1: 超解像手法（2025-26年時点）

要点:

- **即差し替え候補**: `4xNomosWebPhoto_RealPLKSR`（なければ `4xNomos8kSC`）。
  いずれも **CC BY 4.0で商用可**。RealPLKSRは軽量・高速で、旧ESRGAN系の弱点
  「大きな平坦面（アスファルト・芝）にノイズ状アーティファクトを生む」
  （arXiv 2311.18082で文献確認）が小さい。WebP再圧縮劣化も学習済みで本件の
  出力形式と相性が良い。推論は chaiNNer / spandrel。
- **注意**: コミュニティ最人気の 4x-UltraSharp は **CC BY-NC-SA（商用不可）**。
- **品質の天井**: 手持ちの静岡20cmオルソ自体を教師に **neosrで自前ファイン
  チューニング**（RealPLKSR学習対応）。ドメイン内学習でアスファルト/芝の質感
  統計を直接学べ、自作なのでライセンス制約ゼロ。RTX 3090で数時間〜1日。
- **拡散系**: StableSR（S-Labライセンス）と SUPIR（重み）は**非商用限定で不可**。
  DiffBIR / OSEDiff は Apache-2.0 で可だが幻覚リスクが最大なので、既定
  パイプラインには入れず「決め絵」限定のオプション枠。
- **既製リモセンSRは不適合**: Satlas-SR等は「低解像衛星→高解像」タスクで、
  「既に高精細な20cm→10cm」とは逆方向。既製モデル探しより自前FTが実際的。
- **タイリング**: GAN/CNN系は決定論的なので、オーバーラップ64〜128px＋
  フェザー合成でシームは実務上解消できる（現行の context=32 は増やす）。
  4×推論→Lanczos 2×縮小の現行手順自体は合理的。**廃止すべきは50%ブレンド**で、
  強度は下記の新ゲートで決める。
- **評価ゲート刷新**: 主指標を **DISTS**（質感の再サンプリングに寛容・構造歪み
  に敏感）＋LPIPS、実スケール出力には無参照IQA（**MANIQA / MUSIQ / TOPIQ**、
  pyiqaで一括計測）。幻覚ガードとして **LR一貫性**（SR出力を入力解像度へ再縮小
  して入力とPSNR比較）と、既存ベクトル線と矛盾する線状構造の差分チェック。
  最終ゲートは実カメラ距離での人手A/B。

主要出典: [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)（BSD-3, tile不整合の明記）/
[4xNomosWebPhoto_RealPLKSR](https://openmodeldb.info/models/4x-NomosWebPhoto-RealPLKSR) /
[4xNomos8kSC](https://openmodeldb.info/models/4x-Nomos8kSC) /
[4x-UltraSharp（NC）](https://openmodeldb.info/models/4x-UltraSharp) /
[PLKSR](https://arxiv.org/abs/2404.11848) /
[ESRGAN平坦面アーティファクト](https://arxiv.org/pdf/2311.18082) /
[リモセンSRサーベイ2025](https://arxiv.org/html/2505.23248v1) /
[NTIRE 2025（復元/知覚の分離）](https://arxiv.org/html/2504.14582v1) /
[pyiqa](https://github.com/chaofengc/IQA-PyTorch) /
[neosr](https://pypi.org/project/neosr/) /
[DiffBIR](https://github.com/XPixelGroup/DiffBIR) /
[OSEDiff](https://github.com/cswry/OSEDiff) /
[StableSR（非商用）](https://github.com/IceClear/StableSR) /
[SUPIR（重み非商用）](https://github.com/Fanghua-Yu/SUPIR/blob/master/LICENSE)

## 調査2: 富士で20cmを超える画像ソース

**結論: ①富士SWをカバー ②真に20cm/px超 ③Web同梱可能なライセンス、の3条件を
満たす既製品は公共・民間・衛星のいずれにも存在しない。**

| ソース | 実GSD | ライセンス/条件 | 費用 | 判定 |
|---|---|---|---:|---|
| VIRTUAL SHIZUOKA LPオルソ（使用中） | 20cm・点群16点/m² | CC BY 4.0/ODbL | 無償 | 県オープンデータの天井 |
| 地理院 電子国土基本図オルソ | 当地は山間部扱い40cm | 出典表示で加工可 | 無償〜¥3,000/図郭 | 格下げで不適 |
| GEOSPACE（NTTインフラネット） | 25cm | Web公開ライセンスを明示販売 ¥37,500/図郭 | 数万円 | 唯一の「Web可」既製品だが20cm未達 |
| 国際航業 PAREA 10cm/5cm | 10cm/5cm | 内部利用限定・当地は非整備濃厚 | ¥15,000/km²〜 | カバー外濃厚（要確認） |
| パスコ Vexcel 7.5cm | 7.5cm | 三大都市圏中心 | 要問合せ | カバー外濃厚 |
| 商用衛星（Maxar/Pléiades Neo） | native 30cm | 再配布制限 | $5,000〜 | 解像度で不適。「15cm HD」はAI補間で実GSD 30cm |
| **ドローン写真測量（委託）** | **2〜3cm** | **発注者が全権** | **¥300〜800万**（150〜200ha） | 本命。運営会社の飛行許可が関門 |
| **富士SW/トヨタ系 保有データ** | 高精度（要確認） | 要交渉 | 要交渉 | 打診無料。前例あり |

- 富士SW保有データの前例: Assetto Corsa公認「富士」MOD（2020）は「富士SWの
  全面協力＋最新の測量データ」で制作されており、運営会社が精密測量データを
  第三者へ提供した実績がある。Project CARS 2もレーザースキャン実測。
  2003-05年のティルケ全面改修（大成建設・造成232ha）のas-builtも存在するはず。
  トヨタ系の測量会社 AERO TOYOTA が橋渡し役になりうる。
- ドローン: 小山町はDIDにほぼ非該当（要厳密照合）で、最大の関門は航空法より
  私有地＝運営会社の許可（閉鎖日調整）。GSD2〜3cm・GeoTIFF/LAS納品、
  対空標識＋RTK/PPKが標準。**富士は再舗装の計画・実施情報があるため（公式
  ニュース、要確認）、新規測量はその後に行うと現況を反映できる**。
  なおドローン測量はオルソだけでなく高密度点群も得られるため、Track Tools
  （自前LiDARトラック制作）の方向性とも合流する。
- 点群からの裏技は不可: 2019 LPは16点/m²（約0.25m間隔）で、20cmオルソが既に
  導出可能な最細。PLATEAU（小山町/御殿場FY2023）は3D建物・地形のみでオルソ
  非収録。

主要出典: [VIRTUAL SHIZUOKA](https://www.pref.shizuoka.jp/machizukuri/1049255/1052183.html) /
[2019データセット](https://www.geospatial.jp/ckan/dataset/shizuoka-2019-pointcloud) /
[地理院オルソ仕様](https://www.gsi.go.jp/gazochosa/gazochosa40001.html) /
[GEOSPACE航空写真](https://geospace.nttinf.co.jp/products/koukuu.html)・[二次利用販売](https://www.gisdata-store.biz/product/1847/) /
[PAREA-OrthoPhoto](https://www.kkc.co.jp/service/item/7772/) /
[15cm HDはAI補間](https://www.euspaceimaging.com/blog/2021/02/26/15-cm-hd-imagery-by-maxar/) /
[ドローン測量単価の目安](https://atcl-dsj.com/useful/drone-laser-survey-price/) /
[AC公認富士MOD](https://assettocorsa.games.dmm.com/news/detail/6303) /
[PLATEAU小山町](https://www.geospatial.jp/ckan/dataset/plateau-22344-oyama-cho-2023)

## 調査3: 近接地面のリアルタイム描画技法

要点（一次ソースはフライトシム/エンジン系。レースゲーム各社の路面シェーダーは
公開一次ソースなし＝業界慣行）:

- **定石はマクロ×マイクロ合成**: MSFS 2024 SDKのディテールマテリアルは
  「0.5を中立とするブレンド（未満で暗く/超で明るく）＋ディテール法線・ORMの
  加算＋マスクで強度制御」を公式仕様として公開。X-Planeのdecal段は
  「高解像リピートテクスチャをハードライト合成し、遠距離ではリピートが平均化
  して自動的に効果ゼロへフェード」する設計。UEも `DetailTexturing`
  マテリアル関数を公式提供。現行実装の「mip平均で割る mean-neutral 比率合成」
  は方向性としてこれらと同型で正しい。
- **反復感の抑制**: Heitz & Neyret のヒストグラム保存ブレンド
  （HPG 2018 / PACMCGIT 1(2):31, DOI 10.1145/3233304）が定番。three.js には
  既製の **`three-hex-tiling`**（v0.1.5, MIT, three>=0.151, r151-r173でテスト済、
  map/normalMap/roughnessMapに1マップ最大3タップ）があり即導入可能。
  ただし副作用インポートでシェーダーを全体パッチするため、既存の
  `onBeforeCompile` との競合確認が必要。
- **色はオルソに残し、ディテールは輝度と法線だけ**: アルベド写真から法線を
  生成すると焼き込まれた影が偽凹凸になるため、法線・粗さはノイズ由来の
  手続き生成が安全（現行の方針どおり）。
- **ゾーン別素材（スプラット）**: CC BYオルソのCV処理はライセンス上クリーン
  なので、ExG(=2G−R−B)＋Otsuで芝/舗装を2値化、必要ならGLCMやSLIC＋RFで
  砂利・縁石を分離し、RGBAスプラットマスクをオフライン生成 → シェーダーで
  舗装/芝/砂利のディテールセットを切替える。
- **three.jsエンジニアリング**: ミップLODバイアスの公式APIは無い
  （issue #26564）ためシェーダー内 `texture(sampler, uv, bias)` で行う。
  タイルのVRAMは 119×1024² RGBA8＋mipで約635MiB — KTX2/**UASTC**変換で約1/4
  （ETC1Sは航空写真でバンディングするため非推奨）。将来的には
  `CompressedArrayTexture` への集約も検討。スキッド痕は `DecalGeometry`、
  周回全体のラバー堆積はブレンドオーバーレイが安価。
- **推奨着手順**: (1)SRタイルへのディテール適用（灰中立オーバーレイ＋距離
  フェード） → (2)手続きアスファルト法線/粗さの強化 → (3)three-hex-tiling、
  の3点が最小工数で体感最大。次にスプラットゾーン別化。オルソ脱陰影は効果大
  だが工数大（L）で後回し。

主要出典: [MSFS 2024 SDK マテリアル仕様](https://docs.flightsimulator.com/msfs2024/html/3_Models_And_Textures/Textures/Materials/FlightSim_Material_Textures.htm) /
[X-Plane decals](https://developer.x-plane.com/article/using-decals-to-add-detail-to-scenery/) /
[UE DetailTexturing](https://dev.epicgames.com/documentation/unreal-engine/adding-detail-textures-to-unreal-engine-materials) /
[Heitz & Neyret 2018](https://eheitzresearch.wordpress.com/722-2/) /
[three-hex-tiling](https://github.com/Ameobea/three-hex-tiling) /
[three.js LODバイアスissue](https://github.com/mrdoob/three.js/issues/26564) /
[KTX2アーティストガイド](https://github.com/KhronosGroup/3D-Formats-Guidelines/blob/main/KTXArtistGuide.md) /
[ExGによる植生分離](https://isprs-annals.copernicus.org/articles/V-2-2022/367/2022/)

## 段階ロードマップ

### Phase 0 — 無料の情報回収（約0.5日）

- `enhance_track_corridor.py` の入力を、キャッシュ済みnative TIFF（EPSG:6676）
  からコリドー範囲を直接warpした無劣化モザイクへ変更（JPEG中間を排除）。
- 効果: SR入力の実情報+約10%（線形）とJPEGアーティファクト除去。
  GANの人工ノイズ源が減るためSR品質も向上。

### Phase 1 — SRエンジンと評価ゲートの刷新（1〜2日）

- モデルを 4xNomosWebPhoto_RealPLKSR（CC BY 4.0）へ差し替え。50%ブレンド廃止。
- ゲートを DISTS＋LPIPS（参照あり）/ MANIQA・MUSIQ・TOPIQ（無参照）/
  LR一貫性（幻覚ガード）/ ベクトル線との矛盾チェック / 人手A/B へ刷新。
- オーバーラップを64〜128pxへ拡大しフェザー合成。
- 将来枠: neosrで静岡オルソ自前FT（天井突破）、DiffBIR/OSEDiffは決め絵限定。

### Phase 2 — 近接ディテール描画（2〜4日、体感効果最大）

- `attachGroundDetailShader` 相当を `EnhancedCorridorGround` のタイルマテリアル
  へ適用（コリドーマスクはタイル自身のalphaを流用可能）。
- three-hex-tiling導入（onBeforeCompile競合の検証込み）。
- ExG＋Otsuによる舗装/芝スプラットマスクのオフライン生成とゾーン別素材。
- 距離フェード・タイリングスケールの調整（0.4m単一 → 複数スケール）。

### Phase 3 — 実情報量の更新（交渉・費用が必要、事業ゲート連動）

1. **富士SW/トヨタ系への保有データ照会（無料・即実行可）**: AC公認MODの前例を
   引きつつ、高精度測量データのライセンス可否を打診。
2. **ドローン写真測量の見積取得**: GSD2〜3cm・150〜200haで¥300〜800万の相場。
   有料パイロット成立（2027-01のGo/Kill）と連動して発注判断。再舗装後の実施が
   望ましい。成果は画像とLiDAR形状の両方でTrack Tools方向性にも寄与。
3. 茂原はすでに10cm交付制度あり（¥30,000）— 仕様と同梱可否の照会を先行。

### 非推奨（理由つき）

- 商用衛星: native 30cmで現状より粗く、「15cm HD」はAI補間＝本プロジェクトの
  「AI強化画像を測量真値にしない」方針にも反する。
- 生成AIタイルの全面適用: 既存試算どおり費用は小さいが、偽の線・不連続を生む
  リスクがベクトル線方針と衝突。限定補助のまま。
- 4x-UltraSharp等のNC系モデル: 商用不可。

## ライセンスと安全の境界（CLAUDE.md準拠の再確認)

- Google Map Tilesは表示専用を維持。SR・ディテール・セグメンテーションの入力に
  一切使わない。
- 加工はCC BY 4.0のVIRTUAL SHIZUOKA画像（と将来の自前取得データ）に限定し、
  マニフェストの出典・SHA-256・情報解像度の記録を維持する。
- SR出力・ディテール層は可視化レイヤーであり、形状抽出・計測・検証の真値には
  使わない（既存メタデータの明記を継続）。CC BYオルソへのCV処理（スプラット
  マスク生成）はライセンス上問題ない。

## 検証計画

- Phase 0/1: 新旧タイルのDISTS/LPIPS/無参照IQA比較レポートを
  `satellite_corridor_x2/validation/` に保存。LR一貫性とベクトル線矛盾の自動
  チェックを追加。実カメラ距離のスクリーンショットで人手A/B。
- Phase 2: Playwright実画面でシェーダー適用・タイル数・console/HTTPエラー0を
  確認し、チェイス視点の前後比較画像を保存（既存の検証フローを踏襲）。

## 追記: Phase 0〜2 実装結果（2026-07-16 同日実装）

- **Phase 0 完了**: `fetch_shizuoka_ortho.py` にPNG出力を追加し、native
  0.200m/px・被覆100%の無劣化モザイク（8782×8977）を生成。SR入力のJPEG中間を
  排除した。
- **Phase 1 完了**: `enhance_track_corridor.py` に spandrel エンジン
  （4xNomosWebPhoto_RealPLKSR、CC BY 4.0、RTX 3090 CUDA、weights sha256記録）を
  実装し既定化。50%固定ブレンドを廃止し、選定はDISTS argmin（LPIPSタイブレーク）、
  ガードはLR一貫性≥30dBと位相相関線シフトp95≤0.5px（PSNR/SSIMは報告のみ）。
  検証結果: **強度0.45自動採用、LPIPS 0.122→0.039（−69%）、DISTS 0.083→0.042
  （−50%）、LR一貫性41.8dB、線シフトp95 0.156px（≒1.6cm）、実スケールMUSIQ
  +8〜14**。s≤0.5ではPSNR/SSIMもLanczos比+1.4dB改善。140タイル/26MB/出力格子
  0.100m/pxを再生成し、モデル出所をマニフェストへ記録。
- **Phase 2 完了**: ディテールシェーダーを `groundDetail.ts` へ共有モジュール化し
  SRタイル（EnhancedCorridorGround）へ適用。ゾーンはin-shader ExG（2G−R−B、
  粗ミップ）で舗装/芝を判定しタイルalphaでゲート。反復抑制はHeitz & Neyret 2018の
  hex-tilingを**per-tapで平均中立な比率**として実装（構成的に色ずれ不可能）。
  舗装/芝別の手続きnormal/roughnessも同じ距離・alphaゲートで合成し、
  トグルはuniform共有で下地とタイル全140枚を一括制御。
- **実装過程で潜在バグ2件を発見・修正**:
  1. 比率の分母 `textureLod(…, 7.0)` が実行環境によってほぼ0を返し、全比率が
     クランプ上限へ張り付いて地面全体が約×2明化（v2シェーダー由来の潜在バグ。
     SRタイルに隠れて未発覚だった）。分母をミップ非依存の**CPU事前計算リニア平均
     uniform**へ変更。
  2. sRGB平均の計算順序誤り（平均→デコードは凸性で平均を過小評価）。texel毎に
     デコードしてから平均する方式へ修正。
- **検証**: 単体テスト全35ファイル295件PASS（pipeline新規8件・groundDetail新規
  5件を含む）、TypeScript+Vite本番ビルドPASS。Playwright実画面（凍結Freeカメラ）:
  141タイル認識・タイル/下地への専用シェーダーキー適用・console/page/HTTP/
  シェーダーエラー0・ディテールON/OFF画素差12.7%（地面のみ変化）。ピクセル単位の
  レイヤー切替検証により、近景に見える茶色帯はAC Overlayリボン（別機能の
  半透明可視化レイヤー）でありディテール層と無関係であることを確認。
- **教訓**: ①chaseカメラは静止後も数秒間イージングするため、描画の前後比較は
  Freeカメラで凍結してから行う ②シェーダーの正規化定数をミップチェーンに
  依存させない（ヘッドレス検証のSwiftShaderではtextureLodが黒を返した）
  ③ヘッドレス描画はSwiftShaderのため、最終的な見た目の合否は実GPUでの目視も
  併用する。
- **残作業**: 実GPUでの人手A/B目視、タイルのKTX2/UASTC化、スプラット高度化
  （GLCM/RF等での砂利・縁石分離）、neosrによる静岡オルソ自前ファインチューニング。

## 追記2: トーングレーディング（2026-07-16 夕、ユーザーFB対応）

「Googleの方がまだ良く見える」というフィードバックを受け、差の主因は解像度では
なく**仕上げ（グレーディング）**と判断。`pipeline/grade_ortho.py` を追加した。

- 処理: gray-world WB（±8%上限）→ 輝度パーセンタイルストレッチ（0.4/99.6%）→
  CLAHE局所コントラスト（LAB L、約25mタイル、clip 1.8）→ LAB彩度+12%。
  すべて位置保存のピクセル局所処理（リサンプリングなし・幾何変化なし）。
  **Google画素は入力にも較正基準にも使用しない**（表示専用ポリシー維持）。
- native モザイクをグレードし、**同一グレードから8K下地とSRタイルを再生成**する
  ことで、コリドーフェード境界の色継ぎ目を防止。メタデータに `toneEnhanced` を記録。
- グレード入力での知覚検証: 強度0.5自動採用、LPIPS 0.163→0.059（−64%）、
  DISTS −46%、LR一貫性40.0dB、線シフトp95 0.16px。
- 効果は before/after 比較（アスファルトの黒、ラバー痕、縁石、芝の色分離）で
  歴然。「解像度の打ち手」より費用対効果が高かった。

### 残る画質打ち手（優先順）

1. **neosr自前ファインチューニング**（グレード済み静岡オルソを教師に、RTX 3090で
   数時間〜1日）— SRの天井を上げる本命。
2. **実データ更新**（Phase 3: 富士SW照会=無料 → ドローン2〜3cm）— 情報量の天井。
3. タイルKTX2/UASTC化（VRAM 1/4、ロード高速化）とスプラット高度化。
4. 決め絵限定のdiffusion（DiffBIR/OSEDiff、幻覚ガード必須）。
