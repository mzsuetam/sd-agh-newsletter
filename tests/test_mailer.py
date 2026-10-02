"""Tests for e-mail rendering (title as h1, body, footer, unsubscribe link)."""

from __future__ import annotations

from datetime import date

from app.mailer import (
    build_post_message,
    build_verification_message,
    render_post_html,
    sanitize_html,
)
from app.scraper import NewsPost

POST = NewsPost(
    url="https://sd.agh.edu.pl/aktualnosci/detail/post-a",
    title="Post A",
    post_date=date(2026, 10, 1),
    lead="Lead paragraph.",
    body_html='<p>Body <strong>text</strong>.</p><p><a href="/x">link</a></p>',
)


def test_html_contains_h1_title_body_and_footer(settings):
    html = render_post_html(POST, settings, "https://app/unsubscribe?token=T")
    assert "<h1" in html and "Post A" in html
    assert "Body <strong>text</strong>." in html
    assert "projekt prywatny" in html
    assert "https://app/unsubscribe?token=T" in html
    assert "Zobacz oryginalny wpis" in html


def test_sanitize_strips_scripts_and_event_handlers():
    dirty = '<p onclick="evil()">ok</p><script>alert(1)</script><a href="javascript:evil()">x</a>'
    clean = sanitize_html(dirty)
    assert "<script" not in clean
    assert "onclick" not in clean
    assert "javascript:" not in clean
    assert "ok" in clean


def test_message_has_plain_and_html_parts(settings):
    msg = build_post_message(POST, settings, "sub@example.com", "https://app/u?token=T")
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types
    assert "text/html" in types
    assert msg["Subject"] == "Post A"
    assert msg["Auto-Submitted"] == "auto-generated"


def test_verification_message_contains_link(settings):
    msg = build_verification_message("a@example.com", "https://app/verify?token=V", settings)
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "https://app/verify?token=V" in html
    assert msg["To"] == "a@example.com"
