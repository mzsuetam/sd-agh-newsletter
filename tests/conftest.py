"""Shared pytest fixtures and HTML fragments matching the real site markup."""

from __future__ import annotations

import pytest

from app.config import Settings

# Mirrors the verified structure of /aktualnosci (see app/scraper.py docstring).
LISTING_HTML = """
<html><body>
<div class="list-news">
  <div class="row">
    <a title="Post A" href="/aktualnosci/detail/post-a">
      <article id="1" class="d-md-flex cat-10">
        <div class="article-description">
          <h2> Post A </h2>
          <p> Teaser A &nbsp;[...] </p>
          <span class="date">01.10.2026</span>
        </div>
      </article>
    </a>
    <hr>
    <a title="Post B" href="/aktualnosci/detail/post-b">
      <article id="1" class="d-md-flex cat-10">
        <div class="article-description">
          <h2> Post B </h2>
          <p> Teaser B &nbsp;[...] </p>
          <span class="date">21.09.2026</span>
        </div>
      </article>
    </a>
    <hr>
    <a href="/somewhere-else/ignored">Ignored</a>
  </div>
</div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
  <span class="internal-header"> Post A </span>
  <div class="row"><div class="text-end mb-3">
    <span class="date me-3">
      <time itemprop="datePublished" datetime="2026-10-01"> 01/10/2026 </time>
    </span>
  </div></div>
  <div class="row"><div class="col-12 news-elem">
    <p class="post-header my-4"> Lead paragraph. </p>
  </div></div>
  <div class="row"><div class="col-12">
    <div class="text-mt">
      <p>First body <strong>paragraph</strong>.</p>
      <p><a href="/doktoranci/stypendia">A link</a></p>
    </div>
  </div></div>
  <div class="mt-4 text-center back-btn"><a href="/aktualnosci">
    <button>Powrót</button></a></div>
  <section><h2 class="visually-hidden">Stopka</h2>
    <div id="pageFooter">Footer junk</div>
  </section>
</body></html>
"""

DETAIL_HTML_NO_ISO = DETAIL_HTML.replace(' datetime="2026-10-01"', "")
DETAIL_HTML_NO_DATE = DETAIL_HTML.replace(
    '<span class="date me-3">\n      <time itemprop="datePublished" datetime="2026-10-01"> 01/10/2026 </time>\n    </span>',
    "",
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        smtp_host="localhost",
        smtp_port=1025,
        sender_email="sender@example.com",
        app_password="secret",
        sender_name="Test Sender",
        secret_key="test-secret-key",
        base_url="http://localhost:8000",
        db_path=tmp_path / "app.db",
        check_interval_seconds=3600,
        schedule_hours=(0,),
        verify_token_ttl_seconds=86400,
        news_base_url="https://sd.agh.edu.pl/aktualnosci",
        dry_run=True,
        test_redirect_email=None,
    )
