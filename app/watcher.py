"""Hourly watcher: detect new Aktualnosci posts and e-mail them out.

Run modes
---------
Scheduler (default, used by the ``scheduler`` container)::

    python -m app.watcher

One-shot check::

    python -m app.watcher --once

Time-travel / test mode -- pretend it is another instant so e-mail composition
and sending can be exercised without waiting for a real post::

    # Render what would have been sent at 00:00 on 2026-10-01 (no network send)
    python -m app.watcher --once --dry-run --now "2026-10-01 00:00"

    # Actually send, but only to a test address, ignoring the subscriber table
    python -m app.watcher --once --now "2026-10-02 00:00" --to test@example.com

Detection rules
----------------
* The listing is the source of truth; every post carries a publication date.
* ``last_check_at`` is a watermark (max publication date ever processed).
* ``seen_posts`` is an idempotency table keyed by URL -- a post is emailed at
  most once, ever.
* On the very first run the watcher *seeds* state instead of flooding inboxes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from . import db
from .config import WARSAW, Settings
from .mailer import send_post_notification
from .scraper import NewsItem, NewsPost, fetch_post, fetch_listing, make_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("app.watcher")

WATERMARK_KEY = "last_check_at"


@dataclass
class CheckSummary:
    seeded: bool = False
    considered: int = 0
    new_posts: int = 0
    emails_sent: int = 0
    errors: int = 0


def _parse_now(raw: str | None) -> datetime:
    """Parse ``--now`` (``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM``) in Europe/Warsaw."""
    if not raw:
        return datetime.now(WARSAW)
    text = raw.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=WARSAW)
        except ValueError:
            continue
    raise SystemExit(f"error: --now must be 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM', got {raw!r}")


def _select_candidates(
    conn,
    items: list[NewsItem],
    watermark: str | None,
    ignore_watermark: bool,
) -> list[NewsItem]:
    """Items whose date is at/after the watermark and that were never emailed."""
    ordered = sorted(items, key=lambda i: (i.post_date, i.url))
    if ignore_watermark or watermark is None:
        floor = None
    else:
        floor = datetime.fromisoformat(watermark).date()
    return [
        item
        for item in ordered
        if (floor is None or item.post_date >= floor)
        and not db.is_post_seen(conn, item.url)
    ]


def _write_preview(post: NewsPost, settings: Settings, msg) -> Path:
    preview_dir = settings.db_path.parent / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    slug = post.url.rstrip("/").split("/")[-1] or "post"
    path = preview_dir / f"{post.post_date.isoformat()}-{slug}.eml"
    path.write_bytes(msg.as_bytes())
    return path


def run_check(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    dry_run: bool | None = None,
    override_to: str | None = None,
    advance_watermark: bool = True,
    ignore_watermark: bool = False,
) -> CheckSummary:
    """Perform one detection cycle. Returns a summary of what happened."""
    settings = settings or Settings.from_env()
    now = now or datetime.now(WARSAW)
    dry = settings.dry_run if dry_run is None else dry_run
    summary = CheckSummary()

    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)

        watermark = db.get_meta(conn, WATERMARK_KEY)
        first_run = watermark is None and not ignore_watermark
        floor = (
            None
            if ignore_watermark or watermark is None
            else datetime.fromisoformat(watermark).date()
        )

        # Stop paging as soon as a page contributes nothing new. Because
        # seen_posts starts empty, this fetches the whole archive once (or
        # every page back to the watermark on the very first run, which also
        # seeds it) and only the first page or two on later runs.
        pages_fetched = {"n": 0}

        def page_date_hook(page: int, items: list[NewsItem]) -> bool:
            pages_fetched["n"] = page
            if first_run or floor is None:
                return False  # need to walk back through the whole archive
            return all(db.is_post_seen(conn, i.url) for i in items)

        with make_client() as client:
            items = fetch_listing(
                client,
                settings.news_base_url,
                stop_before=floor,
                page_date_hook=page_date_hook,
            )
            logger.info(
                "Listing yielded %d posts (%d page(s) fetched)",
                len(items),
                pages_fetched["n"],
            )
            if not items:
                logger.warning("No posts found; aborting cycle")
                return summary

            # First ever run: remember everything, send nothing.
            if first_run:
                for item in items:
                    db.mark_post_seen(conn, item.url, item.title, item.post_date.isoformat())
                newest = max(i.post_date for i in items)
                db.set_meta(conn, WATERMARK_KEY, newest.isoformat())
                logger.info(
                    "First run: seeded %d posts, watermark=%s (no e-mails sent)",
                    len(items),
                    newest.isoformat(),
                )
                summary.seeded = True
                return summary

            candidates = _select_candidates(conn, items, watermark, ignore_watermark)
            summary.considered = len(candidates)
            logger.info("New posts to process: %d", len(candidates))

            recipients = (
                [override_to]
                if override_to
                else [r["email"] for r in db.list_confirmed_subscribers(conn)]
            )

            highest = datetime.fromisoformat(watermark).date() if watermark else None

            for item in candidates:
                try:
                    post = fetch_post(client, item.url)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to fetch %s: %s", item.url, exc)
                    summary.errors += 1
                    continue

                summary.new_posts += 1
                logger.info("New post: %s (%s)", post.title, post.post_date)

                if dry:
                    from .mailer import build_post_message

                    recipient = recipients[0] if recipients else override_to or "preview@example.com"
                    msg = build_post_message(
                        post,
                        settings,
                        recipient,
                        settings.unsubscribe_url("PREVIEW-TOKEN"),
                    )
                    path = _write_preview(post, settings, msg)
                    print(f"--- DRY RUN: {post.title} ({post.post_date}) ---")
                    print(msg.get_body(preferencelist=("plain",)).get_content()[:2000])
                    print(f"--- preview written to {path} ---")
                    continue

                for email in recipients:
                    subscriber = db.get_subscriber_by_email(conn, email)
                    unsub_token = subscriber["unsub_token"] if subscriber else "preview-token"
                    result = send_post_notification(
                        post, settings, email, settings.unsubscribe_url(unsub_token)
                    )
                    if result.sent:
                        summary.emails_sent += 1
                    else:
                        summary.errors += 1

                if advance_watermark:
                    db.mark_post_seen(conn, post.url, post.title, post.post_date.isoformat())
                    if highest is None or post.post_date > highest:
                        highest = post.post_date

            if advance_watermark and highest is not None and not dry:
                db.set_meta(conn, WATERMARK_KEY, highest.isoformat())

        logger.info(
            "Cycle done: new=%d sent=%d errors=%d seeded=%s",
            summary.new_posts,
            summary.emails_sent,
            summary.errors,
            summary.seeded,
        )
        return summary
    finally:
        conn.close()


def build_scheduler(settings: Settings) -> BlockingScheduler:
    """Create the cron scheduler that checks a few times a day."""
    scheduler = BlockingScheduler(timezone=WARSAW)
    scheduler.add_job(
        run_check,
        "cron",
        hour=",".join(str(h) for h in settings.schedule_hours),
        minute=0,
        id="check-news",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler


def run_forever(settings: Settings) -> None:
    """Run the scheduler that checks once a day at midnight (00:00)."""
    scheduler = build_scheduler(settings)
    logger.info(
        "Scheduler started; checking daily at %s (Europe/Warsaw)",
        ", ".join(f"{h:02d}:00" for h in settings.schedule_hours),
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.watcher",
        description="Watch sd.agh.edu.pl/aktualnosci and e-mail new posts.",
    )
    parser.add_argument("--once", action="store_true", help="run a single check and exit")
    parser.add_argument(
        "--now",
        metavar="WHEN",
        help="pretend it is this instant (Europe/Warsaw), "
        "'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (test/time-travel mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render e-mails and write .eml previews instead of sending",
    )
    parser.add_argument(
        "--to",
        metavar="EMAIL",
        help="send only to this address, ignoring the subscriber table",
    )
    parser.add_argument(
        "--no-watermark-advance",
        action="store_true",
        help="do not update seen_posts/last_check_at (repeatable test runs)",
    )
    parser.add_argument(
        "--ignore-watermark",
        action="store_true",
        help="consider every unseen post regardless of the watermark (testing)",
    )
    parser.add_argument(
        "--hours",
        metavar="H,H,...",
        help="override SCHEDULE_HOURS for the scheduler, e.g. '0' or '9,17,21'",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.hours:
        hours = tuple(sorted({int(h) for h in args.hours.split(",") if h.strip()}))
        settings = replace(settings, schedule_hours=hours)

    if args.once or args.now or args.dry_run or args.to or args.ignore_watermark:
        run_check(
            settings,
            now=_parse_now(args.now),
            dry_run=args.dry_run or None,
            override_to=args.to,
            advance_watermark=not args.no_watermark_advance and not args.dry_run,
            ignore_watermark=args.ignore_watermark,
        )
        return 0

    run_forever(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
