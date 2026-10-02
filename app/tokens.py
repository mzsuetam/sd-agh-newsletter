"""Signed tokens for e-mail verification.

Unsubscribe links use the random token stored in the database instead (so they
are revocable the moment the row is hard-deleted). Verification links are
stateless and signed with ``itsdangerous``, carrying an expiry and a purpose
salt; single-use is enforced by the ``pending -> confirmed`` transition.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_VERIFY_SALT = "email-verification-v1"


class TokenError(Exception):
    """Raised when a token is invalid or expired."""


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret_key, salt=_VERIFY_SALT)


def make_verify_token(secret_key: str, email: str) -> str:
    return _serializer(secret_key).dumps({"email": email.lower()})


def read_verify_token(secret_key: str, token: str, max_age: int) -> str:
    """Return the e-mail encoded in ``token``, or raise :class:`TokenError`."""
    try:
        data = _serializer(secret_key).loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise TokenError("expired") from exc
    except BadSignature as exc:
        raise TokenError("invalid") from exc

    if not isinstance(data, dict) or "email" not in data:
        raise TokenError("invalid")
    return str(data["email"])
