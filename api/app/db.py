"""Postgres access for the API. All SQL lives here so main.py stays about HTTP."""

from __future__ import annotations

import os
import uuid

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS photos (
    id           UUID PRIMARY KEY,
    event_code   TEXT NOT NULL REFERENCES events(code),
    original_key TEXT NOT NULL,
    display_key  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed
    blur_score   REAL,
    is_blurry    BOOLEAN,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS photos_event_idx ON photos(event_code, created_at);
"""

# Any constant number works; all api replicas just have to agree on it.
SCHEMA_LOCK_ID = 727_001


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5)


def init_schema(conn) -> None:
    """Create tables. Safe when several api replicas start at the same moment.

    `CREATE TABLE IF NOT EXISTS` is not race-free in Postgres: two replicas running it at
    once can fail with a duplicate-key error. A session-level advisory lock makes the
    replicas take turns; the second one then finds the tables already there.
    """
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_ID,))
    try:
        conn.execute(SCHEMA)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_ID,))


def ping() -> None:
    with connect() as conn:
        conn.execute("SELECT 1")


def create_event(code: str, name: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO events (code, name) VALUES (%s, %s)", (code, name))


def get_event(code: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT code, name FROM events WHERE code = %s", (code,)).fetchone()
    return {"code": row[0], "name": row[1]} if row else None


def add_photo(photo_id: uuid.UUID, event_code: str, original_key: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO photos (id, event_code, original_key) VALUES (%s, %s, %s)",
            (photo_id, event_code, original_key),
        )


def list_ready_photos(event_code: str) -> list[tuple[str, str]]:
    """(id, display_key) of processed, non-blurry photos, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, display_key FROM photos
               WHERE event_code = %s AND status = 'ready' AND NOT is_blurry
               ORDER BY created_at""",
            (event_code,),
        ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]
