# R3 取得手段・ローカルフォレンジック — 位置ずれ調査 2026-10-09

## 要旨

- **2020年の富士ログには GNSS 生観測量（擬似距離・搬送波位相・ドップラーの衛星別値）が一切無い。PPK/PPP/GEONET 差分による再測位は不可能。**
  `.xrk` の GPS は u-blox **UBX-NAV-SOL**（受信機が計算済みの ECEF 位置/速度、10 Hz）だけ。
  `.rrk` はその**バイト一致コピー**、`.gpk` はそこから作った **20 Hz の局所 ENU 派生トラック**（中間点は線形補間）。
  `.drk`/`.bak` は Race Studio 系の解析 DB（RDX 形式）で、両者の差は「Series 名 STai→Stai」等 2 バイトだけ。
  （実測。手順は §1。対象 20 セッション・19 組の gpk/rrk をすべて走査済み。）
- 受信機は全ラップで差分補正フラグ（diffSoln）が立っていた（SBAS=MSAS とみられる。補正の種類は記録されていない）。
  自己申告精度 pAcc は中央値 0.81–0.92 m、p95 ≤1.06 m。**7/30 L2 の 3250–3900 m（既知の 1–2 m 局所誤差区間）でも pAcc・衛星数・PDOP は区間外と変わらず**、受信機の品質値では悪い区間を検出できない（実測）。
- XRK の GPS タイムコードは **iTOW（GPS 時刻）+ 定数**（ジッタ 0、ドリフト 0 ppm）。GNFI（ロガー内部時計）メッセージは無い。
  つまり GPS とロガー時計チャンネルを直接結ぶ時刻情報はファイル内に無い（7/30 のチャンネル 0.5 秒ずれが相関推定でしか直せなかった理由と整合）。
- 車載動画は 1280×720・**30 fps**、映像＋PCM 音声 2 本のみで、データ/タイムコードトラックは無い（ffprobe 実測）。
- 将来の 0.1–0.3 m を狙う最小構成は、**多周波 RTK 受信機（ZED-F9P 級）を AiM と並行して載せ、生観測量（RAWX/RINEX）を必ず記録**すること。
  リアルタイム補正は **CLAS（みちびき L6、無料、移動体水平 ≤12 cm 95%）** か商用 NTRIP（ichimill 等）を使い、失敗時は GEONET を基準にした PPK で救う。
  これに加えて**アンテナ・カメラのレバーアーム実測**、**チェッカーボードでのカメラ校正**、**縁石端の GCP 測量**、**映像と GNSS 時刻の同期（PPS LED 等）**が必要。
  これらが揃わないと、RTK にしても誤差の大半はカメラ・時刻・基準モデル（オルソの地理参照 0.2–0.3 m、未測定）側に残る。

---

## 1. ローカルフォレンジック（読取専用）

### 1.1 対象ファイル（解析対象 2 セッション）

| 拡張子 | 7/29 `…_1715` サイズ (B) | 7/30 `…_1741` サイズ (B) | 先頭マジック | 判定 |
|---|---:|---:|---|---|
| .xrk | 3,283,910 | 4,040,243 | `<hCNF` | AiM ロガー生データ。GPS は `<hGPS` メッセージ（7,922 / 9,741 件） |
| .rrk | 507,072 | 623,488 | `HEAD` | `NSOL` 64 B レコード（7,922 / 9,741 件）= XRK と同じ NAV-SOL |
| .gpk | 2,281,632 | 2,805,504 | `PROV` | `PSOL` 144 B レコード（15,844 / 19,482 件、50 ms 間隔）= 派生 ENU トラック |
| .drk | 1,498,366 | 1,827,634 | `RDX\x02` | Race Studio 系 Analysis 用 DB（RS2 系と推定。ヘッダと派生チャンネル名から判断）。2020-08-05 更新 |
| .bak | 1,498,366 | 1,827,634 | `RDX\x02` | 上記 .drk の編集前バックアップ |

