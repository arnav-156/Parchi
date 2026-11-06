"""SQLite storage: retailers, voice_notes, orders, order_lines, replies, events."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from parchi import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS retailers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, area TEXT, language TEXT NOT NULL DEFAULT 'hi-IN'
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, retailer_id TEXT NOT NULL REFERENCES retailers(id),
    status TEXT NOT NULL DEFAULT 'DRAFT',            -- DRAFT | APPROVED | EXPORTED
    created_at TEXT NOT NULL, approved_at TEXT, exported_at TEXT
);
CREATE TABLE IF NOT EXISTS voice_notes (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    kind TEXT NOT NULL DEFAULT 'order',               -- order | clarification
    line_id INTEGER REFERENCES order_lines(id),       -- for clarification replies
    filename TEXT, audio_path TEXT, audio_sha TEXT,
    status TEXT NOT NULL,                             -- PROCESSING | PARSED | STT_FAILED
    transcript TEXT, detected_lang TEXT, notes_json TEXT, error TEXT,
    api_calls INTEGER NOT NULL DEFAULT 0, latency_s REAL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_lines (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    note_id INTEGER REFERENCES voice_notes(id), line_no INTEGER NOT NULL,
    product_text TEXT, sku_id TEXT, qty REAL, unit TEXT,
    status TEXT NOT NULL,                             -- GREEN | AMBER | RED
    reasons TEXT NOT NULL DEFAULT '[]', score REAL, candidates TEXT NOT NULL DEFAULT '[]',
    span_start INTEGER, span_end INTEGER,             -- offsets into the note transcript
    removed INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0,
    resolved_by TEXT                                  -- clerk | clarification
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    kind TEXT NOT NULL,                               -- confirmation | clarification
    text TEXT NOT NULL, lang TEXT NOT NULL, lang_fallback INTEGER NOT NULL DEFAULT 0,
    ogg_path TEXT, wav_path TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, order_id INTEGER, kind TEXT NOT NULL, payload TEXT
);
"""
TABLES = {"retailers", "orders", "voice_notes", "order_lines", "replies", "events"}
JSON_COLS = {"reasons", "candidates", "notes_json", "payload"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT 1 FROM retailers LIMIT 1").fetchone():
        with open(config.RETAILERS_CSV, encoding="utf-8", newline="") as f:
            conn.executemany(
                "INSERT INTO retailers (id, name, area, language) VALUES (?, ?, ?, ?)",
                [(r["retailer_id"], r["name"], r["area"], r["language"]) for r in csv.DictReader(f)],
            )
    conn.commit()
    return conn


def _enc(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: json.dumps(v, ensure_ascii=False) if k in JSON_COLS and not isinstance(v, str) else v
            for k, v in fields.items()}


def insert(conn: sqlite3.Connection, table: str, **fields: Any) -> int:
    assert table in TABLES
    f = _enc(fields)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())
    )
    conn.commit()
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int, **fields: Any) -> None:
    assert table in TABLES and fields
    f = _enc(fields)
    conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in f)} WHERE id = ?", (*f.values(), row_id))
    conn.commit()


def row(conn: sqlite3.Connection, table: str, row_id: int | str) -> dict[str, Any] | None:
    assert table in TABLES
    r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return _dec(r) if r else None


def _dec(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k]:
            d[k] = json.loads(d[k])
    return d


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [_dec(r) for r in conn.execute(sql, params).fetchall()]


def log_event(conn: sqlite3.Connection, kind: str, order_id: int | None = None, **payload: Any) -> None:
    insert(conn, "events", ts=now(), order_id=order_id, kind=kind, payload=payload)


def order_lines(conn: sqlite3.Connection, order_id: int) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM order_lines WHERE order_id = ? ORDER BY line_no", (order_id,))


def unresolved_lines(conn: sqlite3.Connection, order_id: int) -> list[dict[str, Any]]:
    return [ln for ln in order_lines(conn, order_id) if not ln["removed"] and ln["status"] != "GREEN"]


def approve_order(conn: sqlite3.Connection, order_id: int) -> None:
    """Human click only. Refuses while any AMBER/RED line is neither resolved nor removed."""
    if bad := unresolved_lines(conn, order_id):
        raise ValueError(f"{len(bad)} line(s) still need review (AMBER/RED); resolve or remove them first.")
    update(conn, "orders", order_id, status="APPROVED", approved_at=now())
    log_event(conn, "order_approved", order_id)
