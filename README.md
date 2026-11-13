# threads-rakuten-travel

首都圏の割引ホテル（楽天トラベル「ラ・クーポン」）を選定し、Threads 投稿の下書きを作るまでを半自動化するパイプライン。
**自動投稿はしない。** 投稿・リンクの動作確認は人が手動で行う。

> **現在の運用（2026-11〜）**: マニュアルをベースに人が作業し、Claude が補助する段階（フェーズ1）。
> 進め方・今やることは **[PLAN.md](PLAN.md)** を参照。下の自動化パイプライン（`/daily`）はフェーズ3で
> 実際の画面に合わせて作り直してから使う。

```
スクショ ─(Claude が読み取り)→ ①取り込み → ②楽天APIで照合 → ③選定 → ④下書き(Claude が執筆) → 自動検査 → drafts/YYYYMMDD.md
                                                                                           └→ SQLite（hotels / coupons / drafts / posts）
```

| 役割 | 担当 |
|---|---|
| スクショ読み取り・下書き本文の執筆 | Claude（このリポジトリを開いたチャット） |
| 照合・選定・ルール検査・記録・出力 | `python run.py`（Python 標準ライブラリのみ、追加インストール不要） |
| 投稿・リンク確認・数値入力 | 人 |

## 日次の運用手順

### 1. スクショを撮る
楽天トラベルのラ・クーポン一覧を **首都圏・割引額順** で表示し、スクショを撮る。

### 2. 実行する
チャットにスクショを貼り（または `inbox/` に置き）、次のように送る。

```
/daily
```

Claude が次の順に進める（中身は `.claude/commands/daily.md`）。

1. スクショを読み取り、抽出結果の表を見せる → **読み違いがないか確認**
2. `python run.py prepare` で取り込み・照合・選定
   - ホテル名の照合に自信がないときは候補が並ぶ → **番号を選んで返信**（選んだ結果は保存され、次回から聞かれない）
3. 候補ごとに 型A（割引訴求）・型B（特徴訴求）を執筆
4. `python run.py finalize` で検査し、`drafts/YYYYMMDD.md` を出力
5. DB の変更をコミット・push

### 3. コピペで投稿する
`drafts/YYYYMMDD.md` から採用する案を選ぶ。

1. Threads で **1投稿目** を投稿
2. その投稿へのスレッド返信として **2投稿目**（リンク）を投稿
3. **リンクをタップして**、楽天トラベルの該当ホテルが開くこと・クーポンが表示されることを確認

### 4. 記録する
投稿したら、下書きIDを記録する（下書きIDは md の各案の見出しにある）。

```bash
python run.py record 12 --at "2026-11-12 21:00" --url https://www.threads.net/...
```

数日後、閲覧数・クリック数を入力する（Threads のインサイト、楽天アフィリエイトのレポートを見る）。

```bash
python run.py stats 5 --views 1500 --clicks 23
python run.py posts        # 一覧（CTR付き）
```

チャットで「投稿ID 5 の閲覧1500、クリック23を記録して」と頼んでもよい。

## 選定ルール

次の3条件のうち **2つ以上** を満たすホテルが候補になる（しきい値は `pipeline/config.py`）。

1. 口コミ 500 件以上
2. 割引額または割引率が、その日の取り込み分で上位（円・％それぞれ上位30%、最低1件）
3. 一言で言える強みがある（駅近・朝食・大浴場・眺望など。施設特色・アクセス・設備情報・項目別評価から抽出し、根拠の原文を残す）

除外：獲得期限切れ／宿泊対象期間の終了／獲得期限が読み取れていない／照合未確定／**過去14日以内に投稿済み**。

## 下書きルール（`rules/threads_style.md`）

`finalize` が機械的に検査し、違反があれば保存しない。

- 1投稿目は本文100字前後（70〜130字で合格）、URLは入れない。リンクは2投稿目に分ける
- 1投稿目・2投稿目の両方に `#PR`
- 「満室前に」「残りわずか」等の急かす表現は **獲得期限まで7日以内** のときのみ
- 本文中の数字は、すべて候補データ（クーポン・施設情報）にある値であること
- 型Bは強みの根拠メモが必須

## 初期設定

### 楽天ウェブサービスのアプリ
1. https://webservice.rakuten.co.jp/app/list でアプリを登録し、**アプリID**（UUID）と **アクセスキー**（`pk_` で始まる）を発行する
2. アフィリエイトIDを確認する（楽天アフィリエイト）
3. **IPアドレス制限は掛けない**。クラウド環境は接続元IPが固定でないため、制限があると `CLIENT_IP_NOT_ALLOWED` で失敗する

