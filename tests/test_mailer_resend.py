"""Resend requests identify the app, and a failed send is not treated as delivered."""

import io
import json
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
        captured["body"] = json.loads(request.data.decode())
        return _Body(b'{"id":"re_abc"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = mailer.ResendMailer(api_key="secret-key", sender="Plantr <noreply@example.com>").send(
        to="person@example.com", subject="Hello", text="the message body", html="<p>Open the supplier invite</p>"
    )
    assert result == {"status": mailer.SENT, "id": "re_abc"}
    assert captured["ua"] == f"PlantrCollab/{APP_VERSION}"
    assert captured["type"] == "application/json"
    assert captured["body"]["html"] == "<p>Open the supplier invite</p>"
    assert "attachments" not in captured["body"]
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


def test_role_invites_use_the_same_template_fields_and_one_button():
    import emailer

    for kind, label in (("supplier_invite", "Open the supplier invite"), ("creatr_invite", "Open the Creatr invite")):
        spec = emailer.INVITE_COPY[kind]
        assert set(spec) == {"subject", "preheader", "headline", "body", "buttons", "footer"}
        assert len(spec["buttons"]) == 1
        url = "https://influencer-pro-staging.up.railway.app/uat/invite/s/abc"
        subject, text, html_body = emailer.render_invite(kind, url=url)
        words = spec["preheader"].split() + spec["headline"].split() + spec["body"].split() + spec["buttons"][0][0].split() + spec["footer"].split()
        assert len(words) < 120
        assert subject == spec["subject"]
        assert html_body.count("<a ") == 1
        assert f'href="{url}"' in html_body
        assert label in html_body
        assert f"<{url}>" in text.splitlines()
        assert "Stoneware" not in html_body
    assert "uat_invite" not in emailer.INVITE_COPY


def test_uat_invite_email_is_branded_html_and_plain_links(tmp_path):
    import emailer

    artifact_dir = __import__("pathlib").Path("/opt/cursor/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cases = (
        ("supplier_invite", "https://influencer-pro-staging.up.railway.app/uat/invite/s/abc", "supplier-invite-email.html"),
        ("creatr_invite", "https://influencer-pro-staging.up.railway.app/uat/invite/c/def", "creatr-invite-email.html"),
    )
    for kind, url, filename in cases:
        _subject, text, html_body = emailer.render_invite(kind, url=url)
        assert html_body.count("<a ") == 1
        assert f'href="{url}"' in html_body
        assert "#C2127F" in html_body
        assert f"<{url}>" in text.splitlines()
        blob = (text + html_body).lower()
        for banned in ("stoneware", "napkin", "oak board", "attachment", "content-disposition"):
            assert banned not in blob
        (tmp_path / filename).write_text(html_body, encoding="utf-8")
        (artifact_dir / filename).write_text(html_body, encoding="utf-8")
