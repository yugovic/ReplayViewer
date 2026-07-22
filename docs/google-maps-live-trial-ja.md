# Google 航空写真を Replay の地面へ表示する

更新日: 2026-07-15

## 現在の実装

ReplayViewer は Google Maps JavaScript API の別パネルではなく、**Google Map Tiles API の 2D Satellite Tiles を Three.js の地面へ直接表示**する。

- レイヤーパネルの `G Google Ground`、またはキーボードの `G` で切り替える。
- Google セッションは、ユーザーが `G` を有効にした時だけ作成する。
- Google タイルは既存航空写真と同じ Web Mercator 座標で配置し、Replay の地形標高へ追従させる。
- 現在のカメラ視野に入ったタイルだけを最大6並列で取得する。
- 読み込み中は既存の静的航空写真を下地として残す。
- Google の帰属表示と viewport API が返す著作権表示を画面右下に表示する。
- 独自の事前取得、ファイル保存、永続キャッシュは行わない。
- `G` を無効にすると通信を中断し、メモリ・GPU 上の Google タイルも破棄する。

## Google Cloud の設定

### 1. API を有効にする

同じ Google Cloud プロジェクトで Billing を有効にし、**Map Tiles API** を有効にする。

今回の地面表示に必要なのは `Maps JavaScript API` ではなく `Map Tiles API`。以前の別パネル用に Maps JavaScript API だけを有効化していても、地面タイルのセッション作成は 403 になる。

### 2. API キーを制限する

Google Cloud Console の「API とサービス → 認証情報」で、使用するキーを次のように設定する。

- Application restrictions: **Websites (HTTP referrers)**
- API restrictions: **Map Tiles API**
- 開発用リファラー:

  ```text
  http://localhost:5173/*
  http://127.0.0.1:5173/*
  http://localhost:4174/*
  http://127.0.0.1:4174/*
  ```

本番では実際の HTTPS ドメインだけを追加する。ワイルドカードの無制限キーにはしない。

### 3. ReplayViewer を設定する

`.env.local`:

```dotenv
VITE_GOOGLE_MAPS_API_KEY=取得したAPIキー
VITE_GOOGLE_MAPS_TILE_ZOOM=19
```

変更後は Vite を再起動する。

```powershell
npm run dev
```

`VITE_` 変数はブラウザーへ配信されるため、キー自体を秘密として隠すことはできない。HTTP リファラー制限と API 制限で保護する。

## 403 の対処

次の表示が出る場合:

```text
Google Map Tiles API 403
Requests to this API ... BootstrapService.Bootstrap are blocked.
```

確認する項目:

1. Cloud プロジェクトで **Map Tiles API** が有効か。
2. キーの API restrictions に **Map Tiles API** が含まれているか。
3. 現在開いている localhost または本番 URL が HTTP リファラーに登録されているか。
4. Billing が有効か。
5. 設定変更後、数分待ってからページを再読み込みしたか。

この 403 は `https://tile.googleapis.com/v1/createSession` で発生するため、タイル座標や Three.js の貼り付け処理より前の認証・API設定エラーである。

## 解像度

既定値はズーム19。レイヤーパネルの `Google quality` から z18・z19・z20 を表示中にも切り替えられる。富士スピードウェイ付近（緯度約35.37度）での Web Mercator の理論地上解像度は次の通り。

| ズーム | 理論地上解像度 | 富士の既存 bbox 全体を表示した場合の最大タイル数 |
|---:|---:|---:|
| 17 | 約0.974 m/px | 64 |
| 18 | 約0.487 m/px | 225 |
| 19 | 約0.243 m/px | 900 |

これは配信ピクセルの地上寸法であり、元の航空写真が同じネイティブ解像度を持つ保証ではない。ズーム19へ上げても、地域によってはズーム18画像の拡大になる。

通常の Chase／Cockpit／TV 視点では視野内タイルだけを取得するため、表の最大数には達しにくい。Top 視点で全コースを表示すると最大数に近づく。

品質とコストのバランスからz19を既定とし、必要に応じて低コストなz18または車両周辺の比較用z20へ切り替える。

## 「1回表示」と課金単位

今回の Map Tiles API では、**1回表示＝1ロードではない**。

- 取得した 256×256 の 2D タイル1枚が、原則1タイルリクエスト。
- カメラ移動で新しい範囲が視野に入ると、その分のタイルリクエストが増える。
- 同じ `G` 表示中にロード済みのタイルはメモリ上で再利用する。
- `G` を切るとタイルを破棄するため、再度有効にした場合は再取得される。
- ズームを1段上げると、同じ地理範囲の最大タイル数はおおむね4倍になる。

## コスト目安

2026-07-15 時点の 2D Map Tiles の従量料金目安:

| 月間2Dタイルリクエスト | 単価 |
|---:|---:|
| 0〜100,000 | US$0 |
| 100,001〜1,000,000 | US$0.60 / 1,000 |
| 1,000,001〜5,000,000 | US$0.48 / 1,000 |

無料枠を使い切った後に、富士の現在の bbox 全体を初めて表示する単純計算:

| ズーム | 最大タイル数 | 概算 |
|---:|---:|---:|
| 18 | 225 | US$0.135 |
| 19 | 900 | US$0.54 |

実際の請求は月間合計、契約、通貨、税、Google の価格改定で変わる。Google Cloud の予算アラートと Map Tiles API のクォータ上限を必ず設定する。

## 利用上の制約

- Google タイルはアプリ内の可視化だけに使用する。
- タイルの事前取得、オフライン保存、永続キャッシュ、再配布を行わない。
- Google の帰属表示と viewport API が返した著作権表示を隠さない。
- Google 画像を特徴抽出、コース自動生成、機械判定の入力に使用しない。
- Google Maps Web サイトの非公式タイル URLを使用しない。

## 参考資料

- [Google Map Tiles API: 2D tiles overview](https://developers.google.com/maps/documentation/tile/2d-tiles-overview)
- [Google Map Tiles API: Satellite tiles](https://developers.google.com/maps/documentation/tile/satellite)
- [Google Map Tiles API: Session tokens](https://developers.google.com/maps/documentation/tile/session_tokens)
- [Google Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies?hl=ja)
- [Google Maps Platform pricing](https://developers.google.com/maps/billing-and-pricing/pricing)
- [航空写真品質のコース別調査](./aerial-imagery-quality-research-ja.md)
