# R1：GNSS（10 Hz）＋IMU（50 Hz）＋車輪速の事後融合でどこまで位置を改善できるか

作成 2026-10-09。担当トラック R1（文献調査＋手元データでの適用可否の確認）。
数値には **[実測]**（本トラックで手元XRKから計算）、**[文献]**（出典の値、条件が違う）、**[推定]**（根拠を添えた見込み）を付ける。

## 要旨

- **XRKにはu-bloxのNAV-SOL（位置・Doppler速度ベクトル・pAcc/sAcc・pDOP・衛星数・GPS時刻）しか記録されていない。** 生の観測量（擬似距離・搬送波）が無いので、tightly coupled融合・PPK・RTKは既存データではできない。
- **[実測] 受信機の位置出力は、10秒より短い時間スケールでは既に滑らか。** Doppler速度と比べた横方向の不整合は、1周あたりRMS 0.12〜0.17 m、最大0.37〜0.55 m。計測周回では0.3 mを超える瞬間的な飛びは見つからなかった。
  - このため、IMU＋GNSSのEKF/RTSや因子グラフで取り除ける高周波誤差は、横方向で**0.1〜0.15 m程度が上限**[推定]。
- **IMU（とDoppler）では観測できない誤差：** セッション共通の絶対偏り、10秒以上かけてゆっくり変わる誤差。どちらもコース形状・縁石接触・動画など外部の基準でしか直せない（文献の一般論どおり）。
- **[実測] 7/30 L2 3250〜3900 mの「場所依存1〜2 m」誤差は、どちらの自己整合チェックでも他の周と区別できなかった。**
  - 位置とDoppler：区間内の横の残差最大 L2 0.59 m、他の周 0.69〜1.19 m
  - 位置とジャイロ：向き残差 L2 0.76°、他の周 0.49〜0.91°
  - この誤差が本物だとしても、IMU/Dopplerから見えない「ゆっくりした誤差」である。融合だけでは直らない。
- **[実測] 新しく見つかった時刻のずれ：** Doppler速度は位置より**0.090秒遅れている**（両日・全10周で0.084〜0.101秒）。
  - ジャイロ相互相関では、位置はジャイロと±0.05秒以内で揃っている（7/29は−0.033秒）。
  - 影響を受ける：`GPS Speed`（ビューアの速度表示）と、libxrkがDopplerから作る `GPS_InlineAcc/LateralAcc/Yaw_Rate`。
  - 影響を受けない：ビューアの車の位置（位置そのものを使っているため）。
  - 融合でDoppler速度を補正なしで使うと、0.09秒分の遅れが入り込む。
- **[実測] XRKのGPSタイムコードは、GPS時刻（iTOW）の厳密な一次関数**（残差1e-4 ms未満、全エポック100 ms）。届いた時刻を記録しているのではなく、GPS時刻から換算している。
  - 7/30の0.5秒は、この換算とロガー時計の対応づけのずれと整合する。
  - ジャイロと位置の進路変化率から、周ごとに独立推定した7/30のロガー時計オフセットは**0.509〜0.561秒**（L1→L6で増加）。出荷済みの周ごとのずらし（0.497〜0.583秒）と整合する。
- **結論：** 事後融合で効くのは次の3点。ずれの主成分（絶対偏り・ゆっくりした誤差）は外部の基準でしか動かない。
  1. 時刻モデル：ロガー時計のオフセットと漂い、Dopplerの0.09秒遅れ
  2. 50 Hzの姿勢と見た目の滑らかさ
  3. アンテナ取付位置の推定と異常検出

## 1. 手元データの事実（このトラックで確認）

