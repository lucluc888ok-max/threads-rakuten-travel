"""③④ 「一言で言える強み」の抽出と根拠の記録。

口コミ本文は API から取得できないため、施設特色・アクセス・詳細情報の文字列と
項目別評価（口コミの集計値）から辞書ルールで判定する。根拠は必ず原文の抜粋を残す。
"""
from __future__ import annotations

import json
import re

RATING_THRESHOLD = 4.5
WALK_MAX_MIN = 5

# (ラベル, 正規表現) — テキスト項目から検出
TEXT_RULES: list[tuple[str, str]] = [
    ("駅近", r"駅直結|駅前|駅すぐ|駅から?\s*すぐ|駅改札.{0,4}すぐ"),
    ("朝食", r"朝食|ブレックファースト|モーニング"),
    ("大浴場", r"大浴場|天然温泉|温泉|露天風呂|サウナ"),
    ("眺望", r"夜景|眺望|スカイツリー|東京タワー|富士山|オーシャンビュー|ベイビュー|レインボーブリッジ"),
    ("新しさ", r"(?:20\d\d|令和\d+)年[^。]{0,10}(?:開業|オープン|OPEN|リニューアル|改装)|新築|グランドオープン"),
    ("駐車場無料", r"駐車場無料|無料駐車場|駐車場[^。]{0,6}無料"),
    ("アクセス", r"空港[^。]{0,8}(?:直行|リムジン|直結)|羽田|成田"),
]
NEGATION = re.compile(r"^.{0,8}(?:なし|無し|ありません|ございません|不可|休止|中止|終了)")
WALK = re.compile(r"徒歩\s*約?\s*(\d+)\s*分")
RATING_LABELS = {
    "mealAverage": "食事の評価",
    "bathAverage": "風呂の評価",
    "locationAverage": "立地の評価",
    "serviceAverage": "サービスの評価",
    "roomAverage": "部屋の評価",
    "equipmentAverage": "設備の評価",
}


def _excerpt(text: str, start: int, end: int, width: int = 30) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def extract(hotel_row) -> list[dict]:
    """hotels テーブルの行から強みリストを返す。各要素: label / evidence[{field, text}]"""
    h = dict(hotel_row)
    fields: dict[str, str] = {}
    for key in ("hotel_special", "access", "nearest_station"):
        if h.get(key):
            fields[key] = str(h[key])
    if h.get("facilities_json"):
        fields.update(json.loads(h["facilities_json"]))

    found: dict[str, list[dict]] = {}

    def add(label, field, text):
        ev = found.setdefault(label, [])
        if len(ev) < 3 and not any(e["text"] == text for e in ev):
            ev.append({"field": field, "text": text})

    # 駅からの徒歩分数（アクセス欄）
    for field in ("access", "nearest_station"):
        text = fields.get(field, "")
        for m in WALK.finditer(text):
            if int(m.group(1)) <= WALK_MAX_MIN:
                add("駅近", field, _excerpt(text, m.start(), m.end()))

    for label, pattern in TEXT_RULES:
        rx = re.compile(pattern)
        for field, text in fields.items():
            for m in rx.finditer(text):
                if NEGATION.match(text[m.end():]):
                    continue
                add(label, field, _excerpt(text, m.start(), m.end()))

    # 口コミの集計値
    ratings = json.loads(h["ratings_json"]) if h.get("ratings_json") else {}
    for key, name in RATING_LABELS.items():
        v = ratings.get(key)
        if isinstance(v, (int, float)) and v >= RATING_THRESHOLD:
            # 「食事の評価」は朝食とは限らないので別ラベルにする（推測で言い換えない）
            add(name, f"hotelRatingInfo.{key}", f"口コミ{name} {v}")
    avg = h.get("review_average")
    if isinstance(avg, (int, float)) and avg >= RATING_THRESHOLD:
        add("高評価", "reviewAverage", f"口コミ総合評価 {avg}（{h.get('review_count')}件）")

    return [{"label": k, "evidence": v} for k, v in found.items()]
