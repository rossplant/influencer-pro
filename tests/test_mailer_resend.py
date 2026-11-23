"""Resend requests identify the app, and a failed send is not treated as delivered."""

import io
import logging
import urllib.error
import urllib.request

import mailer
from main import APP_VERSION


class _Body:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_resend_request_sets_a_user_agent_and_json_content_type(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=10):
        captured["ua"] = request.get_header("User-agent")
        captured["type"] = request.get_header("Content-type")
        return _Body(b'{"id":"re_abc"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = mailer.ResendMailer(api_key="secret-key", sender="Plantr <noreply@example.com>").send(
        to="person@example.com", subject="Hello", text="the message body"
    )
    assert result == {"status": mailer.SENT, "id": "re_abc"}
    assert captured["ua"] == f"PlantrCollab/{APP_VERSION}"
    assert captured["type"] == "application/json"
    assert "secret-key" not in captured["ua"]


def test_resend_failure_logs_status_and_name_not_the_key_or_body(monkeypatch, caplog):
    def fake_urlopen(request, timeout=10):
        raise urllib.error.HTTPError(
            mailer.RESEND_URL,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"name":"security_error","message":"browser_signature_banned"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="plantr.mailer"):
        result = mailer.ResendMailer(api_key="secret-key", sender="Plantr <noreply@example.com>").send(
            to="person@example.com", subject="Hello", text="the message body"
        )
    assert result["status"] == mailer.FAILED
    assert result["id"] is None
    assert result["reason"] == "HTTP 403 security_error"
    assert "HTTP 403" in caplog.text
    assert "security_error" in caplog.text
    assert "browser_signature_banned" in caplog.text
    assert "secret-key" not in caplog.text
    assert "the message body" not in caplog.text
