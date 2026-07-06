# P3b仕様: features3d.jsonのビューア描画(樹木/建物/バリア+クレジット表示)

作成: 2026-07-05 / 仕様: Fable 5 / 実装・テスト: Opus 4.8

## 目的

P3aで生成した `public/data/tracks/{barber,fuji}/features3d.json`
(スキーマは specs/p3a_features3d_extraction.md 参照。レビュー済み・座標検証済み)を
ビューアで描画する。あわせてデータ出典のクレジット表示(CC BY 4.0要件、
P1bからの持ち越し)を実装する。

規模感: Barber=樹木6,489/建物13/バリア72、富士=樹木18,100/建物91/バリア0。

## 成果物

1. 新規 `src/engine/track/Features3DBuilder.ts`(+vitest対象の純粋ロジックは
   分離してテスト)
2. ReplayScene/レイヤートグルへの統合(既存のキー7/8のトグル配線パターンに
   従い、**キー9**で3Dフィーチャ一括ON/OFF。既定ON)
3. クレジット表示UI(下記)
4. 検証レポート `specs/reports/p3b/REPORT.md`(日本語)

## 描画仕様

### 樹木(最重要・性能予算の主対象)
- **InstancedMeshで合計2〜3ドローコール以内**(幹=円錐台/円柱、樹冠=低ポリ
  コーン+球など。マージして1メッシュ1ドローでも可)
- 位置: (x, groundY, z)。**groundYは衛星地面と同じドレープ後の地面高**
  (TrackBuilderのcomputeDrapeInputs/corridorBlendHeight系の経路を再利用。
  生terrain.pngサンプルだけだとコリドー内で地面と食い違い浮く/沈むので不可)
- スケール: height(実測地上高)とcrownRadiusをインスタンス毎に反映
- バリエーション: Y回転と樹冠色の微変化(緑相のみ)。**乱数はインスタンス
  インデックス由来の決定的ハッシュ**(Math.random禁止。スクショ回帰を壊さない)
- ライティング: 既存のシーンライトに馴染むこと。castShadow=false
  (SSAOが効くので影なしでも接地感は出る。FPS予算優先)

### 建物
- footprintをTHREE.Shape→ExtrudeGeometryで押し出し。高さ=height、
  基部=footprint頂点位置のドレープ後地面高の最小値−0.5m(斜面で浮かない)
- マテリアル: ニュートラルな明灰色+建物毎の微妙な色調差(決定的ハッシュ)、
  フラットシェーディング。屋根と壁の色を僅かに変える(上面判定はノーマルY)
- 全建物まとめてジオメトリマージ→1〜2ドローコール

### バリア
- ポリラインに沿った薄い箱形リボン(type毎の高さは既にデータ内)。
  地面高に追従。guard_railは明灰、fenceは半透明気味の灰、wallはコンクリ色。
  まとめてマージ
- 富士はバリア0本(OSMデータ制約)。**プロシージャル生成はしない**
  (見た目が嘘になるので、将来のデータ改善で対応。REPORTに記録のみ)

### クレジット表示(CC BY 4.0要件)
- 画面隅(ミニマップと干渉しない位置)に小さな常時表示テキスト:
  - barber: `Terrain/Features: USGS 3DEP · Map data © OpenStreetMap contributors`
  - fuji: `Terrain/Features: VIRTUAL SHIZUOKA (静岡県, CC BY 4.0) · Map data © OpenStreetMap contributors`
- terrain_meta.json / features3d.json の `source` から組み立てる
  (トラック追加時にハードコード不要な形)。目立たないがマウスオーバーで
  全文表示(title属性程度でよい)

## 絶対に守る制約

- **features3d.jsonが無い/壊れている場合は完全にno-op**(既存features.jsonの
  グレースフルデグラデーションと同じ思想。ロード失敗でシーンを壊さない)
- 追加ドローコールは全部で**10以下**、フレーム時間増加は実測で
  perf_fps.mjsのFPS低下2以内
- 既存のレイヤートグル(キー7/8)の挙動を変えない
- ミニマップ・テレメトリ・ゴースト・全カメラモードの既存挙動を変えない
- TVカメラ/シネマティックカメラの視線が樹木で塞がれる問題が起きても
  カメラ位置の変更はしない(REPORTに記録、Fable 5が次を判断)
- データファイル(public/data/)への書き込み禁止(読むだけ)
- Math.random禁止(全て決定的シード)

## 検証(全6項目)

1. **ビジュアル**: 両トラックで、(a)トップビュー全景、(b)チェイスカメラで
   樹木近接、(c)富士グランドスタンド/Barber博物館が正しい場所に立つ、
   (d)キー9でON/OFF、のスクショを `specs/reports/p3b/` に保存。
   樹木が路面・ランオフに侵入していないこと(P3aで木はwoodポリゴン内100%
   確認済みなので、出たら描画側の座標バグ)
2. **接地**: 樹木・建物が地面から浮いていない/沈んでいないことを
   近接スクショで確認(特にコリドー境界付近と斜面)
3. **性能**: perf_fps.mjs before/after(FPS低下≤2)+ renderer.infoの
   ドローコール数をログ(≤10増)
4. **ユニットテスト**: 配置・ハッシュ・パース・フォールバックの純粋ロジックを
   vitestで(features3d.json欠如時のno-opテスト含む)
5. **ビルド**: `npm run test` 全通過、`npm run build` 成功
6. **クレジット表示**: 両トラックで正しい文言が出るスクショ

## レポート

`specs/reports/p3b/REPORT.md`: 実施内容、検証6項目、ドローコール/頂点数の
実測、判断保留事項(TVカメラ遮蔽の有無など)。
