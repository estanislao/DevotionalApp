"""Camada de banco de dados — um único arquivo SQLite."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "data" / "portal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT,
    pin TEXT,                        -- legado (primeira versão logava por PIN)
    is_admin INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ativo',   -- pendente | ativo
    session_version INTEGER NOT NULL DEFAULT 0,   -- sobe a cada troca de senha e derruba as sessões antigas
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eventos (   -- contadores dos limites de uso (login, cadastro, recuperação)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eventos_chave_ts ON eventos(chave, ts);
CREATE INDEX IF NOT EXISTS idx_eventos_ts ON eventos(ts);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS pin_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    FOREIGN KEY(member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
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

# Colunas que entraram depois da primeira versão (migração leve, idempotente).
MIGRACOES_MEMBERS = {
    "email": "ALTER TABLE members ADD COLUMN email TEXT",
    "password_hash": "ALTER TABLE members ADD COLUMN password_hash TEXT",
    "status": "ALTER TABLE members ADD COLUMN status TEXT NOT NULL DEFAULT 'ativo'",
    "session_version": "ALTER TABLE members ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0",
}


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(admin: dict | None = None) -> None:
    """Cria tabelas, aplica migrações e garante a conta do administrador.

    admin = {"name", "email", "password_hash"}. O administrador só é CRIADO: se já existir membro com esse
    e-mail (ativo, pendente ou de qualquer perfil), nada é alterado. Isso impede que alguém se cadastre
    antes e vire admin com a própria senha quando o servidor reiniciar com aquele ADMIN_EMAIL.
    """
    conn = get_conn()
    conn.executescript(SCHEMA)
    colunas = {r["name"] for r in conn.execute("PRAGMA table_info(members)").fetchall()}
    for coluna, sql in MIGRACOES_MEMBERS.items():
        if coluna not in colunas:
            conn.execute(sql)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_members_email ON members(email) WHERE email IS NOT NULL")
    conn.commit()
    if admin:
        existing = conn.execute("SELECT id FROM members WHERE email = ?", (admin["email"],)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO members (name, email, password_hash, pin, is_admin, status, created_at) VALUES (?,?,?,'',1,'ativo',?)",
                (admin["name"], admin["email"], admin["password_hash"], agora()),
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
