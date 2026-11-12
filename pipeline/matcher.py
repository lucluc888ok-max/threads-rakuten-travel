"""② ホテル照合: スクショ上のホテル名 → 楽天トラベル施設番号。

- 確定済みの対応（hotel_aliases）があればそれを使う
- 正規化した名前が候補の1件とだけ完全一致し、首都圏の住所なら自動確定
- それ以外は候補を match_pending に保存し、人に選んでもらう（`run.py resolve`）
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher

from . import config
from .rakuten_api import RakutenClient, section_strings

_BRACKETS = re.compile(r"[（(【\[〔「『].*?[）)】\]〕」』]")
_NOISE = re.compile(r"[\s・･\-‐－—―〜~～,，.。、/／'’&＆]")


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).lower()
    s = _BRACKETS.sub("", s)
    s = _NOISE.sub("", s)
    return s


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    if na in nb or nb in na:
        ratio = max(ratio, 0.85)
    return round(ratio, 3)


def _is_capital(hotel: dict) -> bool:
    addr = str(hotel.get("address1") or "")
    return addr.startswith(config.CAPITAL_PREFS)


def search_keywords(name: str) -> list[str]:
    """キーワード検索語。長い名前で0件になる場合に備えて括弧を外した版も試す。"""
    base = unicodedata.normalize("NFKC", name).strip()
    no_bracket = _BRACKETS.sub(" ", base).strip()
    out = []
    for k in (base, no_bracket):
        k = re.sub(r"\s+", " ", k)
        if len(k) >= 2 and k not in out:
            out.append(k)
    return out


def find_candidates(client: RakutenClient, name: str, limit: int = 5) -> list[dict]:
    seen, cands = set(), []
    for kw in search_keywords(name):
        for h in client.keyword_search(kw, hits=15):
            if h["hotelNo"] in seen:
                continue
            seen.add(h["hotelNo"])
            cands.append({
                "hotelNo": h["hotelNo"],
                "hotelName": h.get("hotelName", ""),
                "address": f"{h.get('address1') or ''}{h.get('address2') or ''}",
                "reviewCount": h.get("reviewCount"),
                "capital": _is_capital(h),
                "score": similarity(name, h.get("hotelName", "")),
            })
        if cands:
            break
    cands.sort(key=lambda c: (c["score"], c["capital"]), reverse=True)
    return cands[:limit]


def decide(name: str, cands: list[dict]) -> int | None:
    """自動確定できる場合のみ hotelNo を返す。"""
    exact = [c for c in cands if c["score"] == 1.0]
    if len(exact) == 1 and exact[0]["capital"]:
        return exact[0]["hotelNo"]
    return None


def save_hotel(conn: sqlite3.Connection, h: dict) -> None:
    sections = h.get("_sections") or {}
    rating = sections.get("hotelRatingInfo") or {}
    conn.execute(
        """INSERT INTO hotels (hotel_no, name, address, review_count, review_average, ratings_json,
               min_charge, access, nearest_station, hotel_special, facilities_json, image_url,
               thumbnail_url, info_url, affiliate_url, raw_json, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(hotel_no) DO UPDATE SET
               name=excluded.name, address=excluded.address, review_count=excluded.review_count,
               review_average=excluded.review_average, ratings_json=excluded.ratings_json,
               min_charge=excluded.min_charge, access=excluded.access,
               nearest_station=excluded.nearest_station, hotel_special=excluded.hotel_special,
               facilities_json=excluded.facilities_json, image_url=excluded.image_url,
               thumbnail_url=excluded.thumbnail_url, info_url=excluded.info_url,
               affiliate_url=excluded.affiliate_url, raw_json=excluded.raw_json,
               fetched_at=excluded.fetched_at""",
        (
            int(h["hotelNo"]), h.get("hotelName", ""),
            f"{h.get('address1') or ''}{h.get('address2') or ''}" or None,
            h.get("reviewCount"), h.get("reviewAverage"),
            json.dumps(rating, ensure_ascii=False) if rating else None,
            h.get("hotelMinCharge"), h.get("access"), h.get("nearestStation"), h.get("hotelSpecial"),
            json.dumps(section_strings(h), ensure_ascii=False),
            h.get("hotelImageUrl"), h.get("hotelThumbnailUrl"), h.get("hotelInformationUrl"),
            affiliate_url_of(h),
            json.dumps({k: v for k, v in h.items() if k != "_sections"} | {"_sections": sections},
                       ensure_ascii=False),
            config.now().isoformat(timespec="seconds"),
        ),
    )


def affiliate_url_of(h: dict) -> str | None:
    """affiliateId 付与時に返るアフィリエイトURL。afl ドメインでなければ None。"""
    for key in ("hotelAffiliateUrl", "hotelInformationUrl", "planListUrl"):
        url = h.get(key)
        if isinstance(url, str) and "afl.rakuten.co.jp" in url:
            return url
    return None


def set_alias(conn: sqlite3.Connection, name: str, hotel_no: int | None, by: str) -> None:
    conn.execute(
        """INSERT INTO hotel_aliases (alias_name, hotel_no, confirmed_by, confirmed_at)
           VALUES (?,?,?,?) ON CONFLICT(alias_name) DO UPDATE SET
           hotel_no=excluded.hotel_no, confirmed_by=excluded.confirmed_by,
           confirmed_at=excluded.confirmed_at""",
        (name, hotel_no, by, config.now().isoformat(timespec="seconds")),
    )
    conn.execute("DELETE FROM match_pending WHERE alias_name = ?", (name,))
    conn.execute("UPDATE coupons SET hotel_no = ? WHERE hotel_name_raw = ?", (hotel_no, name))


def match_all(conn: sqlite3.Connection, client: RakutenClient, names: list[str],
              refresh_hotels: bool = True) -> dict:
    """names を照合し、確定したホテルの詳細を取得・保存する。"""
    result = {"matched": [], "pending": [], "not_found": [], "skipped": []}
    for name in names:
        row = conn.execute("SELECT hotel_no FROM hotel_aliases WHERE alias_name = ?", (name,)).fetchone()
        if row is not None:
            if row["hotel_no"] is None:
                result["skipped"].append(name)
                continue
            hotel_no = row["hotel_no"]
            if refresh_hotels or not _hotel_exists(conn, hotel_no):
                fetch_and_save_hotel(conn, client, hotel_no)
            conn.execute("UPDATE coupons SET hotel_no = ? WHERE hotel_name_raw = ?", (hotel_no, name))
        else:
            cands = find_candidates(client, name)
            if not cands:
                conn.execute(
                    "INSERT OR REPLACE INTO match_pending VALUES (?,?,?)",
                    (name, "[]", config.now().isoformat(timespec="seconds")),
                )
                result["not_found"].append(name)
                continue
            hotel_no = decide(name, cands)
            if hotel_no is None:
                conn.execute(
                    "INSERT OR REPLACE INTO match_pending VALUES (?,?,?)",
                    (name, json.dumps(cands, ensure_ascii=False), config.now().isoformat(timespec="seconds")),
                )
                result["pending"].append({"name": name, "candidates": cands})
                continue
            fetch_and_save_hotel(conn, client, hotel_no)  # FK のため先にホテルを保存
            set_alias(conn, name, hotel_no, "auto")
        result["matched"].append((name, hotel_no))
    conn.commit()
    return result


def _hotel_exists(conn: sqlite3.Connection, hotel_no: int) -> bool:
    return conn.execute("SELECT 1 FROM hotels WHERE hotel_no = ?", (hotel_no,)).fetchone() is not None


def fetch_and_save_hotel(conn: sqlite3.Connection, client: RakutenClient, hotel_no: int) -> None:
    detail = client.hotel_detail(hotel_no)
    if not detail:
        raise RuntimeError(f"施設番号 {hotel_no} の詳細が取得できません")
    save_hotel(conn, detail)
