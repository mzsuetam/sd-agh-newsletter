"""Scraper for https://sd.agh.edu.pl/aktualnosci.

Verified markup (see README):

Listing (``/aktualnosci`` and ``/aktualnosci/page-N``)::

    <div class="list-news">
      <a href="/aktualnosci/detail/<slug>" title="...">
        <article class="...">
          <div class="article-description ...">
            <h2> Title </h2>
            <p> teaser [...] </p>
            <span class="date">01.10.2026</span>
          </div>
        </article>
      </a>

Detail (``/aktualnosci/detail/<slug>``)::

    <span class="internal-header"> Title </span>
    <span class="date me-3">
      <time itemprop="datePublished" datetime="2026-09-21"> 21/09/2026 </time>
    </span>
    ...
    <p class="post-header my-4"> lead paragraph </p>
    <div class="text-mt"> ... main body ... </div>
    ...
    <div class="back-btn"><a href="/aktualnosci"><button>Powrót</button></a></div>
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from .config import WARSAW

logger = logging.getLogger(__name__)

USER_AGENT = (
    "sd-agh-newsletter-bot/1.0 (+private, non-commercial newsletter; "
    "contact the project maintainer)"
)

_LIST_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_DETAIL_DISPLAY_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


class ScraperError(Exception):
    """Raised when the news site cannot be parsed."""


@dataclass(frozen=True)
class NewsItem:
    """A single news post as shown on the listing page."""

    url: str
    slug_path: str
    title: str
    post_date: date


@dataclass(frozen=True)
class NewsPost:
    """A fully fetched news post."""

    url: str
    title: str
    post_date: date
    lead: str
    body_html: str


def _parse_list_date(text: str) -> date | None:
    m = _LIST_DATE_RE.search(text or "")
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_detail_date(node: Tag | None) -> date | None:
    if node is None:
        return None
    iso = node.get("datetime")
    if iso:
        try:
            return datetime.fromisoformat(str(iso)).date()
        except ValueError:
            pass
    m = _DETAIL_DISPLAY_DATE_RE.search(node.get_text(" ", strip=True))
    if m:
        day, month, year = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def parse_listing(html: str, base_url: str) -> list[NewsItem]:
    """Extract every news item from one listing page."""
    soup = BeautifulSoup(html, "lxml")
    items: list[NewsItem] = []
    seen: set[str] = set()

    for anchor in soup.select('a[href*="/aktualnosci/detail/"]'):
        href = anchor.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue

        article = anchor.find("article")
        scope = article if article is not None else anchor

        h2 = scope.find("h2")
        title = (h2.get_text(" ", strip=True) if h2 else "") or (
            anchor.get("title") or ""
        )
        title = title.strip()
        if not title:
            continue

        date_span = scope.find("span", class_="date")
        parsed = _parse_list_date(
            date_span.get_text(" ", strip=True) if date_span else ""
        )
        if parsed is None:
            # A post without a readable date cannot be ordered safely; skip it
            # rather than risk emailing it out of order.
            logger.warning("Skipping listing entry with no parseable date: %s", href)
            continue

        seen.add(absolute)
        items.append(
            NewsItem(
                url=absolute,
                slug_path=href,
                title=title,
                post_date=parsed,
            )
        )
    return items


def fetch_listing(
    client: httpx.Client,
    base_url: str,
    max_pages: int = 50,
    stop_before: date | None = None,
    page_date_hook=None,
) -> list[NewsItem]:
    """Fetch listing pages, newest first, stopping as soon as it is safe.

    The listing is ordered newest-first, so once every item on a page predates
    ``stop_before`` there cannot be anything newer on later pages. Paging then
    stops, which keeps us from hammering the AGH servers.

    ``page_date_hook(page_number, items)`` -- when supplied, its return value
    overrides the "all items older than ``stop_before``" decision. The watcher
    uses it to stop only when a page *adds no new* posts, which is the correct
    rule when the publication dates are not perfectly ordered.

    Pages are requested as ``<base>/page-2``, ``page-3`` ... because ``<base>``
    itself is page 1. The site clamps out-of-range pages to the last page, so
    "no new URLs" is also treated as the end of the listing.
    """
    by_url: dict[str, NewsItem] = {}
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}/page-{page}"
        resp = client.get(url)
        resp.raise_for_status()
        items = parse_listing(resp.text, base_url)

        new = 0
        for item in items:
            if item.url not in by_url:
                by_url[item.url] = item
                new += 1
        logger.debug("Page %d: %d items, %d new", page, len(items), new)

        if new == 0:
            logger.debug("Page %d added no new posts; stopping", page)
            break

        if page_date_hook is not None:
            if page_date_hook(page, items):
                break
        elif stop_before is not None and items:
            newest_on_page = max(i.post_date for i in items)
            if newest_on_page < stop_before:
                logger.debug(
                    "Page %d is entirely older than %s; stopping", page, stop_before
                )
                break
    return list(by_url.values())


# --------------------------------------------------------------------------
# Detail
# --------------------------------------------------------------------------


def parse_detail(html: str, url: str) -> NewsPost:
    """Extract title, date and body from a detail page."""
    soup = BeautifulSoup(html, "lxml")

    header = soup.select_one(".internal-header")
    h1 = soup.find("h1")
    if header is not None:
        title = header.get_text(" ", strip=True)
    elif h1 is not None:
        title = h1.get_text(" ", strip=True)
    else:
        raise ScraperError(f"No title found on {url}")
    title = title.strip()

    time_node = soup.select_one('time[itemprop="datePublished"]')
    if time_node is None:
        time_node = soup.select_one("span.date time")
    post_date = _parse_detail_date(time_node)

    lead_node = soup.select_one("p.post-header")
    lead = lead_node.get_text(" ", strip=True) if lead_node else ""

    body_parts: list[str] = []
    for node in soup.select("div.text-mt"):
        # Stop before the "Powrót" button / footer if they ever nest inside.
        for junk in node.select(".back-btn, #pageFooter"):
            junk.decompose()
        body_parts.append(node.decode_contents().strip())
    body_html = "\n".join(p for p in body_parts if p)

    if post_date is None:
        raise ScraperError(f"No date found on {url}")

    return NewsPost(
        url=url, title=title, post_date=post_date, lead=lead, body_html=body_html
    )


def fetch_post(client: httpx.Client, url: str) -> NewsPost:
    resp = client.get(url)
    resp.raise_for_status()
    return parse_detail(resp.text, url)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_client(timeout: float = 20.0) -> httpx.Client:
    """Create a client with retries and a descriptive User-Agent."""
    transport = httpx.HTTPTransport(retries=3)
    return httpx.Client(
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.8"},
    )


def to_warsaw_iso(d: date) -> str:
    """Render a date as start-of-day Europe/Warsaw ISO-8601."""
    return datetime(d.year, d.month, d.day, tzinfo=WARSAW).isoformat(timespec="seconds")
