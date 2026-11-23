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


def test_approved_invites_fill_every_placeholder(monkeypatch, tmp_path):
    import emailer

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    artifact_dir = __import__("pathlib").Path("/opt/cursor/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cases = (
        (
            "supplier_invite",
            "https://influencer-pro-staging.up.railway.app/uat/invite/s/abc",
            "You've been selected for Plantr Collab",
            "Hand-picked Creatrs, matched to your products. No cut of your sales.",
            "Set up your catalogue",
            "supplier-invite-email.html",
        ),
        (
            "creatr_invite",
            "https://influencer-pro-staging.up.railway.app/uat/invite/c/def",
            "Your Plantr Collab invite",
            "Two months on us, and you start at Influencr today.",
            "Set up my Creatr profile",
            "creatr-invite-email.html",
        ),
    )
    for kind, url, subject, preheader, button, filename in cases:
        unsubscribe = emailer.invite_opt_out_url("https://influencer-pro-staging.up.railway.app", "person@example.com")
        rendered_subject, text, html_body = emailer.render_invite(
            kind, url=url, brand_name="Homeware supplier", unsubscribe_url=unsubscribe
        )
        assert rendered_subject == subject
        assert emailer.invite_preheader(kind) == preheader
        assert preheader in html_body
        assert "{{" not in html_body and "{{" not in text
        assert f'class="btn-link" href="{url}"' in html_body
        assert f'href="{url}"' in html_body
        assert html_body.count(f'href="{url}"') >= 2
        assert f">{url}</a>" in html_body
        assert button in html_body
        assert url in text.splitlines()
        assert "Hi there," in html_body
        assert "there, your Plantr Collab invite" not in rendered_subject
        assert "Test environment" in html_body
        assert "Test environment" in text
        assert "#C2127F" in html_body
        assert unsubscribe in html_body and unsubscribe in text
        assert "creator" not in html_body.lower()
        (tmp_path / filename).write_text(html_body, encoding="utf-8")
        (artifact_dir / filename).write_text(html_body, encoding="utf-8")
    monkeypatch.setenv("ENV", "production")
    _subject, text, html_body = emailer.render_invite(
        "supplier_invite",
        url="https://example.com/uat/invite/s/abc",
        brand_name="Homeware supplier",
        unsubscribe_url="https://example.com/invite/opt-out/token",
    )
    assert "Test environment" not in html_body
    assert "Test environment" not in text
    named_subject, named_text, named_html = emailer.render_invite(
        "creatr_invite",
        url="https://example.com/uat/invite/c/def",
        first_name="Ada",
        brand_name="Oak & Co",
        unsubscribe_url="https://example.com/invite/opt-out/token",
        show_banner=False,
    )
    assert named_subject == "Ada, your Plantr Collab invite"
    assert "Hi Ada," in named_html
    assert "Hi Ada," in named_text
    assert "there, your" not in named_subject


def test_invite_from_and_reply_to_go_on_the_resend_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=10):
        captured["body"] = json.loads(request.data.decode())
        return _Body(b'{"id":"re_from"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("RESEND_API_KEY", "secret-key")
    monkeypatch.setenv("RESEND_FROM", "noreply@send.plantr-collab.com")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    import emailer

    result = emailer.send(
        "supplier_invite",
        "person@example.com",
        url="https://influencer-pro-staging.up.railway.app/uat/invite/s/abc",
        brand_name="Homeware supplier",
        unsubscribe_url=emailer.invite_opt_out_url("https://influencer-pro-staging.up.railway.app", "person@example.com"),
    )
    assert result["provider_id"] == "re_from"
    assert captured["body"]["from"] == "Ross at Plantr Collab <noreply@send.plantr-collab.com>"
    assert captured["body"]["reply_to"] == "hello@plantr-collab.com"
    assert "attachments" not in captured["body"]


def test_unsubscribe_link_records_the_opt_out(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    import emailer

    url = emailer.invite_opt_out_url("https://example.com", "Person@Example.com")
    token = url.rsplit("/", 1)[1]
    page = TestClient(main.app).get(f"/invite/opt-out/{token}", headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert "opted out" in page.text
    assert emailer.invite_opted_out("person@example.com") is True
    assert emailer.invite_opted_out("other@example.com") is False