| 項目 | 7/29（wet） | 7/30（dry） | 種別 |
|---|---|---|---|
| GPSの記録 | NAV-SOL 56 B/件、10 Hz、7922件 | 9741件 | [実測] libxrk実装＋生バイト解析 |
| ロガーtimecode と GPS iTOW | 傾き 約0 ppm、残差 9.5e-5 ms以下 | 同左 | [実測] |
| 受信機が申告する精度 pAcc（走行中） | 中央値 0.90 m（p95 1.28） | 0.85 m（p95 1.89） | [実測] |
| sAcc（速度精度） | 中央値 0.15 m/s | 0.15 m/s | [実測] |
| 使用衛星数（最小/中央値）・pDOP | 7/13・1.5〜2.1 | 8/13・1.5〜2.2 | [実測] |
| DiffSoln（差分補正の適用、SBAS等）フラグ | 1395エポックで外れ。すべてアウトラップ（約140秒付近、5回切替） | 常時オン | [実測] |
| 位置の増分 と Doppler積分 の1エポック残差（遅れ補正後、RMS） | 前後 0.031 m／横 0.069 m | 0.021／0.039 m | [実測] |
| Doppler速度の位置に対する遅れ（増分での探索） | 最良τ=0.09 s | 0.10 s | [実測] |
| 同上（ジャイロ相互相関、Doppler進路 − 位置進路） | +0.090 s（4周 0.089〜0.093） | +0.092 s（6周 0.084〜0.101） | [実測] |
| 位置進路のジャイロに対する遅れ | −0.033 s | +0.534 s（周ごと 0.509〜0.561、ロガー時計オフセット） | [実測] |
| 10秒の高域通過をかけた横の不整合（1周） | RMS 0.12〜0.14 m、最大 0.37〜0.45 m | RMS 0.13〜0.17 m、最大 0.42〜0.55 m | [実測] |
| 同上、他周の中央値との差 | RMS 0.07〜0.08 m | RMS 0.07〜0.09 m | [実測] |
| 1周の経路長（位置）と Doppler速度の積分の差 | −1120〜−1383 ppm（−5.0〜−6.3 m） | −515〜−1183 ppm（−2.3〜−5.4 m） | [実測] 原因は未特定：受信機の位置平滑による角の内側通り（corner cutting）か、Dopplerの尺度か |
| ジャイロと位置の向き残差（直線区間 3900〜4450 m） | 0.19〜0.25° RMS（横に換算0.2〜0.35 m） | 0.12〜0.20°（L6のみ0.40°） | [実測] 直線ではこの程度の分解能がある |
| 同上（コーナー区間） | 0.4〜2.2° RMS（横に換算0.4〜4 m） | 0.3〜1.1°（0.4〜2.4 m） | [実測] 周ごとの横滑り角の違いに埋もれる |

スクリプトとJSON：`navsol_probe.py/.json`（時計・遅れ・不整合）、`navsol_band_check.py/.json`（ゆっくりした成分、区間）、`navsol_yaw_lag.py/.json`（ジャイロ相互相関）、`navsol_gyro_band.py/.json`（ジャイロ向き残差）。入力は環境変数 `FUJI_WET_XRK` / `FUJI_DRY_XRK`。動画は使っていない。

## 2. 手法ごとの「直せるもの／直せないもの」