### クラウドのチャットで使う場合（現在の運用）
チャットのタイトルバーの環境メニュー → 環境「threads-rakuten-travel」→ 編集。

| 設定場所 | 名前 | 値 |
|---|---|---|
| ネットワークシークレット（API credentials） | ホスト `openapi.rakuten.co.jp`、ヘッダー名 `accessKey`、接頭辞なし | アクセスキー |
| 環境変数 | `RAKUTEN_APP_ID` | アプリID |
| 環境変数 | `RAKUTEN_AFFILIATE_ID` | アフィリエイトID |

- アクセスキーはチャットに貼らないこと。ネットワークシークレットに入れると、通信時に自動で付与される
- ネットワークシークレットで `accessKey` というヘッダー名が使えない場合は、環境変数 `RAKUTEN_ACCESS_KEY` に入れる
- 新しいセッションから反映される
- ネットワーク許可には `openapi.rakuten.co.jp`（API）と `webservice.rakuten.co.jp`（ドキュメント）を登録済み

### 自分のPCで使う場合
```bash
cp .env.example .env   # 3つの値を記入（.env は .gitignore 済み）
python run.py status
```

## データの保存場所

| パス | 内容 | git |
|---|---|---|
| `data/db_dump.sql` | SQLite の全内容（テキスト）。書き込みのたびに更新 | **入れる** |
| `data/app.db` | 作業用DB。無ければダンプから自動復元 | 入れない |
| `data/candidates/` `data/drafts_input/` `data/extracted/` | 中間ファイル | 入れない |
| `drafts/YYYYMMDD.md` | 当日の一覧（DB の drafts テーブルにも保存） | 入れない |
| `inbox/` `inbox/done/` | スクショと抽出JSON | 入れない |
| `.env` | 認証情報 | 入れない |

クラウドのコンテナは作業後に破棄されるので、**投稿履歴（14日除外の判定に使う）は `data/db_dump.sql` をコミットして残す。**
作業ブランチを main にマージしておくと、新しいセッションでも履歴が引き継がれる。

### テーブル
- `hotels` 施設情報（口コミ件数・評価・項目別評価・最安料金・アクセス・施設特色・設備・画像URL・アフィリエイトURL）
- `hotel_aliases` スクショ上の表記 → 施設番号（照合結果の記憶）／ `match_pending` 照合待ち
- `coupons` クーポン（ホテル名・クーポン名・割引種別・割引値・利用条件額・獲得期限・宿泊対象期間）
- `drafts` 下書き（型A/B、1投稿目・2投稿目、選定条件、強みと根拠）
- `posts` 投稿実績（投稿日時・採用案・閲覧数・クリック数）

## 楽天APIについて（要確認事項）

- 接続先: `https://openapi.rakuten.co.jp/engine/api/Travel/{KeywordHotelSearch,HotelDetailSearch}/20170426`
- `applicationId` はクエリ必須、`accessKey` はクエリかヘッダー、`affiliateId` を付けるとアフィリエイトURLが返る想定

**公式ドキュメント（webservice.rakuten.co.jp）はクラウド環境から 503 で閲覧できず、未確認。**
上記は次の2点から組み立てている。

- 2026-11-12 時点の実APIへの疎通確認（エラーメッセージで accessKey の渡し方を確認）
- 非公式パッケージ `rakuten-mcp` 1.3.0 の実装

公式ドキュメントで変更を確認したら、`pipeline/config.py` の `DEFAULT_API_BASE`・各バージョン（または環境変数 `RAKUTEN_API_BASE`）を更新する。
アフィリエイトURLが返らない場合、md に「⚠ アフィリエイトURLが取得できていません」と出る。

## 開発

```bash
python -m unittest discover -s tests   # 楽天APIはモック
```

```
run.py                 入口（サブコマンド）
pipeline/
  config.py            設定・しきい値・.env 読み込み
  schema.sql / db.py   SQLite（ダンプで永続化）
  ingest.py            ①クーポンJSONの検証・取り込み・inbox/done へ移動
  rakuten_api.py       ②楽天トラベルAPI
  matcher.py           ②ホテル名照合（自信がなければ候補を提示）
  strengths.py         ③強みの抽出と根拠
  selector.py          ③選定・除外
  drafter.py           ④下書きのルール検査・保存
  render.py            drafts/YYYYMMDD.md 出力
  record.py            ⑤投稿実績・数値入力
prompts/coupon_extract.md   スクショ読み取り手順（Claude 用）
rules/threads_style.md      投稿文ルール（一部 ai-shacho-system から出典付きで転記）
.claude/commands/daily.md   /daily コマンド
```
