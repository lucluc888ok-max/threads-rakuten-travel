"""SQLite アクセス。

クラウドのコンテナは作業後に破棄されるため、DB本体(data/app.db)は git に入れず、
書き込みのたびにテキストダンプ(data/db_dump.sql)を出力して git で永続化する。
app.db が無いときはダンプから復元する。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config


def connect(db_path: Path | None = None, dump_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or config.DB_PATH
    dump_path = dump_path or config.DB_DUMP
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not db_path.exists()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if fresh and dump_path.exists() and dump_path.stat().st_size > 0:
        # ダンプはテーブル名順に出力されるため、復元中は外部キー検査を切る
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(dump_path.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(config.SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def dump(conn: sqlite3.Connection, dump_path: Path | None = None) -> None:
    dump_path = dump_path or config.DB_DUMP
    conn.commit()
    dump_path.write_text("\n".join(conn.iterdump()) + "\n", encoding="utf-8")