| 手法 | 直せる | 直せない | 文献での効果（条件） | この車両データでの見込み |
|---|---|---|---|---|
| **GNSS/INSの疎結合EKF＋RTS（固定区間）平滑**（Doppler速度・車輪速・横滑り込み拘束を観測に） | 高周波雑音、短い欠落・飛びの橋渡し、50 Hz姿勢、IMUバイアス、時刻オフセットの状態推定 | 絶対偏り。10秒以上の位置誤差は、Dopplerの積分も漂うため区別できない | RTSは欠落時に大きく効く：180秒欠落で位置95%改善（Jouybari 2022、航空・DGNSS）。全受信時のMEMS/DGPSは約0.4 m（Godha 2006） | 横の改善は**≤0.10〜0.15 m RMS**[推定、根拠は§1の高周波不整合]。絶対値は0 |
| **因子グラフによる一括平滑（GTSAM流）＋頑健カーネル**（切替拘束・Huber・GNC） | 上の全部に加え、周/セッションの偏りノード、時刻オフセット・アンテナ位置のノード、縁石・道路端の因子を1つの最適化に入れられる。不確かさ（共分散）も出る | 外部の基準なしでは絶対偏りは不定 | 疎結合でEKF→FGO：平均誤差9.14→7.01 m（−23%、香港の高層街路）。密結合FGOは3.64 m（擬似距離が必要。本データでは不可）（Wen 2021） | 開けたサーキットでは雑音が小さく、改善幅は上の行と同程度。価値は「縁石・端・時刻・アンテナ位置を同時に推定し、幅を出せる」点。±0.7 mが±0.4〜0.5 mになる可能性[推定・未検証] |
| **車両運動の拘束**（横方向に動かない仮定＋横滑りモデル、車輪速での走行距離） | 欠落時の漂い、向き・姿勢 | 絶対偏り。限界走行では横方向の仮定が破れる | 横方向拘束・走行距離は欠落時の漂い抑制に広く使われる（GO-INO 2022 ほか）。横滑り角は一般運転で2°以下、ESCのしきい値の例は乾燥3〜4°・湿潤1〜2°（特許・総説） | 40 m/sで横滑り3°なら横速度約2 m/s。拘束は横滑りモデル付きか緩めて使う必要がある。車輪速は1 km/h刻み（XRKのdec_pts=0）で、滑りもある |
| **アンテナ取付位置（レバーアーム）推定** | 車体固定のずれ。向きで回るので周ごとの平行移動では吸収できない | — | GNSS/INSの標準的な状態量（旋回時に観測できる） | ヨー50°/s・1 mのずれで0.87 m/sの速度差。Doppler（0.15 m/s）×数千エポックで±0.1〜0.2 mに推定できる見込み[推定]。GPS-REG-01の独立した検算になる（10/02の縁石推定は右−0.02・前+0.04 m） |
| **コース拘束付き平滑**（station／横オフセットの座標で、道路幅を不等式拘束に） | 道路外の位置、端に張り付く方向の誤差 | 道路内のどこを走ったか（拘束が効かない区間）、絶対偏り | 不等式拘束付きKFの理論（Simon 2010）。HMMマップマッチング（Newson & Krumm 2009）は道路への吸着なので横の情報を失う。走行ラインの解析には不適 | 拘束が効く場所だけで効く。時間変化する補正は実際のライン差を吸収する危険がある（9/20方針）。周/縁石を外す交差検証が必須 |
| **周回の合意（multi-lap consensus）＋周間の進行方向合わせ** | 周差（KPIの周比較）、周固有の異常の検出、進行方向のそろえ | セッション共通の偏り | GNSSのコード誤差は100〜1500秒の相関を持つ（Jada & Joerger 2025）。同じセッションの周は誤差を共有する。路面の形状でのstation推定は試験路で1 m、高速道路で1 km走行後5 m（Dean 2008） | ゆっくりした成分の「その周の値 − 中央値」は0.28〜0.79 m[実測]。進行方向は縁石打音・制動開始で±1〜2 m（50 Hz×40 m/s=0.8 m/標本）[推定] |
| **縁石接触を基準点に使う**（因子として） | 局所的な横位置（場所依存の誤差も、基準点の周辺だけ） | 基準点の間 | 路面イベントを目印にする位置推定（Dean 2008/2011） | 既存の候補（?gps=kerb）は周回の平行移動の形。局所因子にすると7/30 L2を局所補正できる可能性。ただし基準点の不足（7/29は強いラベル1件）、過適合の恐れがある。縁石ブロック線p95 0.128 m＋オルソ0.2〜0.3 mで、1点あたり±0.3〜0.5 m[推定] |
| **異常（マルチパス・飛び）の検出** | 飛びの印付け、除外 | 補正そのもの | イノベーションのχ²検定・頑健最適化（Sünderhauf 2012/2013、Wen 2021 GNC）。受信機のpAcc・衛星数 | 計測周回では0.3 m超の飛びなし[実測]。L2 3250〜3900も他の周と区別できない[実測]。ジャイロは直線で0.2〜0.35 mまで分解できるが、コーナーでは横滑りに埋もれる |
| **生の観測量を使う密結合・PPK・RTK** | 絶対・相対ともcm〜dm級 | — | 密結合FGO 3.64 m vs 疎結合7.01 m（Wen 2021、都市）。開けた場所でのRTK fixはcm級 | **既存データでは不可**（NAV-SOLのみ）。今後の撮影向け（RAW出力の受信機・RTK・基準局） |

