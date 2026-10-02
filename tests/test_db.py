"""Tests for the SQLite persistence layer."""

from __future__ import annotations

from app import db


def _conn(settings):
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    return conn


def test_upsert_is_idempotent_and_keeps_status(settings):
    conn = _conn(settings)
    db.upsert_pending_subscriber(conn, "a@example.com")
    db.upsert_pending_subscriber(conn, "a@example.com")
    rows = conn.execute("SELECT COUNT(*) AS n FROM subscribers").fetchone()
    assert rows["n"] == 1

    db.confirm_subscriber(conn, "a@example.com")
    db.upsert_pending_subscriber(conn, "a@example.com")  # must not downgrade
    assert db.get_subscriber_by_email(conn, "a@example.com")["status"] == "confirmed"
    conn.close()


def test_confirm_is_single_use(settings):
    conn = _conn(settings)
    db.upsert_pending_subscriber(conn, "b@example.com")
    assert db.confirm_subscriber(conn, "b@example.com") is True
    assert db.confirm_subscriber(conn, "b@example.com") is False
    conn.close()


def test_unsubscribe_is_hard_delete(settings):
    conn = _conn(settings)
    db.upsert_pending_subscriber(conn, "c@example.com")
    token = db.get_subscriber_by_email(conn, "c@example.com")["unsub_token"]

    assert db.delete_subscriber_by_token(conn, token) is True
    assert db.get_subscriber_by_email(conn, "c@example.com") is None
    assert conn.execute("SELECT COUNT(*) AS n FROM subscribers").fetchone()["n"] == 0
    # Token is no longer resolvable.
    assert db.get_subscriber_by_unsub_token(conn, token) is None
    assert db.delete_subscriber_by_token(conn, token) is False
    conn.close()


def test_each_subscriber_gets_a_unique_unsub_token(settings):
    conn = _conn(settings)
    db.upsert_pending_subscriber(conn, "d@example.com")
    db.upsert_pending_subscriber(conn, "e@example.com")
    t1 = db.get_subscriber_by_email(conn, "d@example.com")["unsub_token"]
    t2 = db.get_subscriber_by_email(conn, "e@example.com")["unsub_token"]
    assert t1 != t2
    conn.close()


def test_seen_posts_dedup(settings):
    conn = _conn(settings)
    assert db.is_post_seen(conn, "u") is False
    db.mark_post_seen(conn, "u", "T", "2026-10-01")
    db.mark_post_seen(conn, "u", "T2", "2026-10-02")  # ON CONFLICT DO NOTHING
    assert db.is_post_seen(conn, "u") is True
    assert conn.execute("SELECT COUNT(*) AS n FROM seen_posts").fetchone()["n"] == 1
    conn.close()


def test_meta_roundtrip(settings):
    conn = _conn(settings)
    assert db.get_meta(conn, "k") is None
    db.set_meta(conn, "k", "v1")
    db.set_meta(conn, "k", "v2")
    assert db.get_meta(conn, "k") == "v2"
    conn.close()
