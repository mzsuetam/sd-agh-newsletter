"""E-mail delivery, mirroring the SMTP approach in ``hello.py``.

The transport is intentionally identical to the original reference script::

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)

Messages are built with :class:`email.message.EmailMessage` and always carry a
plain-text alternative alongside the HTML body.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from bs4 import BeautifulSoup

from .config import Settings
from .scraper import NewsPost

logger = logging.getLogger(__name__)

_DROP_TAGS = ("script", "style", "iframe", "object", "embed", "form", "input", "link")
_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:", "#", "/")


@dataclass(frozen=True)
class MailResult:
    sent: bool
    detail: str = ""


# --------------------------------------------------------------------------
# HTML sanitisation
# --------------------------------------------------------------------------


def sanitize_html(fragment: str) -> str:
    """Strip dangerous tags/attributes from scraped HTML.

    The source is the (trusted) AGH site, but content is attacker-influenceable
    in principle, and it is being embedded in an e-mail -- so we defensively
    remove scripts, event handlers and ``javascript:`` URLs.
    """
    if not fragment:
        return ""
    soup = BeautifulSoup(fragment, "lxml")

    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            value = tag.attrs[attr]
            if attr.lower().startswith("on"):
                del tag.attrs[attr]
            elif attr.lower() in {"href", "src"}:
                joined = value if isinstance(value, str) else " ".join(value)
                if not joined.strip().lower().startswith(_SAFE_URL_SCHEMES):
                    del tag.attrs[attr]

    body = soup.body
    return body.decode_contents().strip() if body else str(soup).strip()


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def _text_footer(settings: Settings, unsubscribe_url: str) -> str:
    return (
        "\n\n---\n"
        "Otrzymujesz te wiadomosc, poniewaz zapisales(-as) sie do newslettera "
        "Aktualnosci Szkoly Doktorskiej AGH.\n"
        "To jest projekt prywatny, niezalezny i niebedacy czescia systemow AGH. "
        "Nie jest oficjalnym kanalem Szkoły Doktorskiej AGH.\n"
        f"Aby zrezygnowac z subskrypcji (trwale usuniemy Twoj adres): {unsubscribe_url}\n"
    )


def _html_footer(settings: Settings, unsubscribe_url: str) -> str:
    return f"""
<hr style="margin:32px 0;border:none;border-top:1px solid #e0e0e0">
<table role="presentation" cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#666;line-height:1.5">
  <tr><td>
    <p style="margin:0 0 8px">
      Otrzymujesz te wiadomość, ponieważ zapisałeś(-aś) się do newslettera
      <strong>Aktualności Szkoły Doktorskiej AGH</strong>.
    </p>
    <p style="margin:0 0 8px">
      <strong>To jest projekt prywatny</strong>, niezależny i niebędący częścią
      systemów AGH. Nie jest oficjalnym kanałem Szkoły Doktorskiej AGH.
    </p>
    <p style="margin:0">
      <a href="{html_mod.escape(unsubscribe_url, quote=True)}"
         style="color:#7a1f1f">Zrezygnuj z subskrypcji</a>
      &nbsp;(trwale usuniemy Twój adres z bazy).
    </p>
  </td></tr>
</table>
"""


def render_post_html(post: NewsPost, settings: Settings, unsubscribe_url: str) -> str:
    """Full HTML body of a single-post notification."""
    body = sanitize_html(post.body_html)
    lead = sanitize_html(f"<p>{html_mod.escape(post.lead)}</p>") if post.lead else ""
    return f"""<!DOCTYPE html>
