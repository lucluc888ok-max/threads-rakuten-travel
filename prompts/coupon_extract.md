# ラ・クーポン一覧スクショの読み取り手順（Claude 用）

楽天トラベル「ラ・クーポン」一覧（首都圏・割引額順）のスクショから、クーポン1枚ごとに下記を抽出する。

## 出力先
- `inbox/<画像ファイル名>` がある場合 → `inbox/<画像ファイル名>.coupons.json`
- チャットに直接貼られた画像の場合 → `inbox/chat_YYYYMMDD_HHMM.coupons.json`（`source_image` は `null`）

## 形式
```json
{
  "source_image": "IMG_0123.png",
  "coupons": [
    {
      "hotel_name": "ホテル名（画面の表記どおり）",
      "coupon_name": "クーポン名（画面の表記どおり）",
      "discount_type": "yen",
      "discount_value": 3000,
      "min_amount": 20000,
      "acquire_deadline": "2026-11-20T23:59",
      "stay_start": "2026-11-12",
      "stay_end": "2026-12-25",
      "note": ""
    }
  ]
}
```

| 項目 | ルール |
|---|---|
| hotel_name | 画面の表記をそのまま。略さない・補わない |
| discount_type | 「◯円OFF」→ `yen`、「◯%OFF」→ `percent` |
| discount_value | 数字のみ（3,000円OFF → `3000`） |
| min_amount | 「◯円以上の予約で利用可」の金額。条件なし・読めない → `null` |
| acquire_deadline | 獲得期限。時刻があれば `YYYY-MM-DDTHH:MM`、日付のみなら `YYYY-MM-DD` |
| stay_start / stay_end | 宿泊対象期間。読めない → `null` |
| note | 年を補った、一部が切れていた等の注記 |

## 守ること
- **画面に書かれていない値は入れない。** 読めない・切れている項目は `null` にして `note` に理由を書く
- 年が省略されている日付は、今日以降で最も近い年を補い、`note` に「年を補完」と書く
- 同じクーポンが2枚の画像にまたがって写っている場合は1件だけにする
- 書き出した後、抽出結果を表にしてユーザーに見せ、読み違いがないか確認してもらう
