"""Tests for signed verification tokens."""

from __future__ import annotations

import pytest

from app.tokens import TokenError, make_verify_token, read_verify_token

SECRET = "unit-test-secret"


def test_roundtrip_is_case_insensitive():
    token = make_verify_token(SECRET, "User@Example.COM")
    assert read_verify_token(SECRET, token, 3600) == "user@example.com"


def test_tampered_token_rejected():
    token = make_verify_token(SECRET, "a@example.com")
    with pytest.raises(TokenError):
        read_verify_token(SECRET + "-wrong", token, 3600)


def test_expired_token_rejected():
    token = make_verify_token(SECRET, "a@example.com")
    with pytest.raises(TokenError):
        read_verify_token(SECRET, token, -1)  # max_age < 0 => already expired


def test_garbage_token_rejected():
    with pytest.raises(TokenError):
        read_verify_token(SECRET, "not-a-token", 3600)