同じフォルダの他の 18 セッション（.xrk 計 20 本、.gpk/.rrk 計 19 組。1 本は .gpk/.rrk が無い短いログ）も走査した。
record tag は .rrk = {HEAD, NSOL}、.gpk = {PROV, PSOL} だけで、XRK のヘッダトークンも通常セット
（CNF/CHS/CDE/GRP/LAP/GPS/CAL/TRK/… 。ペイロード内のバイト列が偶然一致した 4 件を除く）だけだった。
**RAWX/RXM/SFRB/GNFI/GPSR 等は 0 件**。証拠: `tags.json`、`decode.json`。

### 1.2 各形式の中身と根拠

- **XRK `<hGPS`**: 1 件 56 B = ロガー時刻 ms(4) + **UBX-NAV-SOL ペイロード 52 B**
  （iTOW, fTOW, week, gpsFix, flags, ecefX/Y/Z [cm], pAcc, ecefVX/VY/VZ [cm/s], sAcc, pDOP, numSV）。
  libxrk 0.12.0 の `_decode_gps` の説明と同じ構造。GPS 週 2116（2020-07-26 起点）、gpsFix は全件 3（3D）。
- **.rrk**: `HEAD` ヘッダの後に `NSOL` + ロガー ms(4) + NAV-SOL 52 B + 0 埋め 4 B。
  XRK の GPS と iTOW で突き合わせると、**12 フィールドが全件一致**（7/29 7,922/7,922、7/30 9,741/9,741）。新しい情報は無い。
- **.gpk**: `PROV` ヘッダ（基準点 ECEF の倍精度 3 値。7/29 は 35.36986°N 138.92688°E 付近でコース中央。レコード数も入っている）
  の後に `PSOL`（ロガー ms, week, iTOW, fix, flags + double×16）が続く。
  - 列 0/1 は ECEF の一次式で、係数は基準点の ENU 回転行列（East = −0.657X −0.754Y …）と一致する。
    NAV-SOL と同じ時刻での残差 RMS は 6–10 mm（cm 量子化レベル）→ **東・北**。
    列 2（高さ）は残差 2–5 m で、平滑化/加工済み。
  - 列 3 = pAcc、列 7 = sAcc（NAV-SOL と 1e-7 一致）、列 4–6 = ENU 速度、列 8 = 速度 [m/s]、列 10 = 累積距離 [m]。
  - 50 ms 間隔の中間レコードは前後の線形中点と一致（水平 中央値 6–7 mm）→ **補間点で、新しい観測ではない**。
- **.drk/.bak**: `RDX\x02` 形式。GPS 系は派生チャンネル名（GPS_Speed, GPS_Nsat, GPS_PosAccuracy, GPS_Heading, GPS_Slope, GPS_Gyro …）だけ。
  .drk と .bak の違いは 2 バイト（オフセット 1060 のフラグ 0x5d→0x7d、1449 の Series 名 `STai`→`Stai`）だけ。

### 1.3 結論: PPK は可能か

**不可能**。PPK/PPP/RTK 後処理には衛星ごとの擬似距離・搬送波位相（と LLI/C/N0）が要る。
今回のファイルにあるのは受信機の最終解（NAV-SOL）だけで、使用衛星の内訳（NAV-SVINFO/NAV-SAT）すら無い。
GEONET の観測データがあっても、移動局側に組み合わせる観測量が存在しない。

### 1.4 付随して分かったこと（実測）

| 項目 | 7/29 (fuji_aim_01) | 7/30 (fuji_aim_2020_07_30) |
|---|---|---|
| diffSoln フラグ（全エポック） | 82.4%（非補正 1,395 件はすべてラップ 1 開始前の約 141 秒間） | 100% |
| 再生対象ラップの diffSoln | 4/4 ラップで 100% | 6/6 ラップで 100% |
| pAcc（受信機の推定値、走行中） | 中央値 0.90 m、p95 1.28 m | 中央値 0.85 m、p95 1.89 m |
| ラップ別 pAcc 中央値 / p95 | 0.83–0.92 / 1.02–1.06 m | 0.81–0.87 / 0.89–0.98 m |
| 衛星数（走行中） | 中央値 13（7–15） | 中央値 13（8–15） |
| PDOP（走行中） | 中央値 1.65 | 中央値 1.60 |
| GPS タイムコード − iTOW | 一定（値は 1 種、ドリフト 0 ppm） | 一定（同） |
| GNFI（ロガー時計）メッセージ | 0 | 0 |

