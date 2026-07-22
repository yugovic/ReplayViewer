# リポジトリ ファイル整理・リファクタリング計画（2026-07-17）

## 要旨

ルート直下に一回性スクリプト約37本・動画35MB・実験HTMLが散在し、一時フォルダが
3系統（`Temp/` `tmp/` `scratchpad/`）併存、`.bak` ファイルが66個、git未コミット
変更が87件という状態。対策を6フェーズに分けた:
**Phase 0（未コミット87件を論理単位でコミット＝安全網）→ Phase 1（ルートの
スクリプト類を `scripts/` へ集約）→ Phase 2（一時フォルダを `Temp/` に一本化し
gitignore）→ Phase 3（`.bak` 世代を `_archive/` へ退避）→ Phase 4（git管理方針の
確定：大容量データは原則ignore+由来メタデータのみ追跡）→ Phase 5（規約整備で
再発防止）**。Phase 0 完了までは移動・削除を一切行わないのが唯一の絶対条件。
所要目安は合計2〜3時間。

---

## 1. 現状診断（2026-07-17 実測）

### 1-1. ルート直下の散在物

| 分類 | 実体 | 件数/サイズ | git状態 |
|------|------|------------|---------|
| スナップショット取得スクリプト | `snap_*.mjs` ×19 | 約50KB | ignore済（`*.mjs`） |
| 調査・検証スクリプト | `probe*.mjs`, `check_offtrack.mjs`, `verify_ground_detail.mjs`, `demo_probe.mjs` | 6本 | ignore済 |
| 性能計測スクリプト | `perf_cpu.mjs`, `perf_frames.mjs`, `perf_profile.mjs` | 3本 | ignore済 |
| デモ動画生成 | `demo_video.mjs` | 1本 | ignore済 |
| AIMデータ変換（日付つき一回性） | `build_race_20200730.py`, `convert_aim_20200730.py`, `find_aim_files.py`, `inspect_aim.py` | 4本 | 未追跡 |
| 成果物動画 | `comparison_fuji_lap6_vs_cg.mp4`, `demo_fuji_3dreplay.mp4` | 35MB | 未追跡 |
| 実験HTML | `kn5scene.html`, `kn5test.html` | 2本 | 未追跡 |
| デバッグ画像 | `scan-debug.png` | 1本 | 未追跡 |

正当にルートに置くべきものは `index.html` / `package.json` 等の設定ファイル、
`README.md` / `CLAUDE.md` / `WORKLOG.md` / `BACKLOG.md` の4文書のみ。

### 1-2. 一時フォルダの3系統併存

| フォルダ | 中身 | 件数/サイズ |
|----------|------|------------|
| `Temp/` | 検証スクリーンショット、bisect画像、`sweep/` | 85件 / 56MB |
| `tmp/` | viteログ、`imagegen/` | 6件 / 2MB |
| `scratchpad/` | `anchor-audit/` ほか | 71件 / 11MB |

いずれも **gitignoreされていない**（現状は未追跡だが `git add -A` 事故で混入し得る）。

### 1-3. `.bak` ファイル 66個（node_modules/.venv除く）

- `public/data/tracks/fuji/`: `scene.native.bak.glb`（51MB）、`terrain.z15.bak.png`、
  `track.*-pre.bak.json` ×4、`features3d.p3c-pre.bak.json`、`*.bak-mirror` など
  ≒54MB。**コミット履歴が3件しかないため、これらが事実上の唯一の世代管理**。
- `pipeline/`: `build_race.py.bak`, `build_track_osm.py.bak(-mirror)`,
  `convert_aim.py.bak`, `fetch_terrain_tiles.py.bak` などスクリプトの手動バックアップ。

### 1-4. git の状態

- コミット3件のみ。変更87件（src/ 25ファイルのmodified + 大量の未追跡）。
- 追跡済みルートファイルは8個のみ。`public/data/` の436MBはほぼ未追跡で、
  追跡すべきか否かの方針が未決定。

### 1-5. 容量（参考・整理対象外の確認）

