"""Tests for the detection logic (watermark + seen-URL dedup)."""

from __future__ import annotations

from datetime import date

import app.watcher as watcher
from app import db
from app.scraper import NewsItem


def _item(slug: str, d: date) -> NewsItem:
    return NewsItem(
        url=f"https://sd.agh.edu.pl/aktualnosci/detail/{slug}",
        slug_path=f"/aktualnosci/detail/{slug}",
        title=slug,
        post_date=d,
    )


def _conn(settings):
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    return conn


def test_selection_respects_watermark_and_seen(settings):
    conn = _conn(settings)
    items = [
        _item("old", date(2026, 9, 1)),
        _item("edge", date(2026, 9, 15)),
        _item("new", date(2026, 9, 20)),
    ]
    db.mark_post_seen(conn, items[2].url, "new", "2026-09-20")

    picked = watcher._select_candidates(conn, items, "2026-09-15", ignore_watermark=False)
    # "old" is before the watermark, "new" is already seen; only "edge" remains.
    assert [i.title for i in picked] == ["edge"]
    conn.close()


def test_ignore_watermark_still_excludes_seen(settings):
    conn = _conn(settings)
    items = [_item("a", date(2026, 9, 1)), _item("b", date(2026, 9, 2))]
    db.mark_post_seen(conn, items[0].url, "a", "2026-09-01")
    picked = watcher._select_candidates(conn, items, None, ignore_watermark=True)
    assert [i.title for i in picked] == ["b"]
    conn.close()


def test_parse_now_accepts_supported_formats():
    assert watcher._parse_now("2026-10-01").hour == 0
    assert watcher._parse_now("2026-10-01 00:00").date() == date(2026, 10, 1)
    assert watcher._parse_now("2026-10-01T13:30").hour == 13


def test_scheduler_runs_once_a_day_at_midnight(settings):
    sched = watcher.build_scheduler(settings)
    job = sched.get_job("check-news")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "0"
    assert fields["minute"] == "0"


def test_default_schedule_is_once_a_day(monkeypatch):
    from app.config import Settings

    for var in ("SCHEDULE_HOURS",):
        monkeypatch.delenv(var, raising=False)
    assert Settings.from_env().schedule_hours == (0,)


def test_schedule_hours_parsed_and_sorted(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SCHEDULE_HOURS", "21, 9 ,17")
    assert Settings.from_env().schedule_hours == (9, 17, 21)


def test_fetch_listing_stops_when_page_adds_nothing_new(settings, monkeypatch):
    """Keep paging while a page brings new posts; stop once it brings none."""
    import app.scraper as scraper

    page1 = """
    <div class="list-news">
      <a href="/aktualnosci/detail/n1"><article>
        <h2>N1</h2><span class="date">02.10.2026</span></article></a>
      <a href="/aktualnosci/detail/n2"><article>
        <h2>N2</h2><span class="date">01.10.2026</span></article></a>
    </div>"""

    class FakeResp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def get(self, url):
            self.calls += 1
            if self.calls == 1:
                return FakeResp(page1)
            # Page 2 repeats an already-seen item -> nothing new.
            return FakeResp(
                '<div class="list-news"><a href="/aktualnosci/detail/n2">'
                '<article><h2>N2</h2><span class="date">01.10.2026</span>'
                "</article></a></div>"
            )

    client = FakeClient()
    seen = {"https://sd.agh.edu.pl/aktualnosci/detail/n2"}

    def hook(page, items):
        return all(i.url in seen for i in items)

    items = scraper.fetch_listing(
        client, "https://sd.agh.edu.pl/aktualnosci", page_date_hook=hook
    )
    assert {i.title for i in items} == {"N1", "N2"}
    assert client.calls == 2  # page 2 added nothing -> stop, never fetch page 3


def test_fetch_listing_stops_after_first_page_when_nothing_is_new(settings):
    """If page 1 is entirely already-seen, there is nothing new to find."""
    import app.scraper as scraper

    page1 = """
    <div class="list-news">
      <a href="/aktualnosci/detail/n1"><article>
        <h2>N1</h2><span class="date">02.10.2026</span></article></a>
    </div>"""

    class FakeResp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def get(self, url):
            self.calls += 1
            return FakeResp(page1)

    client = FakeClient()
    seen = {"https://sd.agh.edu.pl/aktualnosci/detail/n1"}

    items = scraper.fetch_listing(
        client,
        "https://sd.agh.edu.pl/aktualnosci",
        page_date_hook=lambda page, items: all(i.url in seen for i in items),
    )
    assert len(items) == 1
    assert client.calls == 1  # one request only