## 3. 誤差成分ごとのまとめ

- **高周波雑音（<10秒）：** 実測の上限は横0.15 m RMS程度。融合しても見た目の滑らかさ以上の改善は小さい。ビューアは既に先読み平滑済み（向きp95 0.075°）。
- **短い飛び（例：7/30 L2）：** 自己整合では検出できなかった。L2の異常が本物なら、10秒以上の時間スケールで位置とDopplerが同じ誤差を共有している。IMUだけでは観測できない。直すには縁石・道路端・動画（カメラ較正後の境界残差）が必要。
- **ゆっくりした漂い（1周以内）：** Doppler積分の漂い（1周で2〜6 m）が同じ大きさなので区別できない。周回の合意・外部の基準で扱う。
- **絶対偏り（セッション共通）：** IMUでは原理的に観測できない。外部の基準（コース境界・縁石・測量点・動画）だけ。現行の周平行移動（±0.7 m）が実質的な上限で、改善はアンカーの質で決まる。
- **時刻：** 融合の枠組みは時刻オフセットと漂いの推定に強い（Skog & Händel 2011）。今回、ジャイロ対位置で7/30の周ごとのオフセットを独立に再現した。Dopplerの0.09秒遅れは新しい知見。

## 4. 推奨（優先順）

1. **[小・既存データ]** Dopplerの0.09秒遅れを記録し、速度表示とDoppler由来チャネルを−0.09秒ずらすか位置から作り直すかを判断する。過去の「ブレーキ→GPS速度の減速開始」系の数値に0.09秒が含まれていないか再確認する。
2. **[小・既存データ]** GPS-REG-09（時刻ずれの自動検査）に、ジャイロ対位置の進路変化率の相互相関を加える。既存の加速度相関と独立な2つ目の方法になる。周ごとの値の幅が0.05秒以内かで判定する。
3. **[中・既存データ]** 因子グラフ（またはEKF＋RTS）を「推定器」ではなく**検算器**として作る。推定するのは次の4つ：アンテナ取付位置、時刻オフセットと漂い、横滑りモデル付きの残差、周ごとの偏りの幅。表示する位置は現行の周平行移動を維持する。
4. **[中]** 横滑りモデル（ステア・速度・ヨーレート）でコーナーの横滑りによる隠れを減らし、7/30 L2仮説を再検定する。
5. **[将来の撮影]** 生の観測量が取れる受信機かRTKの追加、アンテナとカメラ位置の実測。20〜30 cm級を狙うならここが本命（GPS-REG-07）。

## 5. 出典