- `pipeline/` 9.2GB の内訳は `.venv-sr` 5.6GB + `cache/` 3.4GB（ともにignore済、
  C:圧迫回避のため意図的にD:へ置いたもの → 削除しない）。
- `dist/` 437MB はビルド成果物（ignore済、`npm run build` で再生成可 → 問題なし）。

---

## 2. 整理計画（フェーズ順に実施）

### Phase 0 — 安全網: 未コミット87件を先にコミットする 【最優先・移動作業の前提】

`.bak` が唯一の履歴である現状で先にファイルを動かすと、壊した時に戻れない。

1. `src/` `public/data/` などの modified/untracked を**論理単位で分割コミット**
   （例: ①src本体の機能変更 ②pipeline新スクリプト ③docs ④トラックデータ方針が
   決まるまでdataは保留、の順）。
2. `public/data/` の扱いは Phase 4 で決めるため、このフェーズでは
   **コード＋docs＋設定のみ**コミットし、データはコミットしない。
3. 検証: コミット後 `npm run build`（または `npx tsc --noEmit`）＋
   `npx vitest run` が通ることを確認。

### Phase 1 — ルート直下のスクリプト類を `scripts/` へ集約

```
scripts/
  snap/      ← snap_*.mjs（19本）
  probe/     ← probe*.mjs, check_offtrack.mjs, verify_ground_detail.mjs, demo_probe.mjs
  perf/      ← perf_*.mjs（3本）
  demo/      ← demo_video.mjs
  aim/       ← build_race_20200730.py, convert_aim_20200730.py, find_aim_files.py, inspect_aim.py
  experiments/ ← kn5scene.html, kn5test.html
```

- **移動前に参照チェック必須**: `rg "snap_|probe|perf_|verify_ground" docs/ WORKLOG.md BACKLOG.md package.json`
  でコマンド記載箇所を洗い出し、移動後にパスを更新する
  （特に `docs/guide-circuit-imagery-playbook-2026-07-17.md` は手順書としてコマンドを
  記載しているため要更新）。
- `.gitignore` の全域 `*.mjs` を廃止し、`scripts/` 配下は**追跡する**方針に変更
  することを推奨（再利用する検証・計測スクリプトは資産。使い捨ては最初から
  `Temp/` に書く運用へ）。追跡したくない場合は `scripts/**/tmp_*.mjs` のような
  限定ignoreにする。
- ルートの `.py` 4本は `pipeline/build_race.py` / `convert_aim.py` の日付固定版。
  中身を比較し、pipeline版に吸収済みなら `scripts/aim/` ではなく Phase 3 の
  `_archive/` 行きでもよい（実行ログがWORKLOGに残っているため復元可能性は低リスク）。
