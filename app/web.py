"""FastAPI web frontend: subscribe (with e-mail verification) and unsubscribe.

Security measures
------------------
* Hidden honeypot field -- silently ignored when filled (bot trap).
* Per-IP rate limiting on ``POST /subscribe``.
* Generic responses: the UI never reveals whether an address is subscribed.
* Signed, expiring, single-use verification tokens.
* Unsubscribe is a **hard delete** of the subscriber row.
* ``no-store`` + ``no-referrer`` on token pages so links do not leak via
  caches or the ``Referer`` header.
* Security headers on every response.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import Settings
from .mailer import send_verification
from .tokens import TokenError, make_verify_token, read_verify_token

logger = logging.getLogger("app.web")

BASE_DIR = Path(__file__).resolve().parent
settings = Settings.from_env()

app = FastAPI(title="Aktualnosci newsletter", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

# --- naive in-process rate limiter (adequate for a single-worker app) -------
_RATE_LIMIT = 5
_RATE_WINDOW = 600.0  # seconds
_hits: dict[str, deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        bucket = _hits[ip]
        while bucket and now - bucket[0] > _RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT:
            return True
        bucket.append(now)
        return False


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    if request.url.path in {"/verify", "/unsubscribe"}:
        response.headers["Cache-Control"] = "no-store"
    return response


def _render(request: Request, template: str, status: int = 200, **ctx):
    ctx.setdefault("settings", settings)
    return templates.TemplateResponse(request, template, ctx, status_code=status)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _render(request, "index.html")


@app.post("/subscribe", response_class=HTMLResponse)
async def subscribe(
    request: Request,
    email: str = Form(default=""),
    website: str = Form(default=""),  # honeypot
):
    ip = _client_ip(request)

    # Honeypot: pretend success, do nothing.
    if website.strip():
        logger.info("Honeypot triggered from %s", ip)
        return _render(request, "message.html", title="Sprawdź skrzynkę", body=(
            "Jeśli podany adres był poprawny, wysłaliśmy na niego link "
            "potwierdzający subskrypcję."
        ))

    if _rate_limited(ip):
        return _render(
            request,
            "message.html",
            status=429,
            title="Zbyt wiele prób",
            body="Odczekaj kilka minut i spróbuj ponownie.",
        )

    address = email.strip().lower()
    if not address or len(address) > 254 or not _EMAIL_RE.match(address):
        return _render(
            request,
            "message.html",
            status=400,
            title="Niepoprawny adres",
            body="Podaj poprawny adres e-mail.",
        )

    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        db.upsert_pending_subscriber(conn, address)
        row = db.get_subscriber_by_email(conn, address)
        # Only send a verification mail if the address is not already confirmed.
        if row is not None and row["status"] == "pending":
            token = make_verify_token(settings.secret_key, address)
            send_verification(settings, address, settings.verify_url(token))
    finally:
        conn.close()

    # Generic response regardless of prior existence.
    return _render(
        request,
        "message.html",
        title="Sprawdź skrzynkę",
        body=(
            "Jeśli podany adres był poprawny, wysłaliśmy na niego link "
            "potwierdzający subskrypcję. Kliknij go, aby dokończyć zapis."
        ),
    )


@app.get("/verify", response_class=HTMLResponse)
async def verify(request: Request, token: str = Query(default="")):
    try:
        address = read_verify_token(
            settings.secret_key, token, settings.verify_token_ttl_seconds
        )
    except TokenError as exc:
        return _render(
            request,
            "message.html",
            status=400,
            title="Link nieprawidłowy",
            body=f"Ten link potwierdzający jest nieprawidłowy lub wygasł ({exc}). "
            "Zapisz się ponownie, aby otrzymać nowy.",
        )

    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        db.confirm_subscriber(conn, address)
    finally:
        conn.close()

    return _render(
        request,
        "message.html",
        title="Subskrypcja potwierdzona",
        body="Dziękujemy! Od teraz będziesz otrzymywać nowe wpisy z Aktualności.",
    )


@app.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_form(request: Request, token: str = Query(default="")):
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        row = db.get_subscriber_by_unsub_token(conn, token)
    finally:
        conn.close()

    if row is None:
        return _render(
            request,
            "message.html",
            title="Link nieaktywny",
            body="Ten link do rezygnacji jest nieprawidłowy lub został już użyty.",
        )

    return _render(request, "unsubscribe.html", token=token)


@app.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm(request: Request, token: str = Form(default="")):
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        removed = db.delete_subscriber_by_token(conn, token)
    finally:
        conn.close()

    return _render(
        request,
        "message.html",
        title="Zrezygnowano z subskrypcji",
        body=(
            "Twój adres został trwale usunięty z bazy."
            if removed
            else "Ten link do rezygnacji jest nieprawidłowy lub został już użyty."
        ),
    )


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)
