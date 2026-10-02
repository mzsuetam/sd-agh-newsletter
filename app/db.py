"""SQLite persistence layer.

Design notes
------------
* Every statement is parameterised -- no string interpolation of user input.
* The subscriber e-mail has a UNIQUE constraint, so duplicate registrations
  cannot create multiple rows.
* Unsubscribing is a **hard delete** (``DELETE FROM subscribers``); no soft
  flags are kept.
* Timestamps are stored as ISO-8601 strings in Europe/Warsaw.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .config import WARSAW

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'confirmed')),
    created_at   TEXT NOT NULL,
    confirmed_at TEXT,
    unsub_token  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS seen_posts (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    post_date     TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    """Current time in Europe/Warsaw as an ISO-8601 string."""
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def new_token() -> str:
    """Cryptographically strong, URL-safe token."""
    return secrets.token_urlsafe(32)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a tuned SQLite connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------------------
# Subscribers
# --------------------------------------------------------------------------


def upsert_pending_subscriber(conn: sqlite3.Connection, email: str) -> None:
    """Register ``email`` as pending, or leave an existing row untouched.

    Idempotent: re-registering a confirmed address does not downgrade it, and
    re-registering a pending address does not leak whether it existed.
    """
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO subscribers (email, status, created_at, unsub_token)
            VALUES (?, 'pending', ?, ?)
            ON CONFLICT(email) DO NOTHING
            """,
            (email, now_iso(), new_token()),
        )


def get_subscriber_by_email(
    conn: sqlite3.Connection, email: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subscribers WHERE email = ?", (email,)
    ).fetchone()


def confirm_subscriber(conn: sqlite3.Connection, email: str) -> bool:
    """Mark a pending subscriber as confirmed. Returns True if newly confirmed."""
    with transaction(conn):
        cur = conn.execute(
            """
            UPDATE subscribers
               SET status = 'confirmed', confirmed_at = ?
             WHERE email = ? AND status = 'pending'
            """,
            (now_iso(), email),
        )
    return cur.rowcount > 0


def get_subscriber_by_unsub_token(
    conn: sqlite3.Connection, token: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subscribers WHERE unsub_token = ?", (token,)
    ).fetchone()


def delete_subscriber_by_token(conn: sqlite3.Connection, token: str) -> bool:
    """Hard delete. Returns True if a row was removed."""
    with transaction(conn):
        cur = conn.execute("DELETE FROM subscribers WHERE unsub_token = ?", (token,))
    return cur.rowcount > 0


def list_confirmed_subscribers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subscribers WHERE status = 'confirmed' ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------
# Seen posts
# --------------------------------------------------------------------------


def is_post_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_posts WHERE url = ?", (url,)).fetchone()
    return row is not None


def mark_post_seen(
    conn: sqlite3.Connection, url: str, title: str, post_date: str
) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO seen_posts (url, title, post_date, first_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO NOTHING
            """,
            (url, title, post_date, now_iso()),
        )


# --------------------------------------------------------------------------
# Meta / watermark
# --------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