- 動画2本（35MB）はコード資産ではないので `Temp/media/` へ移動
  （成果物として保存したいなら リポジトリ外 `D:\00_Dev\_deliverables\ReplayViewer\` を
  新設して退避）。`scan-debug.png` は `Temp/` へ。

### Phase 2 — 一時フォルダを `Temp/` に一本化

1. 統合先は既に最大の `Temp/` とする（`Temp/logs/` ← tmp のログ、
   `Temp/imagegen/` ← tmp/imagegen、`Temp/anchor-audit/` ← scratchpad の中身）。
2. **移動前に参照チェック**: `rg "tmp/|scratchpad/" src/ pipeline/ scripts/ docs/`
   （viteログ出力先やスクリプトの出力パスが `tmp/` を指していれば書き換え）。
3. `.gitignore` に追記:
   ```
   Temp/
   tmp/
   scratchpad/
   _archive/
   ```
   （`tmp/` `scratchpad/` は空にして削除するが、再発防止でignoreも残す）
4. 運用ルール: 「一時ファイル・スクショ・使い捨てスクリプトは必ず `Temp/` 直下
   またはサブフォルダへ」を CLAUDE.md に明文化（Phase 5）。

### Phase 3 — `.bak` 世代を `_archive/` へ退避

Phase 0 のコミット完了後に実施（git履歴ができた時点で `.bak` の保険価値が下がる）。

1. リポジトリ直下に `_archive/`（gitignore済）を作り、日付フォルダで退避:
   ```
   _archive/2026-07-17/
     fuji/   ← public/data/tracks/fuji/*.bak*, *.bak-mirror（≒54MB）
     pipeline/ ← pipeline/*.bak, *.bak-mirror
   ```
2. **削除ではなく移動**にする理由: 大容量バイナリ（scene.native.bak.glb等）は
   git履歴に入れないため、`.bak` が本当に最後の砦。ビューア表示・pipeline再生成の
   動作確認が済んだ将来時点（目安1ヶ月後）で `_archive/` ごと削除判断。
3. 退避後の検証: `npm run dev` でビューアが正常表示されること、
   `pipeline/test_grade_ortho.py` `pipeline/test_enhance_track_corridor.py` が
   通ること。
4. 以後の運用: **`.bak` を新規に作らない**（コードはgit、データは pipeline で
   再生成可能にするのが原則。実験前のデータ退避が要る場合のみ `_archive/` へ）。

### Phase 4 — git 管理方針の確定（`public/data/` 436MB）

推奨方針:

| 対象 | 方針 | 理由 |
|------|------|------|
| `public/data/tracks/*/` の JSON（track.json, features.json, overlays.json 等） | **追跡する** | 小さく、手修正が入る本質データ |
| glb / jpg / png の大容量バイナリ | **ignore + 生成手順をdocsに担保** | pipelineで再生成可能。playbookに手順あり |
| `*_meta.json`（衛星画像の出所メタ） | **追跡する** | 権利チェーン台帳（PROVENANCE）の証拠。出口戦略docの中核要件 |
| `public/data/races/*/` | レースJSONは追跡、元AIMデータはignore | 変換結果は成果物、元データは別管理 |

- `.gitignore` に `public/data/tracks/*/*.glb` `public/data/tracks/*/*.jpg` 等を追記。
- 将来チーム共有や売却パッケージングが必要になったら Git LFS を検討
  （今は単独開発なので不要）。

### Phase 5 — 再発防止の規約整備

CLAUDE.md（プロジェクト側）に以下を追記:

- 一時ファイル・スクショ・使い捨てスクリプトは `Temp/` へ。ルート直下に置かない
- 再利用するスクリプトは `scripts/<分類>/` へ置き、git追跡する
- `.bak` 禁止。コードはgitコミット、データ退避は `_archive/YYYY-MM-DD/` へ
- 成果物動画・納品物はリポジトリ外へ

docs/README.md の「プロジェクト全体のドキュメント地図」に `scripts/` `Temp/`
`_archive/` の役割1行ずつを追加。

---

## 3. 実施順序と所要目安

| 順 | フェーズ | 目安 | リスク |
|----|---------|------|--------|
| 1 | Phase 0 コミット | 30-45分 | 低（追加のみ） |
| 2 | Phase 2 一時フォルダ統合 + gitignore | 15分 | 低（参照チェックのみ注意） |
| 3 | Phase 1 scripts/ 集約 | 30-45分 | 中（docs内コマンドパスの更新漏れ） |
| 4 | Phase 3 .bak 退避 | 15分 | 中（退避後の動作検証必須） |
| 5 | Phase 4 データ管理方針 + コミット | 30分 | 低 |
| 6 | Phase 5 規約追記 | 10分 | なし |

**やらないこと**: `pipeline/.venv-sr`（5.6GB）と `pipeline/cache/`（3.4GB）の削除
（C:圧迫回避のための意図的配置＋SR再実行コスト大）、`src/` 内部のコード
リファクタリング（本計画はファイル配置のみが対象。コード側は別途
/simplify・/code-review で扱う）、`track-creator/` `tracktools-kit/` の内部整理
（別プロジェクト扱いのため要望があれば別計画）。

---

## 4. 未決事項（実施前に確認したい点）

1. `scripts/` 配下の .mjs を git追跡に切り替えるか（推奨: 追跡する）
2. 動画2本の退避先: `Temp/media/`（リポジトリ内・ignore）か、リポジトリ外の
   納品物フォルダか
3. ルートの日付つき .py 4本: `scripts/aim/` に残すか `_archive/` 送りか
   （pipeline版との差分次第）