- 7/30 L2 の 3250–3900 m: pAcc 中央値 0.86 m（区間外 0.82 m）、衛星数 14（14）、PDOP 1.57（1.58）。
  **既知の 1–2 m 局所誤差を受信機の品質値は示していない**（`navsol_lap_quality.json`）。
  ラップ JSON の緯度経度と NAV-SOL の対応は最大 0.5–1.4 m の差（同じエポックで、内挿時刻の違いとみられる）。区間統計は ±1 エポックの精度。
- diffSoln=1 は u-blox の定義で「差分補正を適用」の意味。2020 年 4 月以降、日本の SBAS（MSAS）は QZS-3 静止衛星から放送されている。
  補正の種類は NAV-SOL に記録されないため、**SBAS だとする判断は推定**。
- 動画 SCHD0613/0624.MOV: H.264 1280×720・30/1 fps、他は PCM 音声 2 本のみ。
  creation_time は秒単位（7/30 は 15:53:18。ロガー開始 15:53:29 の 11 秒前）。サブフレーム同期には使えない。

---

## 2. 生観測量なしで可能な GNSS 後処理（c）

| 手段 | 可否 | 理由 / 期待値 |
|---|---|---|
| PPK（GEONET/CORS 基準） | **不可** | 移動局の搬送波位相・擬似距離が無い |
| PPP / MADOCA 後処理 / 精密暦での再測位 | **不可** | 同上。衛星別の再計算ができない |
| GEONET 補正を位置ドメインで適用 | **不可（実質）** | 使用衛星と重み・SBAS 状態が不明。同じ受信機・同じ衛星選択の静止局が同時に記録していた場合だけ近似的に成り立つが、2020 年には存在しない |
| 受信機品質値（pAcc/PDOP/衛星数）で悪い区間を除外 | 効果小 | §1.4 のとおり、既知の悪い区間を検出できない |
| 位置ドメインの登録（コース形状・縁石・映像目標への当てはめ） | 可（実施済み） | per-lap 平行移動（gps_registration.json）。絶対値は基準モデル精度（KPI ±0.7 m、オルソ 0.2–0.3 m 未測定）が上限 |
| NAV-SOL 位置（pAcc 重み）＋ドップラー速度（sAcc 0.15 m/s）＋IMU＋車輪速の RTS 平滑化 | 可 | 10 Hz の雑音とラップ内の形状を改善。共通バイアス（絶対位置）は直らない。時間変化バイアスは、GPS に依存しない拘束（縁石接触、映像目標）がある所でだけ推定できる |
| 時間変化バイアスのモデル化（区間ごとのランダムウォーク） | 可（要拘束） | 7/30 L2 型の局所 1–2 m 誤差に効く可能性。拘束点が疎だと過適合する（?gps=kerb 候補と同じ問題） |

---

## 3. 将来セッション向けの取得手段（a）

### 3.1 誤差予算の考え方（数値は概算）

