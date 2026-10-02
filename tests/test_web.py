"""End-to-end tests for the web frontend (subscribe -> verify -> unsubscribe)."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture
def client(settings, monkeypatch):
    """A TestClient bound to the fixture settings/database."""
    monkeypatch.setenv("DB_PATH", str(settings.db_path))
    monkeypatch.setenv("SECRET_KEY", settings.secret_key)
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("SMTP_HOST", "localhost")

    import app.config as config

    importlib.reload(config)
    import app.web as web

    importlib.reload(web)
    return TestClient(web.app, follow_redirects=False)


def test_health_and_index(client):
    assert client.get("/healthz").status_code == 200
    body = client.get("/").text
    assert 'name="email"' in body
    assert 'name="website"' in body  # honeypot present


def test_honeypot_is_silently_ignored(client, settings):
    resp = client.post("/subscribe", data={"email": "bot@example.com", "website": "spam"})
    assert resp.status_code == 200
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    assert db.get_subscriber_by_email(conn, "bot@example.com") is None
    conn.close()


def test_invalid_email_rejected(client):
    assert client.post("/subscribe", data={"email": "nope", "website": ""}).status_code == 400


def test_full_lifecycle(client, settings):
    resp = client.post("/subscribe", data={"email": "User@Example.com ", "website": ""})
    assert resp.status_code == 200

    conn = db.connect(settings.db_path)
    row = db.get_subscriber_by_email(conn, "user@example.com")
    assert row["status"] == "pending"
    token = row["unsub_token"]
    conn.close()

    from app.tokens import make_verify_token

    vt = make_verify_token(settings.secret_key, "user@example.com")
    assert client.get(f"/verify?token={vt}").status_code == 200

    conn = db.connect(settings.db_path)
    assert db.get_subscriber_by_email(conn, "user@example.com")["status"] == "confirmed"
    conn.close()

    assert client.get(f"/unsubscribe?token={token}").status_code == 200
    assert client.post("/unsubscribe", data={"token": token}).status_code == 200

    conn = db.connect(settings.db_path)
    assert db.get_subscriber_by_email(conn, "user@example.com") is None  # hard delete
    conn.close()


def test_duplicate_subscribe_does_not_duplicate_row(client, settings):
    client.post("/subscribe", data={"email": "dup@example.com", "website": ""})
    client.post("/subscribe", data={"email": "dup@example.com", "website": ""})
    conn = db.connect(settings.db_path)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE email = ?", ("dup@example.com",)
    ).fetchone()["n"]
    assert n == 1
    conn.close()


def test_bad_verify_token_rejected(client):
    assert client.get("/verify?token=garbage").status_code == 400


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in resp.headers
