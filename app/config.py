"""Application configuration, loaded from environment variables.

The SMTP variables mirror ``hello.py`` (``SMTP_HOST```, ``SMTP_PORT``,
``SENDER_EMAIL``, ``APP_PASSWORD``) so existing ``.env`` files keep working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load variables from a local .env file if present. Never logged or printed.
load_dotenv()

#: The news site is located in Krakow.
WARSAW = ZoneInfo("Europe/Warsaw")


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - config error path
        raise ValueError(f"Environment variable {name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    smtp_host: str
    smtp_port: int
    sender_email: str
    app_password: str
    sender_name: str

    secret_key: str
    base_url: str
    db_path: Path
    check_interval_seconds: int
    schedule_hours: tuple[int, ...]
    verify_token_ttl_seconds: int

    news_base_url: str

    dry_run: bool
    test_redirect_email: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        test_redirect = os.environ.get("TEST_REDIRECT_EMAIL", "").strip() or None
        raw_hours = os.environ.get("SCHEDULE_HOURS", "")
        try:
            hours = tuple(
                sorted({int(h) for h in raw_hours.replace(";", ",").split(",") if h.strip()})
            )
        except ValueError as exc:
            raise ValueError(
                "SCHEDULE_HOURS must be a comma-separated list of hours, e.g. '0' or '9,17,21'"
            ) from exc
        if any(not 0 <= h <= 23 for h in hours):
            raise ValueError("SCHEDULE_HOURS entries must be between 0 and 23")
        return cls(
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=_get_int("SMTP_PORT", 465),
            sender_email=os.environ.get("SENDER_EMAIL", ""),
            app_password=os.environ.get("APP_PASSWORD", ""),
            sender_name=os.environ.get(
                "SENDER_NAME", "Newsletter Szkoly Doktorskiej AGH (nieoficjalny)"
            ),
            secret_key=os.environ.get("SECRET_KEY", "insecure-development-key"),
            base_url=os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/"),
            db_path=Path(os.environ.get("DB_PATH", "./data/app.db")),
            check_interval_seconds=_get_int("CHECK_INTERVAL_SECONDS", 3600),
            schedule_hours=hours or (0,),
            verify_token_ttl_seconds=_get_int("VERIFY_TOKEN_TTL_SECONDS", 86400),
            news_base_url=os.environ.get(
                "NEWS_BASE_URL", "https://sd.agh.edu.pl/aktualnosci"
            ).rstrip("/"),
            dry_run=_get_bool("DRY_RUN", False),
            test_redirect_email=test_redirect,
        )

    # -- derived helpers ---------------------------------------------------

    def verify_url(self, token: str) -> str:
        return f"{self.base_url}/verify?token={token}"

    def unsubscribe_url(self, token: str) -> str:
        return f"{self.base_url}/unsubscribe?token={token}"

    def post_url(self, slug_path: str) -> str:
        """Build an absolute URL for a post from its list href."""
        if slug_path.startswith("http"):
            return slug_path
        origin = self.news_base_url.split("/aktualnosci")[0]
        return f"{origin}{slug_path}"