| 成分 | 現状 | 0.1–0.3 m に必要なこと |
|---|---|---|
| アンテナ絶対位置（横・前後） | SBAS 単独 0.8–0.9 m（pAcc、推定値）、局所 1–2 m。登録後でも ±0.7 m | RTK/PPK の Fix 解: 1–3 cm（ZED-F9P 仕様 0.01 m + 1 ppm CEP）。CLAS は移動体 ≤12 cm (95%) |
| アンテナ→車両基準点/カメラのレバーアーム | 未測定（カメラ高さ 1.25 m は仮定） | 巻尺・レーザーで ±2 cm。横方向の影響は L·sin(β)（L=1.5 m、横滑り角 β=5° で 0.13 m）、前後方向は L そのもの |
| 向き（ヨー） | GPS 進行方向から推定。カメラヨー −0.69° は当てはめ値 | 2 アンテナ方位、または RTK+IMU。0.5° の誤差は 30 m 先で 0.26 m、50 m 先で 0.44 m の画面上の横ずれ |
| 映像と GNSS の時刻同期 | ペダル表示照合で ±0.03–0.05 s → 50 m/s で前後 ±1.5–2.5 m 相当 | 50 m/s で 0.1 m を得るには ≤2 ms。PPS 点滅 LED の撮影、または GNSS 時刻付き映像機材 |
| 30 fps のフレーム間隔 | 33 ms（50 m/s で 1.7 m 進む） | 撮影時刻が正確なら CG をその時刻で描けばよく、量子化は問題にならない。同期精度の方が支配的 |
| 基準モデル（CG/オルソ） | 縁石線 p95 0.128 m（同じオルソ内）。オルソの地理参照 0.2–0.3 m は未測定 | 縁石端などの GCP を RTK で測量（2–6 cm）し、オルソと CG を検証・補正 |

→ **RTK だけ入れても、レバーアーム・時刻・基準モデルの 3 つを押さえないと 0.1–0.3 m の比較にはならない。**

### 3.2 選択肢の比較

| 選択肢 | 精度（出典の値） | 補正源 | 概算費用 | 富士での実用性 | 生観測量の記録 |
|---|---|---|---|---|---|
| **u-blox ZED-F9P ボード + 多周波アンテナ**（ArduSimple simpleRTK2B、SparkFun 等） | RTK 0.01 m + 1 ppm CEP、RTK 最大 20 Hz | CLAS（D9C 併用）/NTRIP/自前基地局/PPK | ボード 2–3 万円、アンテナ 1–3 万円（**推定**） | 小型。ルーフに磁石台座とグランドプレーン | ○（UBX-RXM-RAWX） |
| **+ NEO-D9C（CLAS L6 受信）** ArduSimple キット | CLAS 移動体 水平 ≤12 cm・垂直 ≤24 cm (95%)、TTFF ≤60 s（静止 6/12 cm） | QZSS L6D（無料、通信不要） | €99（掲載時の価格） | 通信圏外でも使える。補正データの中継・変換はホスト側の責任（u-blox C101-D9C 手引き） | ○ |
| **u-blox ZED-F9R**（RTK + IMU + 車輪パルス推測航法） | RTK 約 0.01 m、センサ融合で最大 30 Hz | 同上 | ボード 3–4 万円（**推定**） | 橋・スタンド下の途切れを推測航法で補える | ○ |
| **商用ネットワーク RTK**（SoftBank ichimill） | cm 級（神戸清光の検証で水平 1 cm 以内） | NTRIP（全国 3,300 か所以上の独自基準点） | 月額数千円（法人契約） | サーキットのモバイル通信品質に依存（**要現地確認**） | 受信機次第 |
| Swift Skylark | 1 cm RTK $69/月、sub-dm $29/月 | NTRIP | 同左 | 同上 | 受信機次第 |
| **MADOCA-PPP**（みちびき、無料） | 収束後の水平 約 10 cm。収束 ≤30 分（電離層補正の併用で <10 分を評価中） | L6E またはインターネット | 無料（D9C 等が必要） | 走行前に 30 分ピットで収束させる必要がある。途切れた後の再収束が弱点 | ○ |
| **Emlid Reach M2**（PPK 前提の小型受信機） | RINEX 最大 20 Hz 記録、PPK 基線 最大 100 km、RTK 60 km | GEONET/自前基地局で PPK | $785 / £649 | ログ取りに専念できる | ○（RINEX 直接） |
| **Racelogic VBOX 3i Dual Antenna RTK** | 100 Hz、基地局使用時 2 cm、横滑り角・ピッチ/ロール | Racelogic 基地局 / NTRIP | 見積のみ（**最も高価な選択肢と推定**） | モータースポーツ・ADAS 試験の標準機 | 機種による |
| VBOX Video HD2（参考） | 内蔵 10 Hz GNSS は ±3 m (95% CEP) | — | — | 映像と GNSS の同期は優秀。位置精度は外部 RTK が必要 | — |
| **AiM GPS09c / GPS09c Pro** | 25 Hz、4 衛星系、**0.5 m CEP**（Pro は 6 軸 IMU 100 Hz） | なし（RTK 無し） | 数万円（**推定**） | AiM で完結。ただし掲載の互換表に MXL2 は無い（**要確認**） | × |
| AiM GPS08（現行） | GPS+GLONASS、「平均 1 m 未満」（販売店の表記）。実データは 10 Hz NAV-SOL | SBAS | — | — | × |

