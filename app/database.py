"""SQLite database layer.

Schema highlights (maps to the manager's notes):
  * cities            - the City Master table (id, name, ...)
  * tags / city_tags  - N:N relation between cities and tags
  * city_events       - filled by a DB TRIGGER whenever a new city is inserted
                        (the "new city trigger" that feeds the Email Agent)
  * audit_log         - the admin table, filled by triggers on insert/update/delete
  * email_outbox      - emails produced by the Email Agent
  * kb_documents      - Travel knowledge base used by the RAG agent
  * sessions / session_messages - memory for the *stateful* chat mode
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
    country              TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    region               TEXT NOT NULL DEFAULT '',
    description          TEXT NOT NULL DEFAULT '',
    best_season          TEXT NOT NULL DEFAULT '',
    avg_daily_budget_usd REAL CHECK (avg_daily_budget_usd IS NULL OR avg_daily_budget_usd >= 0),
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, country)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS city_tags (
    city_id INTEGER NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (city_id, tag_id)
);

CREATE TABLE IF NOT EXISTS city_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    city_id    INTEGER,
    city_name  TEXT NOT NULL,
    processed  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  INTEGER,
    details    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS email_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER REFERENCES city_events(id),
    to_addr    TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('sent', 'simulated', 'failed')),
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at    TEXT
);

CREATE TABLE IF NOT EXISTS kb_documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    city       TEXT COLLATE NOCASE,
    source     TEXT NOT NULL DEFAULT 'api',
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    last_city  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    agent      TEXT,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cities_country ON cities(country);
CREATE INDEX IF NOT EXISTS idx_city_tags_tag ON city_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_events_processed ON city_events(processed);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- "New city trigger": every INSERT into cities creates an event for the Email Agent
-- and an audit row for the admin table.
CREATE TRIGGER IF NOT EXISTS trg_city_after_insert AFTER INSERT ON cities
BEGIN
    INSERT INTO city_events (event_type, city_id, city_name)
    VALUES ('city_added', NEW.id, NEW.name);
    INSERT INTO audit_log (action, entity, entity_id, details)
    VALUES ('INSERT', 'city', NEW.id, NEW.name || ' (' || NEW.country || ')');
END;

CREATE TRIGGER IF NOT EXISTS trg_city_after_update AFTER UPDATE ON cities
BEGIN
    INSERT INTO audit_log (action, entity, entity_id, details)
    VALUES ('UPDATE', 'city', NEW.id, NEW.name || ' (' || NEW.country || ')');
END;

CREATE TRIGGER IF NOT EXISTS trg_city_after_delete AFTER DELETE ON cities
BEGIN
    INSERT INTO audit_log (action, entity, entity_id, details)
    VALUES ('DELETE', 'city', OLD.id, OLD.name || ' (' || OLD.country || ')');
END;
"""


class Database:
    """Opens a fresh connection per unit of work (safe across threads)."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)
