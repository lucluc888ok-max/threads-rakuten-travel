"""⑤ 投稿実績と数値の入力。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import config


def _parse_at(value: str | None) -> str:
    if not value:
        return config.now().isoformat(timespec="seconds")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=config.JST).isoformat(timespec="seconds")
        except ValueError:
            pass
    raise ValueError(f"日時の形式が不正です: {value!r}（例: 2026-11-12 21:00）")


def record_post(conn: sqlite3.Connection, draft_id: int, at: str | None, url: str | None,
                notes: str | None) -> int:
    d = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if d is None:
        raise ValueError(f"下書きID {draft_id} がありません")
    now = config.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO posts (draft_id, hotel_no, adopted_variant, posted_at, threads_url, notes, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (draft_id, d["hotel_no"], d["variant"], _parse_at(at), url, notes, now),
    )
    conn.commit()
    return cur.lastrowid


def update_stats(conn: sqlite3.Connection, post_id: int, views: int | None, clicks: int | None,
                 url: str | None, notes: str | None) -> None:
    if conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        raise ValueError(f"投稿ID {post_id} がありません")
    sets, args = [], []
    for col, v in (("views", views), ("clicks", clicks), ("threads_url", url), ("notes", notes)):
        if v is not None:
            sets.append(f"{col} = ?")
            args.append(v)
    if not sets:
        raise ValueError("更新する値がありません（--views / --clicks など）")
    sets.append("updated_at = ?")
    args += [config.now().isoformat(timespec="seconds"), post_id]
    conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()


def list_posts(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.id, p.posted_at, h.name, p.adopted_variant, p.views, p.clicks,
                  CASE WHEN p.views > 0 THEN ROUND(100.0 * p.clicks / p.views, 2) END AS ctr,
                  p.draft_id, p.threads_url
           FROM posts p JOIN hotels h ON h.hotel_no = p.hotel_no
           ORDER BY p.posted_at DESC LIMIT ?""", (limit,))]
