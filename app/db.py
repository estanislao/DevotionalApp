"""Camada de banco de dados — um único arquivo SQLite."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

DB_PATH = Path(__file__).parent / "data" / "portal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    devotional_slug TEXT NOT NULL,
    step TEXT NOT NULL,              -- ouvir | ler | slides | quiz | praticar
    completed_at TEXT NOT NULL,
    UNIQUE(member_id, devotional_slug, step),
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    devotional_slug TEXT NOT NULL,
    question_id TEXT NOT NULL,
    chosen TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    answered_at TEXT NOT NULL,
    UNIQUE(member_id, devotional_slug, question_id),
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    devotional_slug TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(member_id, devotional_slug),
    FOREIGN KEY(member_id) REFERENCES members(id)
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed_members: list[dict] | None = None) -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    if seed_members:
        for m in seed_members:
            existing = conn.execute(
                "SELECT id FROM members WHERE name = ?", (m["name"],)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO members (name, pin, is_admin, created_at) VALUES (?, ?, ?, ?)",
                    (m["name"], m["pin"], int(m.get("is_admin", False)), datetime.utcnow().isoformat()),
                )
        conn.commit()
    conn.close()


@contextmanager
def session():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