- Wen, Pfeifer, Bai, Hsu (2021) *Factor graph optimization for GNSS/INS integration: A comparison with the extended Kalman filter*, NAVIGATION. 数値はプレプリントの表2: https://arxiv.org/abs/2004.10572 ／ https://www.polyu.edu.hk/aae/ipn-lab/us/en/publications/2021-factor-graph-optimization-for-gnssins-integration-a-compar/
- Jouybari, Bagherbandi, Nilfouroushan (2022) *Impact of GNSS signal outage on EOPs using forward Kalman filter and smoothing algorithm*, ISPRS Archives XLIII-B2-2022: https://isprs-archives.copernicus.org/articles/XLIII-B2-2022/59/2022/isprs-archives-XLIII-B2-2022-59-2022.pdf
- Chiang et al. (2012) *On-line smoothing for an integrated navigation system with low-cost MEMS inertial sensors*, Sensors 12(12):17372: https://www.mdpi.com/1424-8220/12/12/17372 （手法のみ。数値は未取得）
- Godha (2006) MSc thesis, U. Calgary: https://www.ucalgary.ca/engo_webdocs/MEC/06.20239.SGodha.pdf （約0.4 m／7 mは検索要約の値。本文は未精読）
- Skog & Händel (2011) *Time synchronization errors in loosely coupled GPS-aided inertial navigation systems*, IEEE T-ITS 12(4):1014–1023
- Jada & Joerger (2025) *Measurement error time-correlation modeling for safety-critical navigation*, NAVIGATION 72(4): https://navi.ion.org/content/72/4/navi.721
- Dean, Martini, Brennan (2008) *Terrain-based road vehicle localization using particle filters*（PSU）: https://www.me.psu.edu/toboldlygo/Publications/Conferences/2008_Dean_TerrainBasedRoadVehicleLocalizationUsingParticleFilters.pdf ／ 高速道路評価: https://www.me.psu.edu/toboldlygo/Publications/Conferences/2008_Dean_HighwayEvaluationOfTerrainAidedLocalizationUsingParticleFilters.pdf
- Newson & Krumm (2009) *Hidden Markov map matching through noise and sparseness*, ACM SIGSPATIAL: https://www.microsoft.com/research/publication/hidden-markov-map-matching-noise-sparseness/
- Simon (2010) *Kalman filtering with state constraints: a survey*, IET CTA 4(8): https://engagedscholarship.csuohio.edu/enece_facpub/14/
- Sünderhauf, Obst, Wanielik, Protzel (2012/2013) GNSSの頑健最適化・切替拘束（TU Chemnitz 文献一覧）: https://www.tu-chemnitz.de/etit/nt/publikationen/publikationen.php?jahr=2012 ／ Wen, Zhang, Hsu (2021) GNC FGO: https://arxiv.org/abs/2109.00667 ／ Watson & Gross (2017) GTSAM頑健GNSS: https://arxiv.org/abs/1806.08899
- GO-INO (2022) MEMS-IMU/横方向拘束/走行距離の因子グラフ: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9503408/
- 車両の横滑り：Chindamo et al. (2018) 総説 https://shura.shu.ac.uk/18721 。しきい値の例は特許 https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6449542 ほか
- 高速レースカーの状態推定：Wischnewski et al. (2019) IFAC doi:10.1016/j.ifacol.2019.08.064 ／ Goblirsch et al. (2024) https://arxiv.org/abs/2408.14885
- u-blox M8級の仕様（CEP 2.0〜2.5 m、速度0.05 m/s）: https://sparkfun.com/products/14198 ほか。GPS08の内部受信機の型は未確認
- Racelogic VBOX LapTimer（2 m 95% CEP、位置ベースの周比較）: https://en.racelogic.support/VBOX_Motorsport/Product_Info/Lap_Timing/VBOX_LapTimer_(RLVBLAP-R)
- libxrk（XRKのNAV-SOL 56バイト配置）: 導入済みパッケージの `aim_xrk.pyx` `_decode_gps`

## 6. 制約・未検証

- Doppler 0.09秒の遅れの発生源（受信機内部か、ロガーの組み立てか）は特定していない。また、絶対時刻に対してどちらが正しいかは、ジャイロとの比較（横滑りの動特性を含むため±0.05秒程度）でしか判断していない。
- 1周の経路長の差（−500〜−1400 ppm）の原因は未特定。
- ジャイロ向き残差の「横に換算した値」は、向き残差を距離で積分した桁の目安。横位置の実測ではない。
- 融合器そのものは実装・評価していない。表の改善見込み（[推定]）は検証していない。