- AiM 純正の RTK 製品は見つからなかった（GPS09c の仕様に RTK の記載なし）。
- Race Studio 3 の「GPS オフセット/トラック合わせ」は公式機能として確認できなかった。
  ユーザーフォーラムでは「組込みのオフセット機能は無く、外部ツールで周をずらしている」とされる（非公式情報）。

### 3.3 推奨構成（段階別）

1. **最小（ほぼ無料・すぐできる）**: レバーアーム実測（アンテナ・カメラ・ロガー）、カメラのチェッカーボード校正、
   映像に GNSS の PPS で点滅する LED を写す（または走行前後にクラッパーとロガーの急加速度イベントを撮る）。
2. **標準（数万円）**: ZED-F9P + 多周波アンテナ + NEO-D9C を AiM と並行して搭載する。
   CLAS のリアルタイム解と **RAWX の生観測量**を microSD に記録する。後日 GEONET（terras）の RINEX で PPK して検証・救済する。
   AiM の NAV-SOL は iTOW を持っているので、**外部 GNSS とは GPS 時刻で直接つなげる**（GPS 系チャンネルについては）。
3. **上位**: 2 アンテナの移動基地局構成（F9P×2、基線 ≥0.5–1 m）で方位/横滑り角も取る。または VBOX 3i Dual Antenna RTK。
   ZED-F9R の推測航法で途切れを埋める。

### 3.4 富士スピードウェイでの実務メモ

- CLAS は通信不要で、国内陸域が対象。NTRIP はパドック/コース上のモバイル通信品質に依存する（**未確認**）。
- 近くの GEONET 局には例えば「御殿場（93072）」がある（気象庁資料）。**コースからの距離は未計算**。terras で座標と 1 秒値の提供期間を確認すること。
- ピットビル・グランドスタンド・跨道橋の周辺では Fix が切れることがある。CLAS の再 Fix は仕様上 ≤60 s なので、その間はフロート解になる。
  PPK（前後両方向の処理）や推測航法で補う。
- 自前基地局を置く場合は、パドックで既知点を作る（GEONET 基準の静止 PPK で 1 時間程度）。無線リンクは国内の技術基準適合（技適）品に限る（**一般知識・要確認**）。

### 3.5 計測手順の要点

- **レバーアーム**: 車両座標（例: 前車軸中心を原点、x 前・y 左・z 上）で、アンテナ位相中心・カメラ光学中心（レンズ前面で近似）・ロガー（IMU）の位置を ±2 cm で測る。
  カメラのピッチ/ロールは傾斜計で、取付写真も残す。メタデータに記録する。
- **カメラ校正**: SmartyCam と同じ解像度・モードで、チェッカーボード（例: 9×6、25–30 mm 角）を 20–40 枚、画角の隅まで撮る。
  OpenCV の calibrateCamera（広角なら fisheye モデル）で f, cx, cy, 歪みを求める。再投影 RMS 0.5 px 未満が目安。
  さらに車両前方の既知位置（巻尺で測る）にボードを置いて solvePnP すれば、取付の外部パラメータも求まる。
  → 現在は 3 フレームから f=472.3 px と高さ（1.25 m を仮定）を同時に推定しており、焦点距離と俯角・高さが相関している。この相関を断てる。
