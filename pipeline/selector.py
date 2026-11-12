"""③ 選定: 3条件中2つ以上を満たすホテルを候補にする。"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timedelta

from . import config, strengths

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_deadline(value: str | None) -> datetime | None:
    """獲得期限。日付だけなら当日 23:59 とみなす。"""
    if not value:
        return None
    if "T" in value:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=config.JST)
    return datetime.strptime(value, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=config.JST)


def numbers_in(text) -> set[str]:
    out = set()
    for m in NUM.finditer(str(text or "")):
        n = m.group().replace(",", "")
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))
    return out


def batch_date(conn: sqlite3.Connection, run_date: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(import_date) AS d FROM coupons WHERE import_date <= ?", (run_date,)
    ).fetchone()
    return row["d"] if row else None


def _top_thresholds(coupons: list[dict]) -> dict[str, float]:
    th = {}
    for dtype in ("yen", "percent"):
        vals = sorted((c["discount_value"] for c in coupons if c["discount_type"] == dtype), reverse=True)
        if vals:
            k = max(1, math.ceil(len(vals) * config.TOP_DISCOUNT_RATIO))
            th[dtype] = vals[k - 1]
    return th


def select(conn: sqlite3.Connection, run_date: str, now: datetime | None = None) -> dict:
    now = now or config.now()
    today = now.date()
    bdate = batch_date(conn, run_date)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM coupons WHERE import_date = ? ORDER BY id", (bdate,))] if bdate else []

    excluded: list[dict] = []
    active: list[dict] = []
    for c in rows:
        deadline = parse_deadline(c["acquire_deadline"])
        reason = None
        if deadline is None:
            reason = "獲得期限が読み取れていない"
        elif deadline < now:
            reason = f"獲得期限切れ（{c['acquire_deadline']}）"
        elif c["stay_end"] and c["stay_end"] < today.isoformat():
            reason = f"宿泊対象期間が終了（{c['stay_end']}）"
        elif c["hotel_no"] is None:
            reason = "ホテル照合が未確定"
        if reason:
            excluded.append({"hotel_name": c["hotel_name_raw"], "coupon_id": c["id"], "reason": reason})
            continue
        c["_deadline"] = deadline
        active.append(c)

    thresholds = _top_thresholds(active)
    for c in active:
        c["_top"] = c["discount_value"] >= thresholds.get(c["discount_type"], math.inf)
        max_v = max(x["discount_value"] for x in active if x["discount_type"] == c["discount_type"])
        c["_rel"] = c["discount_value"] / max_v

    by_hotel: dict[int, list[dict]] = {}
    for c in active:
        by_hotel.setdefault(c["hotel_no"], []).append(c)

    block_since = (now - timedelta(days=config.REPOST_BLOCK_DAYS)).isoformat(timespec="seconds")
    candidates = []
    for hotel_no, cs in by_hotel.items():
        hotel = conn.execute("SELECT * FROM hotels WHERE hotel_no = ?", (hotel_no,)).fetchone()
        name = hotel["name"] if hotel else cs[0]["hotel_name_raw"]
        recent = conn.execute(
            "SELECT posted_at FROM posts WHERE hotel_no = ? AND posted_at >= ? ORDER BY posted_at DESC",
            (hotel_no, block_since),
        ).fetchone()
        if recent:
            excluded.append({"hotel_name": name, "hotel_no": hotel_no,
                             "reason": f"{config.REPOST_BLOCK_DAYS}日以内に投稿済み（{recent['posted_at']}）"})
            continue
        if hotel is None:
            excluded.append({"hotel_name": name, "hotel_no": hotel_no, "reason": "施設情報が未取得"})
            continue
        coupon = max(cs, key=lambda c: (c["_top"], c["_rel"], -(c["min_amount"] or 0)))
        found = strengths.extract(hotel)
        criteria = {
            "reviews_500plus": (hotel["review_count"] or 0) >= config.MIN_REVIEW_COUNT,
            "top_discount": coupon["_top"],
            "has_strength": bool(found),
        }
        met = sum(criteria.values())
        if met < 2:
            excluded.append({"hotel_name": name, "hotel_no": hotel_no,
                             "reason": f"条件 {met}/3（{_criteria_text(criteria)}）"})
            continue
        candidates.append(_candidate(hotel, coupon, criteria, found, now))

    candidates.sort(key=lambda c: (c["criteria_met"], c["coupon"]["_rel"]), reverse=True)
    for c in candidates:
        c["coupon"].pop("_rel", None)
    return {"run_date": run_date, "batch_date": bdate, "generated_at": now.isoformat(timespec="seconds"),
            "thresholds": thresholds, "candidates": candidates, "excluded": excluded}


def _criteria_text(criteria: dict) -> str:
    names = {"reviews_500plus": "口コミ500件以上", "top_discount": "割引上位", "has_strength": "強みあり"}
    return " / ".join(f"{names[k]}{'○' if v else '×'}" for k, v in criteria.items())


def _candidate(hotel, coupon: dict, criteria: dict, found: list[dict], now: datetime) -> dict:
    h = dict(hotel)
    deadline: datetime = coupon["_deadline"]
    days_left = (deadline.date() - now.date()).days
    coupon_out = {k: v for k, v in coupon.items() if not k.startswith("_")}
    coupon_out["_rel"] = coupon["_rel"]
    coupon_out["is_top"] = coupon["_top"]

    facts_text = [
        h["name"], h.get("access"), h.get("nearest_station"), h.get("hotel_special"),
        h.get("review_count"), h.get("review_average"), h.get("min_charge"),
        coupon["coupon_name"], coupon["discount_value"], coupon["min_amount"],
        coupon["acquire_deadline"], coupon["stay_start"], coupon["stay_end"],
    ] + [e["text"] for s in found for e in s["evidence"]]
    allowed = set()
    for t in facts_text:
        allowed |= numbers_in(t)
    for d in (coupon["acquire_deadline"], coupon["stay_start"], coupon["stay_end"]):
        if d:  # 「11/20」「11月20日」のように月日を単独で書けるようにゼロ埋めなしも許可
            allowed |= {str(int(x)) for x in re.findall(r"\d+", d)}

    return {
        "hotel_no": h["hotel_no"],
        "hotel": {
            "name": h["name"], "address": h.get("address"),
            "review_count": h.get("review_count"), "review_average": h.get("review_average"),
            "ratings": json.loads(h["ratings_json"]) if h.get("ratings_json") else {},
            "min_charge": h.get("min_charge"), "min_charge_fetched_at": h.get("fetched_at"),
            "access": h.get("access"), "nearest_station": h.get("nearest_station"),
            "hotel_special": h.get("hotel_special"), "image_url": h.get("image_url"),
            "info_url": h.get("info_url"), "affiliate_url": h.get("affiliate_url"),
        },
        "coupon": coupon_out,
        "criteria": criteria,
        "criteria_met": sum(criteria.values()),
        "strengths": found,
        "days_to_deadline": days_left,
        "urgency_allowed": 0 <= days_left <= config.URGENCY_MAX_DAYS,
        "allowed_numbers": sorted(allowed, key=lambda x: (len(x), x)),
    }
