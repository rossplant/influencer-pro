"""Admin password checks and the signed session cookie.

SESSION_SECRET and ADMIN_EMAIL are required. ADMIN_SETUP_TOKEN is required
only until the browser setup creates the admin user. ADMIN_PASSWORD_HASH is
an optional fallback and is not the normal way to set the password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Request

COOKIE_NAME = "plantr_admin"
SELLER_COOKIE = "plantr_seller"
CREATOR_COOKIE = "plantr_creator"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

_login_buckets: dict[str, deque] = defaultdict(deque)
_login_lock = threading.Lock()


_setup_buckets: dict[str, deque] = defaultdict(deque)


def reset_login_limits() -> None:
    with _login_lock:
        _login_buckets.clear()
        _setup_buckets.clear()


def admin_email() -> str:
    return os.getenv("ADMIN_EMAIL", "").strip().lower()


def secrets_configured() -> bool:
    return bool(_secret()) and bool(admin_email())


def setup_token_configured() -> bool:
    return bool(_setup_token())


def admin_enabled(*, user_exists: bool) -> bool:
    """The area stays closed unless a secret, an email, and a way to sign in exist."""
    if not secrets_configured():
        return False
    return user_exists or bool(_password_hash()) or bool(_setup_token())


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        n=SCRYPT_N.bit_length() - 1,
        r=SCRYPT_R,
        p=SCRYPT_P,
        salt=salt.hex(),
        digest=digest.hex(),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a scrypt or bcrypt hash. A bad hash returns False."""
    if stored.startswith(("$2a$", "$2b$", "$2y$")):
        return _verify_bcrypt(password, stored)
    return _verify_scrypt(password, stored)


def emails_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.strip().lower().encode("utf-8")).digest(),
        hashlib.sha256(expected.strip().lower().encode("utf-8")).digest(),
    )


def token_matches(provided: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(_setup_token().encode("utf-8")).digest(),
    )


def passwords_match(password: str, confirmation: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(password.encode("utf-8")).digest(),
        hashlib.sha256(confirmation.encode("utf-8")).digest(),
    )


def credentials_match(email: str, password: str, password_hash: str) -> bool:
    """Compare the account without skipping the hash when the email is wrong."""
    email_ok = emails_match(email, admin_email())
    password_ok = verify_password(password, password_hash)
    return email_ok and password_ok


def login_attempt_allowed(ip: str) -> bool:
    """Five login attempts per IP per 15 minutes. The sixth is rejected."""
    return _attempt_allowed(_login_buckets, ip)


def setup_attempt_allowed(ip: str) -> bool:
    """Five setup attempts per IP per 15 minutes."""
    return _attempt_allowed(_setup_buckets, ip)


def _attempt_allowed(buckets: dict[str, deque], ip: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        bucket = buckets[ip]
        while bucket and now - bucket[0] >= LOGIN_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= LOGIN_LIMIT:
            return False
        bucket.append(now)
        return True


def issue_session(email: str) -> str:
    payload = {
        "email": email.strip().lower(),
        "exp": int(time.time()) + SESSION_SECONDS,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def issue_form_token(purpose: str) -> str:
    payload = {"purpose": purpose, "exp": int(time.time()) + SESSION_SECONDS}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def form_token_ok(token: str, purpose: str) -> bool:
    payload = _read_signed(token)
    if payload is None or payload.get("purpose") != purpose:
        return False
    return True


def issue_role_session(role: str, email: str, subject_id: str, impersonator: Optional[str] = None) -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = {
        "role": role,
        "email": email.strip().lower(),
        "subject_id": subject_id,
        "csrf": csrf,
        "exp": int(time.time()) + SESSION_SECONDS,
    }
    if impersonator:
        payload["impersonator"] = impersonator
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}", csrf


def read_role_session(request: Request, cookie_name: str) -> Optional[dict]:
    payload = _read_signed(request.cookies.get(cookie_name, ""))
    if payload is None or payload.get("role") not in {"seller", "creator"}:
        return None
    if not payload.get("subject_id") or not payload.get("csrf"):
        return None
    return payload


def csrf_matches(session: dict, provided: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(str(session.get("csrf") or "").encode("utf-8")).digest(),
        hashlib.sha256(str(provided or "").encode("utf-8")).digest(),
    )


def session_email(request: Request) -> Optional[str]:
    secret = _secret()
    token = request.cookies.get(COOKIE_NAME, "")
    if not secret or not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
        exp = int(payload["exp"])
        email = str(payload["email"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if exp < int(time.time()) or not email:
        return None
    return email


def locked_environment() -> bool:
    """Production and staging fail closed. A local process without these stays open."""
    env = os.getenv("ENV", "").strip().lower()
    railway = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
    return env in {"production", "staging"} or railway in {"production", "staging"}


def _password_hash() -> str:
    return os.getenv("ADMIN_PASSWORD_HASH", "").strip()


def fallback_password_hash() -> str:
    return _password_hash()


def _setup_token() -> str:
    return os.getenv("ADMIN_SETUP_TOKEN", "").strip()


def _secret() -> str:
    return os.getenv("SESSION_SECRET", "").strip()


def _read_signed(token: str) -> Optional[dict]:
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if exp < int(time.time()):
        return None
    return payload


def _sign(body: str) -> str:
    return hmac.new(_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _verify_scrypt(password: str, stored: str) -> bool:
    try:
        scheme, n_log, r_value, p_value, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            raise ValueError("not scrypt")
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2 ** int(n_log),
            r=int(r_value),
            p=int(p_value),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        dummy = hashlib.scrypt(
            password.encode("utf-8"),
            salt=b"plantr-admin-dummy!",
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=SCRYPT_DKLEN,
        )
        hmac.compare_digest(dummy, b"\x00" * SCRYPT_DKLEN)
        return False


def _verify_bcrypt(password: str, stored: str) -> bool:
    try:
        import bcrypt
    except ImportError:
        return False
    try:
        return bool(bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8")))
    except (ValueError, TypeError):
        return False