<html lang="pl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:24px 12px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:640px;background:#ffffff;border-radius:8px;padding:28px;font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.6">
        <tr><td>
          <h1 style="margin:0 0 8px;font-size:22px;line-height:1.3">{html_mod.escape(post.title)}</h1>
          <p style="margin:0 0 20px;color:#888;font-size:13px">{post.post_date.strftime('%d.%m.%Y')}</p>
          {lead}
          {body}
          <p style="margin:20px 0 0">
            <a href="{html_mod.escape(post.url, quote=True)}"
               style="color:#7a1f1f">Zobacz oryginalny wpis na stronie</a>
          </p>
          {_html_footer(settings, unsubscribe_url)}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_post_text(post: NewsPost, settings: Settings, unsubscribe_url: str) -> str:
    """Plain-text alternative for a single-post notification."""
    text = BeautifulSoup(post.body_html or "", "lxml").get_text("\n", strip=True)
    parts = [post.title, post.post_date.strftime("%d.%m.%Y"), ""]
    if post.lead:
        parts += [post.lead, ""]
    parts += [text, "", f"Oryginalny wpis: {post.url}"]
    return "\n".join(parts) + _text_footer(settings, unsubscribe_url)


# --------------------------------------------------------------------------
# Message building
# --------------------------------------------------------------------------


def _from_header(settings: Settings) -> str:
    return formataddr((settings.sender_name, settings.sender_email))


def build_post_message(
    post: NewsPost, settings: Settings, to_email: str, unsubscribe_url: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = post.title
    msg["From"] = _from_header(settings)
    msg["To"] = to_email
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(render_post_text(post, settings, unsubscribe_url))
    msg.add_alternative(
        render_post_html(post, settings, unsubscribe_url), subtype="html"
    )
    return msg


def build_verification_message(
    email: str, verify_url: str, settings: Settings
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Potwierdz subskrypcje newslettera Aktualnosci"
    msg["From"] = _from_header(settings)
    msg["To"] = email
    msg["Auto-Submitted"] = "auto-generated"
    text = (
        "Witaj!\n\n"
        "Aby potwierdzic subskrypcje newslettera Aktualnosci Szkoly Doktorskiej "
        "AGH, otworz ponizszy link:\n\n"
        f"{verify_url}\n\n"
        "Jesli to nie Ty, zignoruj te wiadomosc -- adres nie zostanie dodany.\n"
        + _text_footer(settings, verify_url)
    )
    html = f"""<!DOCTYPE html>
<html lang="pl"><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.6">
  <p>Witaj!</p>
  <p>Aby potwierdzić subskrypcję newslettera <strong>Aktualności Szkoły Doktorskiej AGH</strong>, kliknij poniższy link:</p>
  <p><a href="{html_mod.escape(verify_url, quote=True)}" style="color:#7a1f1f">Potwierdź subskrypcję</a></p>
  <p style="color:#888;font-size:13px">Jeśli to nie Ty, zignoruj tę wiadomość — adres nie zostanie dodany.</p>
  {_html_footer(settings, verify_url)}
</body></html>"""
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def send_message(settings: Settings, msg: EmailMessage) -> MailResult:
    """Send ``msg`` using the same SSL flow as ``hello.py``."""
    if settings.dry_run:
        return MailResult(False, "dry-run: not sent")

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, context=context
        ) as server:
            server.login(settings.sender_email, settings.app_password)
            server.send_message(msg)
        return MailResult(True, "sent")
    except Exception as exc:  # noqa: BLE001 - report, don't crash the watcher
        logger.exception("SMTP send failed: %s", exc)
        return MailResult(False, str(exc))


def resolve_recipient(settings: Settings, intended: str) -> str:
    """Apply the global test-redirect safety rail, if configured."""
    return settings.test_redirect_email or intended


def send_post_notification(
    post: NewsPost, settings: Settings, to_email: str, unsubscribe_url: str
) -> MailResult:
    recipient = resolve_recipient(settings, to_email)
    msg = build_post_message(post, settings, recipient, unsubscribe_url)
    return send_message(settings, msg)


def send_verification(
    settings: Settings, to_email: str, verify_url: str
) -> MailResult:
    recipient = resolve_recipient(settings, to_email)
    msg = build_verification_message(to_email, verify_url, settings)
    if recipient != to_email:
        del msg["To"]
        msg["To"] = recipient
    return send_message(settings, msg)
