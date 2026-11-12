"""① クーポン取り込み。

スクショの読み取りは Claude（チャット）が prompts/coupon_extract.md に従って行い、
画像と同名の `<画像名>.coupons.json` を inbox/ に書き出す。
チャットに直接貼られた画像は `chat_<日時>.coupons.json` として書き出す。
このモジュールは JSON を検証して DB に入れ、処理済みファイルを inbox/done/ へ移す。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config

SIDECAR_SUFFIX = ".coupons.json"
REQUIRED = ("hotel_name", "discount_type", "discount_value")


class IngestError(ValueError):
    pass


def pending_images(inbox: Path | None = None) -> list[Path]:
    """まだ JSON 化されていない画像。"""
    inbox = inbox or config.INBOX
    out = []
    for p in sorted(inbox.iterdir()):
        if p.suffix.lower() in config.IMAGE_EXTS and not sidecar_for(p).exists():
            out.append(p)
    return out


def sidecar_for(image: Path) -> Path:
    return image.with_name(image.name + SIDECAR_SUFFIX)


def _parse_date(value, field: str) -> str | None:
    if value in (None, ""):
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise IngestError(f"{field} の日付形式が不正です: {s!r}（YYYY-MM-DD または YYYY-MM-DDTHH:MM）") from e


def _num(value, field: str, allow_none: bool = False):
    if value in (None, ""):
        if allow_none:
            return None
        raise IngestError(f"{field} が空です")
    if isinstance(value, (int, float)):
        return value
    s = re.sub(r"[,，円%％\s]", "", str(value))
    try:
        f = float(s)
    except ValueError as e:
        raise IngestError(f"{field} が数値ではありません: {value!r}") from e
    return int(f) if f.is_integer() else f


def validate_coupon(c: dict) -> dict:
    for k in REQUIRED:
        if c.get(k) in (None, ""):
            raise IngestError(f"必須項目 {k} がありません: {c}")
    dtype = c["discount_type"]
    if dtype not in ("yen", "percent"):
        raise IngestError(f"discount_type は yen / percent のどちらか: {dtype!r}")
    value = _num(c["discount_value"], "discount_value")
    if value <= 0 or (dtype == "percent" and value > 100):
        raise IngestError(f"discount_value が範囲外: {value}")
    deadline = _parse_date(c.get("acquire_deadline"), "acquire_deadline")
    stay_start = _parse_date(c.get("stay_start"), "stay_start")
    stay_end = _parse_date(c.get("stay_end"), "stay_end")
    return {
        "hotel_name": str(c["hotel_name"]).strip(),
        "coupon_name": (c.get("coupon_name") or "").strip() or None,
        "discount_type": dtype,
        "discount_value": value,
        "min_amount": _num(c.get("min_amount"), "min_amount", allow_none=True),
        "acquire_deadline": deadline,
        "stay_start": stay_start[:10] if stay_start else None,
        "stay_end": stay_end[:10] if stay_end else None,
        "extraction_note": (c.get("note") or "").strip() or None,
    }


def load_sidecar(path: Path) -> tuple[str | None, list[dict]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise IngestError(f"{path.name}: JSON として読めません: {e}") from e
    coupons = data.get("coupons")
    if not isinstance(coupons, list):
        raise IngestError(f"{path.name}: coupons 配列がありません")
    out = []
    for i, c in enumerate(coupons, 1):
        try:
            out.append(validate_coupon(c))
        except IngestError as e:
            raise IngestError(f"{path.name} の {i} 件目: {e}") from e
    return data.get("source_image"), out


def ingest_inbox(conn: sqlite3.Connection, run_date: str, inbox: Path | None = None) -> dict:
    """inbox/*.coupons.json を取り込み、画像と JSON を inbox/done/ へ移す。"""
    inbox = inbox or config.INBOX
    done_dir = inbox / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    config.EXTRACTED.mkdir(parents=True, exist_ok=True)
    stats = {"files": 0, "inserted": 0, "duplicates": 0}
    imported_at = config.now().isoformat(timespec="seconds")

    for sidecar in sorted(inbox.glob("*" + SIDECAR_SUFFIX)):
        source_image, coupons = load_sidecar(sidecar)  # 不正なら例外で止める（移動しない）
        for c in coupons:
            cur = conn.execute(
                """INSERT OR IGNORE INTO coupons
                   (import_date, source_image, hotel_name_raw, coupon_name, discount_type,
                    discount_value, min_amount, acquire_deadline, stay_start, stay_end,
                    extraction_note, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_date, source_image, c["hotel_name"], c["coupon_name"], c["discount_type"],
                 c["discount_value"], c["min_amount"], c["acquire_deadline"], c["stay_start"],
                 c["stay_end"], c["extraction_note"], imported_at),
            )
            if cur.rowcount:
                stats["inserted"] += 1
            else:
                stats["duplicates"] += 1
        conn.commit()
        shutil.copy2(sidecar, config.EXTRACTED / f"{run_date}_{sidecar.name}")
        if source_image:
            img = inbox / source_image
            if img.exists():
                shutil.move(str(img), str(done_dir / img.name))
        shutil.move(str(sidecar), str(done_dir / sidecar.name))
        stats["files"] += 1
    return stats
