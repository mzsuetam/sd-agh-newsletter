"""Tests for the listing/detail parser."""

from __future__ import annotations

from datetime import date

import pytest

from app.scraper import (
    ScraperError,
    parse_detail,
    parse_listing,
    to_warsaw_iso,
)
from tests.conftest import DETAIL_HTML, DETAIL_HTML_NO_DATE, DETAIL_HTML_NO_ISO, LISTING_HTML

BASE = "https://sd.agh.edu.pl/aktualnosci"


def test_parse_listing_extracts_items():
    items = parse_listing(LISTING_HTML, BASE)
    assert len(items) == 2  # the non-detail link is ignored
    first, second = items
    assert first.title == "Post A"
    assert first.post_date == date(2026, 10, 1)
    assert first.url == f"{BASE}/detail/post-a"
    assert first.slug_path == "/aktualnosci/detail/post-a"
    assert second.post_date == date(2026, 9, 21)


def test_parse_listing_skips_entries_without_date():
    html = LISTING_HTML.replace('<span class="date">01.10.2026</span>', "")
    items = parse_listing(html, BASE)
    assert [i.title for i in items] == ["Post B"]


def test_parse_detail_uses_iso_date_and_body():
    post = parse_detail(DETAIL_HTML, "https://x/post-a")
    assert post.title == "Post A"
    assert post.post_date == date(2026, 10, 1)
    assert post.lead == "Lead paragraph."
    assert "First body" in post.body_html
    assert "/doktoranci/stypendia" in post.body_html
    # Nothing from the "Powrót" / footer region leaks in.
    assert "Footer junk" not in post.body_html
    assert "Powrót" not in post.body_html


def test_parse_detail_falls_back_to_display_date():
    post = parse_detail(DETAIL_HTML_NO_ISO, "https://x/post-a")
    assert post.post_date == date(2026, 10, 1)


def test_parse_detail_raises_without_date():
    with pytest.raises(ScraperError):
        parse_detail(DETAIL_HTML_NO_DATE, "https://x/post-a")


def test_to_warsaw_iso_is_start_of_day():
    assert to_warsaw_iso(date(2026, 10, 1)).startswith("2026-10-01T00:00:00")