- **GCP**: 縁石の端や白線の角を RTK ローバーで静止観測する（30–60 s、CLAS 静止 6 cm (95%)、NTRIP/PPK なら 2–3 cm）。
  点ごとに写真とメモを残す。コースウォーク時間の許可が必要。
  2020 年以降に縁石が変わっていなければ、既存データの基準（オルソ・CG）の検証にもそのまま使える。
- **時刻同期**: GNSS の PPS で LED を点滅させ、カメラの画角の隅に写す。多数の点滅とフレーム周期のずれから位相を推定すれば、ms 級の同期ができる（**提案。未検証**）。

---

## 4. 出典

- libxrk（m3rlin45/libxrk）0.12.0 のソース `aim_xrk.pyx` `_decode_gps` の説明（XRK GPS = ロガー時刻 + u-blox NAV-SOL）: https://github.com/m3rlin45/libxrk
- u-blox NAV-SOL のフラグ定義（diffSoln = 差分補正適用。NAV-SOL は後方互換のための旧メッセージ）: https://docs.ros.org/en/kinetic/api/ublox_msgs/html/msg/NavSOL.html
- MSAS の QZS-3 移行（2020 年 4 月）: https://gssc.esa.int/navipedia/index.php/MSAS_Space_Segment 、ICAO APAC IP: https://www.icao.int/sites/default/files/APAC/Meetings/2020/2020%20CNS%20SG24/4-Information%20Papers/IP15_JPN%20AI5.5%20-%20SBAS%20Status%20Update%20in%20Japan.pdf
- CLAS 概要（L6D、専用受信機、車両利用の注意）: https://qzss.go.jp/en/overview/services/sv06_clas.html
- CLAS 性能（移動体 H ≤12 cm / V ≤24 cm (95%)、TTFF ≤60 s）: https://sys.qzss.go.jp/serv_report/CLA/Service%20performance%20report_for_2ndH_FY2024_CLAS.pdf 、 https://home.csis.u-tokyo.ac.jp/~dinesh/GNSS_Train_files/202501/PresentationMaterials/07_GNSS%20Applications%20and%20QZSS%20High-Accuracy%20Services.pdf
- MADOCA-PPP（2024-04-01 運用開始、約 10 cm、収束 ≤30 分、無料）: https://www.unoosa.org/documents/pdf/psa/activities/2024/GNSS2024/GNSS2024_01_05.pdf 、 https://www.gpsworld.com/the-status-of-qzss/
- NEO-D9C / C101-D9C 手引き（補正の中継・変換はホスト側の責任）: https://content.u-blox.com/sites/default/files/C101-D9C-AppBoard_UserGuide_UBX-21009111.pdf
- ArduSimple CLAS L6 受信機キット（€99）: https://www.ardusimple.com/new-product-japan-clas-qzss-l6-correction-data-receiver-with-neo-d9c/
- ArduSimple 日本の補正サービス一覧（CLAS/MADOCA 無料、Skylark $69/$29、Galileo HAS 40 cm、基地局 €699）: https://www.ardusimple.com/rtk-correction-services-and-ntrip-casters-in-japan
- ZED-F9P Integration Manual（RTK 0.01 m + 1 ppm CEP、RTK 20 Hz）: https://cdn.sparkfun.com/assets/learn_tutorials/2/7/8/1/ZED-F9P_IntegrationManual_UBX-18010802.pdf
- ZED-F9R（IMU + 車輪パルス融合、最大 30 Hz）: https://www.sparkfun.com/sparkfun-gps-rtk-dead-reckoning-breakout-zed-f9r-qwiic-gps-22693.html
- 2 台の F9P による方位（基線約 1 m で 1° 未満、50 cm の実績）: https://docs.px4.io/main/en/gps_compass/u-blox_f9p_heading
- ichimill（3,300 か所以上、月額数千円、水平 1 cm 以内の検証）: https://www.softbank.jp/business/content/blog/202205/rtk-gnss-ichimill
- GEONET 電子基準点データ提供サービス terras: https://terras.gsi.go.jp/ 。御殿場（93072）等の局番号: https://www.jma.go.jp/jma/kishou/shingikai/ccpve/Report/117/kaiho_117_07.pdf
- Emlid Reach M2（$785、RINEX 20 Hz、PPK 100 km）: https://robotshop.com/products/emlid-reach-m2-multi-band-uav-rtk-gnss-module 、 PPK 手順: https://docs.emlid.com/emlid-studio/ppk-workflow
- VBOX 3i Dual Antenna RTK（100 Hz、2 cm）: https://www.eenewseurope.com/en/100-hz-dual-antenna-gps-system-delivers-2-cm-position-accuracy/ 、製品ページ: https://en.racelogic.support/VBOX_Automotive/Product_Info/VBOX_Data_Loggers/VBOX_3i_Product_Range/VBOX_3i_RTK
- VBOX Video HD2（内蔵 GNSS ±3 m 95% CEP）: https://racelogic.support/automotive/applications/mining/video-vbox-hd2/vbox-video-hd2-kit-technical-specification/
- AiM GPS09c（25 Hz、4 衛星系、0.5 m CEP、Pro は IMU）: https://aim-sportline.com/en/products/gps09c/index.htm
- AiM MXL2 キットは GPS08 同梱: https://www.aimshop.com/pages/aim-mxl2-car-racing-dash-logger 。GPS08（GPS+GLONASS、平均 1 m 未満は販売店の表記）: https://hhrperformance.com/i-31638917-aim-gps08-module-roof-mounted-2m.html
- RS3 の GPS オフセットについて（フォーラム、非公式）: https://forums.kartpulse.com/t/gps-and-racing-line-offset/13011
- カメラ校正（OpenCV、チェッカーボード）: https://learnopencv.com/camera-calibration-using-opencv/ 、再投影 RMS の目安: https://docs.omniverse.nvidia.com/kit/docs/omni.sensors.nv.camera/latest/intrinsic_calibration.html

