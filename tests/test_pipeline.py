"""通しテスト（楽天APIはモック）。 python -m unittest discover -s tests"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import config, db, drafter, ingest, matcher, record, render, selector, strengths  # noqa: E402
from pipeline.rakuten_api import RakutenClient, flatten_hotel  # noqa: E402

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=config.JST)
AFL = "https://hb.afl.rakuten.co.jp/hgc/xxxx/?pc=https%3A%2F%2Ftravel.rakuten.co.jp%2FHOTEL%2F{no}%2F"


def hotel_sections(no, name, address1, reviews, avg, access, special, meal=4.0, bath=4.0, extra=None):
    secs = [
        {"hotelBasicInfo": {
            "hotelNo": no, "hotelName": name, "address1": address1, "address2": "1-1-1",
            "reviewCount": reviews, "reviewAverage": avg, "hotelMinCharge": 8000,
            "access": access, "nearestStation": "新宿", "hotelSpecial": special,
            "hotelImageUrl": f"https://img.example/{no}.jpg", "hotelThumbnailUrl": f"https://img.example/{no}_s.jpg",
            "hotelInformationUrl": AFL.format(no=no),
        }},
        {"hotelRatingInfo": {"serviceAverage": 4.1, "locationAverage": 4.6, "roomAverage": 4.0,
                             "equipmentAverage": 4.0, "bathAverage": bath, "mealAverage": meal}},
    ]
    if extra:
        secs.append({"hotelFacilitiesInfo": extra})
    return {"hotel": secs}


HOTELS = {
    101: hotel_sections(101, "ホテル新宿サンプル", "東京都", 1200, 4.32, "JR新宿駅南口より徒歩3分",
                        "天然温泉の大浴場が自慢", extra={"hotelFacilities": [{"item": "大浴場"}]}),
    102: hotel_sections(102, "横浜ベイホテル", "神奈川県", 300, 4.1, "みなとみらい駅から徒歩12分",
                        "客室から夜景を一望"),
    103: hotel_sections(103, "千葉ビジネスイン", "千葉県", 100, 3.8, "千葉駅から徒歩15分", "シンプルな客室"),
    201: hotel_sections(201, "グランド上野ホテル 本館", "東京都", 800, 4.0, "上野駅から徒歩8分", ""),
    202: hotel_sections(202, "グランド上野ホテル 別館", "東京都", 600, 4.0, "上野駅から徒歩9分", ""),
}
KEYWORD = {
    "ホテル新宿サンプル": [101],
    "横浜ベイホテル": [102],
    "千葉ビジネスイン": [103],
    "グランド上野ホテル": [201, 202],
}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOpener:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        q = dict(urllib.parse.parse_qsl(url.query))
        self.calls.append((url.path, q))
        assert q["applicationId"] == "app" and q["affiliateId"] == "aff"
        if url.path.endswith("KeywordHotelSearch/20170426"):
            nos = KEYWORD.get(q["keyword"], [])
            body = {"hotels": [{"hotel": HOTELS[n]["hotel"][:1]} for n in nos]}
        else:
            body = {"hotels": [HOTELS[int(q["hotelNo"])]]}
        return FakeResp(json.dumps(body, ensure_ascii=False).encode())


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = {k: getattr(config, k) for k in
                      ("ROOT", "INBOX", "DATA", "EXTRACTED", "CANDIDATES", "DRAFT_INPUT", "DRAFTS", "DB_PATH", "DB_DUMP")}
        config.ROOT = self.tmp
        config.INBOX = self.tmp / "inbox"
        config.DATA = self.tmp / "data"
        config.EXTRACTED = config.DATA / "extracted"
        config.CANDIDATES = config.DATA / "candidates"
        config.DRAFT_INPUT = config.DATA / "drafts_input"
        config.DRAFTS = self.tmp / "drafts"
        config.DB_PATH = config.DATA / "app.db"
        config.DB_DUMP = config.DATA / "db_dump.sql"
        (config.INBOX / "done").mkdir(parents=True)
        self.conn = db.connect()
        self.opener = FakeOpener()
        self.client = RakutenClient(config.Settings(app_id="app", affiliate_id="aff"), opener=self.opener, interval=0)

    def tearDown(self):
        self.conn.close()
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp)

    def write_inbox(self):
        (config.INBOX / "shot1.png").write_bytes(b"png")
        coupons = [
            {"hotel_name": "ホテル新宿サンプル", "coupon_name": "冬の3,000円OFF", "discount_type": "yen",
             "discount_value": 3000, "min_amount": 20000, "acquire_deadline": "2026-11-16T23:59",
             "stay_start": "2026-11-12", "stay_end": "2026-12-25"},
            {"hotel_name": "横浜ベイホテル", "coupon_name": "10%OFF", "discount_type": "percent",
             "discount_value": 10, "min_amount": None, "acquire_deadline": "2026-11-30",
             "stay_start": "2026-11-12", "stay_end": "2026-12-25"},
            {"hotel_name": "千葉ビジネスイン", "coupon_name": "500円OFF", "discount_type": "yen",
             "discount_value": "500", "min_amount": "5,000", "acquire_deadline": "2026-11-30"},
            {"hotel_name": "期限切れホテル", "coupon_name": "5000円OFF", "discount_type": "yen",
             "discount_value": 5000, "acquire_deadline": "2026-11-01"},
            {"hotel_name": "グランド上野ホテル", "coupon_name": "1000円OFF", "discount_type": "yen",
             "discount_value": 1000, "acquire_deadline": "2026-11-30"},
        ]
        (config.INBOX / "shot1.png.coupons.json").write_text(
            json.dumps({"source_image": "shot1.png", "coupons": coupons}, ensure_ascii=False), encoding="utf-8")

    def prepare(self):
        self.write_inbox()
        self.assertEqual(ingest.pending_images(), [])
        stats = ingest.ingest_inbox(self.conn, "20261112")
        self.assertEqual(stats["inserted"], 5)
        self.assertTrue((config.INBOX / "done" / "shot1.png").exists())
        names = [r[0] for r in self.conn.execute("SELECT DISTINCT hotel_name_raw FROM coupons")]
        res = matcher.match_all(self.conn, self.client, names)
        return res, selector.select(self.conn, "20261112", now=NOW)

    def test_end_to_end(self):
        res, data = self.prepare()
        # 同名候補が2件 → 人の確認待ち、期限切れホテルは検索結果なし
        self.assertEqual([p["name"] for p in res["pending"]], ["グランド上野ホテル"])
        self.assertIn("期限切れホテル", res["not_found"])

        cands = {c["hotel_no"]: c for c in data["candidates"]}
        # 101: 口コミ1200・3000円で上位・強みあり / 102: %で上位・夜景 / 103: 条件1（立地の評価のみ）
        self.assertEqual(set(cands), {101, 102})
        self.assertTrue(cands[101]["urgency_allowed"])          # 残り4日
        self.assertFalse(cands[102]["urgency_allowed"])         # 残り18日
        labels = {s["label"] for s in cands[101]["strengths"]}
        self.assertTrue({"駅近", "大浴場"} <= labels)
        reasons = " ".join(e["reason"] for e in data["excluded"])
        self.assertIn("期限切れ", reasons)
        self.assertIn("条件 1/3", reasons)

        config.CANDIDATES.mkdir(parents=True)
        (config.CANDIDATES / "20261112.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        url101, url102 = AFL.format(no=101), AFL.format(no=102)
        p2 = "▼詳細・クーポン獲得はこちら（楽天トラベル）\n{}\n#PR"
        good = [
            {"hotel_no": 101, "variant": "A",
             "post1": "【3,000円OFF】新宿で温泉に入れるホテル、知ってた？\nホテル新宿サンプルのラ・クーポン、"
                      "20,000円以上の予約で使えます。宿泊は12/25まで対象。\n獲得期限は11/16まで、満室前にチェックを。\n#PR",
             "post2": p2.format(url101)},
            {"hotel_no": 101, "variant": "B",
             "post1": "新宿駅南口から徒歩3分で、天然温泉の大浴場つき。\nホテル新宿サンプルは口コミ1,200件で評価4.32。"
                      "仕事帰りにそのまま泊まれる距離感がいい。\nいまなら3,000円OFFのクーポンもあります。\n#PR",
             "post2": p2.format(url101), "strength": "駅近・大浴場",
             "strength_evidence": "access「JR新宿駅南口より徒歩3分」/ hotelSpecial「天然温泉の大浴場が自慢」"},
            {"hotel_no": 102, "variant": "A",
             "post1": "【10%OFF】横浜ベイホテルのラ・クーポンが出ています。\n利用条件なしで、宿泊は12/25まで対象。"
                      "みなとみらいで泊まるなら候補に入れておきたい一軒。\n獲得は11/30まで。\n#PR",
             "post2": p2.format(url102)},
            {"hotel_no": 102, "variant": "B",
             "post1": "客室から夜景を一望できるホテルを探しているなら、横浜ベイホテル。\nみなとみらい駅から歩ける立地で、"
                      "記念日の夜にもちょうどいい。\n10%OFFのクーポンも配布中です。\n#PR",
             "post2": p2.format(url102), "strength": "眺望", "strength_evidence": "hotelSpecial「客室から夜景を一望」"},
        ]
        config.DRAFT_INPUT.mkdir(parents=True)
        path = config.DRAFT_INPUT / "20261112.json"

        # ルール違反: 型A(102)に急かす表現、数字の捏造、#PR なし
        bad = json.loads(json.dumps(good, ensure_ascii=False))
        bad[2]["post1"] = bad[2]["post1"].replace("獲得は11/30まで。", "残りわずか、5,000円お得！")
        bad[3]["post1"] = bad[3]["post1"].replace("#PR", "")
        path.write_text(json.dumps({"drafts": bad}, ensure_ascii=False), encoding="utf-8")
        res = drafter.check_and_save(self.conn, "20261112")
        msg = "\n".join(res["errors"])
        self.assertEqual(res["saved"], 0)
        self.assertIn("残りわずか", msg)
        self.assertIn("5000", msg)
        self.assertIn("#PR がない", msg)

        path.write_text(json.dumps({"drafts": good}, ensure_ascii=False), encoding="utf-8")
        res = drafter.check_and_save(self.conn, "20261112")
        self.assertEqual(res["errors"], [], res)
        self.assertEqual(res["saved"], 4)

        md_path = render.write(self.conn, "20261112", data)
        md = (self.tmp / md_path).read_text(encoding="utf-8")
        self.assertIn("## ⚠ ホテル照合の確認待ち", md)
        self.assertIn("**1投稿目**", md)
        self.assertIn(url101, md)

        # 投稿を記録 → 14日以内は除外される
        draft_id = self.conn.execute("SELECT id FROM drafts WHERE hotel_no=101 AND variant='B'").fetchone()[0]
        post_id = record.record_post(self.conn, draft_id, "2026-11-12 09:00", None, None)
        record.update_stats(self.conn, post_id, 1500, 23, None, None)
        row = record.list_posts(self.conn)[0]
        self.assertEqual((row["views"], row["clicks"], row["adopted_variant"]), (1500, 23, "B"))
        again = selector.select(self.conn, "20261112", now=NOW)
        self.assertNotIn(101, {c["hotel_no"] for c in again["candidates"]})

        # 照合待ちを人が確定
        matcher.fetch_and_save_hotel(self.conn, self.client, 201)
        matcher.set_alias(self.conn, "グランド上野ホテル", 201, "human")
        left = [r[0] for r in self.conn.execute("SELECT alias_name FROM match_pending")]
        self.assertEqual(left, ["期限切れホテル"])  # 該当なしは resolve ... none で消すまで残る

        # ダンプから復元できる
        db.dump(self.conn)
        restored = db.connect(self.tmp / "restored.db", config.DB_DUMP)
        self.assertEqual(restored.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
        restored.close()

    def test_ingest_rejects_bad_json(self):
        (config.INBOX / "x.png.coupons.json").write_text(
            json.dumps({"coupons": [{"hotel_name": "A", "discount_type": "円", "discount_value": 1}]}),
            encoding="utf-8")
        with self.assertRaises(ingest.IngestError):
            ingest.ingest_inbox(self.conn, "20261112")
        self.assertTrue((config.INBOX / "x.png.coupons.json").exists())  # 失敗時は移動しない

    def test_pending_images(self):
        (config.INBOX / "a.png").write_bytes(b"")
        self.assertEqual([p.name for p in ingest.pending_images()], ["a.png"])

    def test_strength_negation(self):
        row = {"hotel_special": "大浴場はございません。朝食無料", "access": "駅から徒歩10分",
               "nearest_station": None, "facilities_json": None, "ratings_json": None,
               "review_average": 4.0, "review_count": 10}
        labels = {s["label"] for s in strengths.extract(row)}
        self.assertEqual(labels, {"朝食"})

    def test_flatten_and_normalize(self):
        h = flatten_hotel(HOTELS[101])
        self.assertEqual(h["hotelNo"], 101)
        self.assertIn("hotelRatingInfo", h["_sections"])
        self.assertEqual(matcher.similarity("ホテル新宿サンプル（東京）", "ホテル　新宿サンプル"), 1.0)

    def test_body_length(self):
        self.assertEqual(drafter.body_length("あいう\nえお #PR https://x.y/z"), 5)


if __name__ == "__main__":
    unittest.main()
