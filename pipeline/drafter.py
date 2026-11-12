"""④ 下書きの検証と保存。

下書き本文は Claude（チャット）が rules/threads_style.md に従って書き、
data/drafts_input/YYYYMMDD.json に保存する。ここでは機械的に守れるルールを検査する。
エラーが1つでもあれば保存しない。
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

from . import config
from .selector import numbers_in

URL = re.compile(r"https?://\S+")


def body_length(text: str) -> int:
    """本文の文字数（改行・空白・ハッシュタグ・URLを除く）。"""
    t = URL.sub("", text)
    t = re.sub(r"#\S+", "", t)
    t = re.sub(r"\s", "", t)
    return len(t)


def lint(draft: dict, cand: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    p1, p2 = draft.get("post1", ""), draft.get("post2", "")
    label = f"hotel {cand['hotel_no']} 型{draft.get('variant')}"

    if draft.get("variant") not in ("A", "B"):
        errors.append(f"{label}: variant は A か B")
    for name, text in (("1投稿目", p1), ("2投稿目", p2)):
        if not text.strip():
            errors.append(f"{label}: {name}が空")
        if "#PR" not in unicodedata.normalize("NFKC", text):
            errors.append(f"{label}: {name}に #PR がない")

    n = body_length(p1)
    if not (config.POST1_MIN_CHARS <= n <= config.POST1_MAX_CHARS):
        errors.append(f"{label}: 1投稿目の本文が{n}字（{config.POST1_MIN_CHARS}〜{config.POST1_MAX_CHARS}字にする）")
    elif not (85 <= n <= 115):
        warnings.append(f"{label}: 1投稿目の本文が{n}字（目安は100字前後）")

    if URL.search(p1):
        errors.append(f"{label}: 1投稿目にURLがある（リンクは2投稿目へ）")
    link = cand["hotel"].get("affiliate_url") or cand["hotel"].get("info_url")
    if link and link not in p2:
        errors.append(f"{label}: 2投稿目に施設URLがない: {link}")
    if not cand["hotel"].get("affiliate_url"):
        warnings.append(f"{label}: アフィリエイトURLが取得できていない（affiliateId を確認）")

    if not cand["urgency_allowed"]:
        for w in config.URGENCY_WORDS:
            if w in p1 or w in p2:
                errors.append(
                    f"{label}: 急かす表現「{w}」は獲得期限{config.URGENCY_MAX_DAYS}日以内のみ可"
                    f"（残り{cand['days_to_deadline']}日）")

    allowed = set(cand["allowed_numbers"])
    for name, text in (("1投稿目", p1), ("2投稿目", p2)):
        t = unicodedata.normalize("NFKC", URL.sub("", text))
        bad = sorted(numbers_in(t) - allowed)
        if bad:
            errors.append(f"{label}: {name}にデータにない数字 {bad}（データの値だけを使う）")

    if not draft.get("strength_evidence") and draft.get("variant") == "B":
        errors.append(f"{label}: 型Bは strength_evidence（強みの根拠）が必須")
    return errors, warnings


def load_inputs(run_date: str) -> list[dict]:
    path = config.DRAFT_INPUT / f"{run_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} がありません（Claude が下書きを書き出す場所）")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["drafts"] if isinstance(data, dict) else data


def load_candidates(run_date: str) -> dict:
    path = config.CANDIDATES / f"{run_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} がありません（先に python run.py prepare）")
    return json.loads(path.read_text(encoding="utf-8"))


def check_and_save(conn: sqlite3.Connection, run_date: str) -> dict:
    cands = {c["hotel_no"]: c for c in load_candidates(run_date)["candidates"]}
    drafts = load_inputs(run_date)
    errors, warnings, ok = [], [], []
    seen = set()
    for d in drafts:
        cand = cands.get(d.get("hotel_no"))
        if cand is None:
            errors.append(f"hotel_no {d.get('hotel_no')} は今日の候補にない")
            continue
        key = (d["hotel_no"], d.get("variant"))
        if key in seen:
            errors.append(f"hotel {key[0]} 型{key[1]} が重複")
        seen.add(key)
        e, w = lint(d, cand)
        errors += e
        warnings += w
        ok.append((d, cand, w))
    for hotel_no in cands:
        for v in ("A", "B"):
            if (hotel_no, v) not in seen:
                errors.append(f"hotel {hotel_no} の型{v}がない（候補ごとに2案）")
    if errors:
        return {"saved": 0, "errors": errors, "warnings": warnings}

    now = config.now().isoformat(timespec="seconds")
    for d, cand, w in ok:
        conn.execute(
            """INSERT INTO drafts (run_date, hotel_no, coupon_id, variant, post1, post2, criteria_json,
                   strength, strength_evidence, urgency_allowed, lint_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_date, hotel_no, variant) DO UPDATE SET
                   coupon_id=excluded.coupon_id, post1=excluded.post1, post2=excluded.post2,
                   criteria_json=excluded.criteria_json, strength=excluded.strength,
                   strength_evidence=excluded.strength_evidence,
                   urgency_allowed=excluded.urgency_allowed, lint_json=excluded.lint_json,
                   created_at=excluded.created_at""",
            (run_date, d["hotel_no"], cand["coupon"]["id"], d["variant"], d["post1"].strip(),
             d["post2"].strip(), json.dumps(cand["criteria"], ensure_ascii=False), d.get("strength"),
             d.get("strength_evidence"), int(cand["urgency_allowed"]),
             json.dumps(w, ensure_ascii=False), now),
        )
    conn.commit()
    return {"saved": len(ok), "errors": [], "warnings": warnings}