## 5. 再現方法

環境変数 `FUJI_WET_XRK` / `FUJI_DRY_XRK`（`scripts/quality/apex_kpi/paths.py` と同じ規約）を設定して、このフォルダで実行する。
元媒体には書き込まない。

| スクリプト | 出力 | 内容 |
|---|---|---|
| `scan_tags.py` | `tags.json` | 兄弟ファイルのサイズ・SHA-256・マジック・レコードタグ・drk/bak 差分 |
| `decode_gnss_siblings.py` | `decode.json` | NSOL/PSOL の解読、XRK GPS との一致、gpk 列の同定 |
| `navsol_lap_quality.py` | `navsol_lap_quality.json` | 再生対象ラップごとの diffSoln・pAcc・衛星数・PDOP、7/30 L2 の区間比較 |
| `gps_timecode_check.py` | `gps_timecode_check.json` | GPS タイムコードと iTOW の関係、GNFI の有無 |

全セッション走査（他ドライバーのファイルを含む）は作業フォルダ側で実行した。リポジトリにはファイル名を残していない。

## 6. 未検証・制約

- 費用の「推定」は掲載価格の記憶・相場からの概算で、見積は取っていない。ArduSimple €99、Reach M2 $785/£649、Skylark $69/$29 は出典の掲載値（変わり得る）。
- ZED-F9P と NEO-D9C の組合せで CLAS を使う際のファームウェア/変換ソフトの条件は確認していない（ホスト側の中継・変換が必要という一般記述のみ確認）。
- 富士スピードウェイでのモバイル通信品質、GEONET 局までの距離、上空の遮蔽状況は現地で確認していない。
- diffSoln の補正源（SBAS/MSAS）は推定。pAcc は受信機の自己申告で、真の誤差ではない。
- RTK 等の精度はすべて出典の仕様値で、本プロジェクトの車両では実測していない。
