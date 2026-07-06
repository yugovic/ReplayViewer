# P3b実施レポート: features3d.json のビューア描画(樹木/建物/バリア+クレジット)

実施: 2026-07-05 / 実装・検証: Opus 4.8 / 仕様: `specs/p3b_features3d_render.md`

## 概要

P3aで生成済みの `public/data/tracks/{barber,fuji}/features3d.json`(樹木/建物/
バリア)を、衛星地面と同じ**ドレープ後の地面高**に載せて描画する
`Features3DBuilder` を新規実装し、ReplaySceneへ統合した。あわせてCC BY 4.0/OSM
クレジット表示UIを追加。データファイル(public/data/)は読むだけで一切書き込んで
いない。terrain/track.json/pipeline/spike には触れていない。

規模実測(コンソールログ `[Features3D]`):
- Barber: 樹木6,489 / 建物13 / バリア72
- 富士: 樹木18,100 / 建物91 / バリア0

## 描画方式(要点)

- **樹木**: 幹=円柱(5角柱)+樹冠=正20面体(detail0)を**2つのInstancedMesh**で
  一括描画(=2ドローコールのみ、インスタンス数に依存しない)。位置は
  `(x, groundY, z)`。groundYは `createFeatureDrape().groundHeightAt`(TrackBuilderが
  衛星地面に使うのと同一のドレープ経路)から取得。スケールは実測 height / crownRadius を
  反映(幹半径は crownRadius と分離し太くなりすぎないよう clamp)。Y回転と樹冠色
  (**緑相のみ** HSL h=0.24–0.33)はインスタンスindex由来の決定的ハッシュ `hash01`
  (Math.random不使用)。castShadow=false。
- **建物**: footprintを `THREE.Shape → ExtrudeGeometry` で押し出し。基部=footprint各
  頂点のドレープ後地面高の**最小値−0.5m**(斜面で浮かない)。全建物を1つの
  BufferGeometryにマージ=**1ドローコール**。屋根(法線Y>0.5)と壁を僅かに色差、
  建物毎の微小tintは決定的ハッシュ。フラットシェーディング。
- **バリア**: ポリラインに沿う薄い箱形リボン(厚み0.14m、地面追従)。type別色。
  不透明系(guard_rail/wall)を1メッシュ、半透明のfenceを1メッシュにマージ=**最大2
  ドローコール**。**富士はバリア0本のまま**(プロシージャル生成はしていない。仕様通り)。
- **クレジット**: `terrain_meta.json` と `features3d.json` の `source` から
  `buildCreditLine()` が組み立てる(トラック名ハードコードなし)。画面右下に常時小さく
  表示、title属性で全文(両source)をホバー表示。ミニマップ(左下)と非干渉。

## no-op / グレースフルデグラデーション

`loadFeatures3d()` はfetch失敗・非OK・不正JSON・スキーマ不一致のいずれでもnullを返し、
3D層は完全にno-op(シーンを壊さない)。unitテストで全ケースを確認済み。

## 統合(既存キー7/8配線パターン踏襲)

store `showFeatures3d`(既定ON)→ ViewerCanvas keydown / useEffect → ReplayScene
`setFeatures3dVisible` → TrackBuildResult `setFeatures3dVisible`。LayersPanelに
「3D Features」ボタン追加。

## 検証(全6項目)

| # | 項目 | 結果 | 根拠 |
|---|------|------|------|
| 1 | ビジュアル | 合格 | 下記スクショ。樹木は全てwoodゾーン内、路面/ランオフ非侵入。key9でON/OFF切替。 |
| 2 | 接地 | 合格 | `barber_chase_on` で斜面上の樹木が接地(浮き/沈みなし)。両トップビューで樹冠が地形に追従。 |
| 3 | 性能(FPS低下≤2 / ドローコール≤10増) | 合格 | 追加ドローコール Barber=5 / 富士=3。FPS低下 DPR2: Barber −0.5 / 富士 −0.1、DPR1: Barber −2.0 / 富士 +0.7。 |
| 4 | ユニットテスト | 合格 | features3dBuilder 26件 + credits 5件を新規追加。合計176件通過。 |
| 5 | ビルド | 合格 | `npm run test` 176/176、`npm run build` 成功(tsc+vite)。 |
| 6 | クレジット表示 | 合格 | 両トラックで正しい文言(下記)。 |

### スクリーンショット(`specs/reports/p3b/`)

- `barber_top_on.png` / `barber_top_off.png` — トップ全景、key9のON/OFF比較
  (OFFで樹木・建物・バリアが全消し、OSMグラベル[key8]は残存=トグル独立)
- `barber_chase_on.png` — チェイスカメラ近接。斜面の低ポリ樹木が接地、右手にバリア(壁)と建物
- `barber_cockpit_on.png` — コックピット近接
- `fuji_top_on.png` / `fuji_top_off.png` — 18,100本の森が地形に沿って分布。
  **メインストレート北側にグランドスタンド/ピットの長い建物**が正しく配置
