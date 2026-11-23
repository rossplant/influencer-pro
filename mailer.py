"""Mail sending interface with a Resend adapter.

Transactional mail only. RESEND_API_KEY and RESEND_FROM turn sending on.
EMAIL_FROM is accepted only when RESEND_FROM is unset. There is no reply-to.
When the key or From address is missing the adapter sends nothing, logs a
warning, and returns status "no_provider" so callers can retry later.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional, Protocol

logger = logging.getLogger("plantr.mailer")

RESEND_URL = "https://api.resend.com/emails"
NO_PROVIDER = "no_provider"
SENT = "sent"
FAILED = "failed"

_warned = False


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, text: str, idempotency_key: Optional[str] = None) -> dict: ...


class ResendMailer:
    def __init__(self, api_key: str = "", sender: str = "", reply_to: str = ""):
        self.api_key = api_key
        self.sender = sender
        self.reply_to = reply_to

    @classmethod
    def from_env(cls) -> "ResendMailer":
        return cls(
            api_key=os.getenv("RESEND_API_KEY", "").strip(),
            sender=os.getenv("RESEND_FROM", "").strip() or os.getenv("EMAIL_FROM", "").strip(),
            reply_to="",
        )

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.sender)

    def send(self, *, to: str, subject: str, text: str, idempotency_key: Optional[str] = None) -> dict:
        global _warned
        if not self.ready:
            if not _warned:
                missing = "RESEND_API_KEY" if not self.api_key else "RESEND_FROM"
                logger.warning("%s is not set. Email is logged only and NOTHING IS SENT.", missing)
                _warned = True
            return {"status": NO_PROVIDER, "id": None}
        payload = {"from": self.sender, "to": [to], "subject": subject, "text": text}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": _user_agent(),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:256]
        request = urllib.request.Request(RESEND_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            name, message = _resend_error(exc)
            logger.warning("Resend send failed: HTTP %s %s %s", exc.code, name, message)
            reason = f"HTTP {exc.code}"
            if name:
                reason = f"{reason} {name}"[:80]
            return {"status": FAILED, "id": None, "reason": reason}
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("Resend send failed: %s", type(exc).__name__)
            return {"status": FAILED, "id": None, "reason": type(exc).__name__}
        return {"status": SENT, "id": body.get("id")}


def default() -> Mailer:
    return ResendMailer.from_env()


def _user_agent() -> str:
    try:
        from main import APP_VERSION
    except Exception:
        return "PlantrCollab/dev"
    return f"PlantrCollab/{APP_VERSION}"


def _resend_error(exc: urllib.error.HTTPError) -> tuple[str, str]:
    raw = b""
    try:
        raw = exc.read() or b""
    except Exception:
        raw = b""
    try:
        parsed = json.loads(raw.decode() or "{}")
    except (UnicodeDecodeError, ValueError):
        return "", ""
    if not isinstance(parsed, dict):
        return "", ""
    name = str(parsed.get("name") or "")[:80]
    message = str(parsed.get("message") or "")[:160]
    return name, message
