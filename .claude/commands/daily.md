---
description: 日次処理（スクショ読み取り→照合→選定→下書き）を通す
---

楽天ラ・クーポンのスクショから Threads 下書きを作る日次処理を行う。自動投稿はしない。
今日の日付（JST, YYYYMMDD）を `D` とする。

1. **スクショ読み取り**
   - `python run.py status` で `inbox/` の未読み取り画像を確認する。チャットに貼られた画像も対象
   - 各画像を開き、`prompts/coupon_extract.md` に従って `.coupons.json` を書き出す
   - 抽出結果を表にして見せる。読み違いの指摘があれば直す

2. **取り込み・照合・選定**: `python run.py prepare`
   - 「照合待ち」が出たら、候補（施設番号・名前・住所・口コミ件数）を並べてユーザーに選んでもらう。
     **自分で決めない**。回答を受けて `python run.py resolve "名前" <施設番号|none>` → もう一度 `prepare`
   - API エラーで止まったら、エラーメッセージをそのまま伝える（README「楽天APIの設定」）

3. **下書き作成**
   - `rules/threads_style.md` を読み、`data/candidates/D.json` の候補ごとに型A・型Bを書く
   - 使ってよい事実は候補データの値だけ。書いたら `data/drafts_input/D.json` に保存

4. **検査と出力**: `python run.py finalize`
   - ✕ が出たら該当案を直して再実行。ルールを緩めて通さない

5. **報告**
   - `drafts/D.md` の場所、候補数、除外理由の要約、注意事項（アフィリエイトURL未取得など）を伝える
   - 変更をコミットして push する（`data/db_dump.sql` に DB の内容が入っている）
   - 投稿後は `python run.py record <下書きID>`、数値は `python run.py stats <投稿ID> --views N --clicks N` と案内する