- `fuji_chase_on.png` / `fuji_cockpit_on.png` — チェイス/コックピット
  (キャプチャ時刻がメインストレート走行中で空が主体。富士の近接接地はトップビュー+
  共有ドレープ経路[検証済み]により担保)

### クレジット文言(実測)

- Barber 可視: `Terrain/Features: USGS 3DEP 1m AL_11County_B23 · Map data (c) OpenStreetMap contributors`
- 富士 可視: `Terrain/Features: VIRTUAL SHIZUOKA 2019 LP Ground (CC BY 4.0, 静岡県) · Map data (c) OpenStreetMap contributors`
- ホバー全文は terrain source と features3d source の両方を併記。

## ドローコール / 頂点数 実測

`[Features3D]` コンソールログ(ビルダー自己計測):

| トラック | 追加ドローコール | ユニーク頂点数 | 内訳 |
|---|---|---|---|
| Barber | 5 | 16,756 | 樹木2(instanced) + 建物1 + バリア2 |
| 富士 | 3 | 5,146 | 樹木2(instanced) + 建物1 |

- 「ユニーク頂点数」= GPUにアップロードする実頂点。樹木はInstancedMeshのため幹/樹冠の
  単位ジオメトリを全インスタンスで共有(6,489/18,100本でも頂点は単位ジオメトリ分のみ)。
- perf計測のフレーム毎GLドローcount差分は Barber +14 / 富士 +0。これはSSAOの
  深度/法線プリパスで同一メッシュが複数パス描画されるため。**シーンに追加した個別
  ドローコール数(=5/3)が仕様の「≤10増」の対象**で、いずれも充足。

## 判断保留事項(Fable 5の判断を仰ぐ)

1. **キー割り当ての衝突(要確認)**: 仕様は「キー9で3Dフィーチャ一括ON/OFF」と明示
   だが、実コードでは**キー9が既にDetail Texture、キー0がSatellite**に使用済みで、
   数字キー0–9が全て埋まっていた(memoryのキー7/8の記載より後に追加されたもの)。
   仕様の保護対象は明示的に「キー7/8」のみだったため、**キー9=3D Features(新規)**
   とし、Detail Textureのキーボードショートカットを**Dキー**へ退避した(LayersPanelの
   ボタン・トグル機能は完全保持、キーラベルのみ7/8/9/Dに変更)。キー7/8の挙動、
   カメラ1–6、Satellite 0、ミニマップ/テレメトリ/ゴーストは不変。別配置(例:3D Features
   を別キーにしDetail Textureを9に戻す)を希望する場合は容易に変更可能。

2. **TVカメラ/シネマティックの遮蔽**: 仕様通りカメラ位置は**一切変更していない**。
   樹木はwoodポリゴン内(ランオフ外側)に限定、TV/シネマカメラは走路コリドー内配置の
   ため遮蔽リスクは限定的。今回のキャプチャでは明確な遮蔽は観測されなかったが、森が
   走路に接近するコーナーでの全周スイープは未実施。レビューで顕在化すればFable 5が
   次を判断。

3. **クレジット可視文言の粒度**: 仕様の例示文言(`USGS 3DEP` / `VIRTUAL SHIZUOKA
   (静岡県, CC BY 4.0)`)は略記形だが、(a)データファイル編集禁止 (b)汎用的な略記変換は
   壊れやすくハードコードに近い、ため**terrain_meta.jsonのsource文字列を逐語表示**
   (CC BY 4.0/USGS/OSMの帰属情報は全て含む)する方針とした。より短い可視文言が
   必要なら `buildCreditLine` に整形ルールを追加可能。

4. **富士バリア0**: OSMデータ制約でバリア0本。仕様通りプロシージャル生成せず、将来の
   データ改善で対応(REPORT記録のみ)。

## 変更・作成ファイル

新規:
- `src/engine/track/Features3DBuilder.ts`(ビルダー本体+純粋ロジック)
- `src/engine/track/features3dBuilder.test.ts`(26 tests)
- `src/ui/credits.ts`(クレジット組み立て純粋関数)
- `src/ui/credits.test.ts`(5 tests)
- `src/ui/CreditOverlay.tsx`(クレジット表示コンポーネント)
- `specs/reports/p3b/REPORT.md` + スクショ8枚

変更:
- `src/engine/track/TrackBuilder.ts`(3Dフィーチャのロード/構築を共有ドレープに追加、
  `setFeatures3dVisible` 追加、statsログ)
- `src/engine/ReplayScene.ts`(`setFeatures3dVisible` 委譲)
- `src/engine/ViewerCanvas.tsx`(初期化・key9=3D Features・keyD=Detail Texture・sync effect)
- `src/state/replayStore.ts`(`showFeatures3d` state + toggle/set アクション)
- `src/ui/LayersPanel.tsx`(「3D Features」[9] 追加、Detail Texture を [D] に)
- `src/App.tsx`(`<CreditOverlay/>` マウント)
- `src/style.css`(`.hud-credit` スタイル)
