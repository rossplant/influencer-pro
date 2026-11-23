"""Admin forgot-password link, single-use reset, and the UAT sign-in redirect."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import db
import main
from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

client = TestClient(main.app)
NEW_PASSWORD = "a-different-password"


def _forgot(monkeypatch, email):
    _setup(monkeypatch)
    page = client.get("/admin/forgot").text
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    return client.post("/admin/forgot", data={"csrf_token": token, "email": email})


def _reset_url():
    body = db.memory_emails[-1]["body"]
    return body.split(" ", 5)[-1].split(". This link")[0].strip()


def test_sign_in_links_to_reset_and_uat(monkeypatch):
    _setup(monkeypatch)
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Forgot password?" in page.text
    assert 'href="/admin/forgot"' in page.text
    assert 'href="/admin/uat"' in page.text
    assert "#C2127F" in page.text
    signed_out = client.get("/admin/uat", follow_redirects=False)
    assert signed_out.status_code == 303
    assert signed_out.headers["location"] == "/admin"


def test_unknown_and_known_emails_get_the_same_message(monkeypatch):
    _setup(monkeypatch)
    page = client.get("/admin/forgot").text
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    missing = client.post("/admin/forgot", data={"csrf_token": token, "email": "nobody@example.com"})
    assert missing.status_code == 200
    assert "If that email is an admin account, a reset link is on its way." in missing.text
    assert db.memory_emails == []
    found = client.post("/admin/forgot", data={"csrf_token": token, "email": EMAIL})
    assert found.status_code == 200
    assert found.text == missing.text
    sent = db.memory_emails[-1]
    assert sent["subject"] == "Reset your Plantr Collab admin password"
    assert sent["to_email"] == EMAIL
    assert NEW_PASSWORD not in sent["body"]
    assert PASSWORD not in sent["body"]
    assert "/admin/reset/" in sent["body"]


def test_reset_replaces_the_password_and_old_sessions(monkeypatch):
    _forgot(monkeypatch, EMAIL)
    signed_in = _cookie_header(_login())
    assert client.get("/admin/api/summary", headers=signed_in).status_code == 200
    url = _reset_url()
    token = url.rsplit("/", 1)[-1]
    page = client.get(f"/admin/reset/{token}")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    short = client.post(
        f"/admin/reset/{token}",
        data={"csrf_token": csrf, "password": "short", "password_confirm": "short"},
        follow_redirects=False,
    )
    assert short.status_code == 422
    mismatch = client.post(
        f"/admin/reset/{token}",
        data={"csrf_token": csrf, "password": NEW_PASSWORD, "password_confirm": "not-the-same-12"},
        follow_redirects=False,
    )
    assert mismatch.status_code == 422
    saved = client.post(
        f"/admin/reset/{token}",
        data={"csrf_token": csrf, "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin"
    assert client.get("/admin/api/summary", headers=signed_in).status_code == 401
    assert _login(password=PASSWORD).status_code == 401
    fresh = _login(password=NEW_PASSWORD)
    assert fresh.status_code == 200
    again = client.post(
        f"/admin/reset/{token}",
        data={"csrf_token": csrf, "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert again.status_code == 422


def test_expired_reset_link_is_rejected(monkeypatch):
    _forgot(monkeypatch, EMAIL)
    url = _reset_url()
    token = url.rsplit("/", 1)[-1]
    db.memory_admin_users[EMAIL].reset_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    page = client.get(f"/admin/reset/{token}")
    assert page.status_code == 422


def test_reset_form_is_rate_limited(monkeypatch):
    _setup(monkeypatch)
    page = client.get("/admin/forgot").text
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    for _ in range(5):
        assert client.post("/admin/forgot", data={"csrf_token": token, "email": "nobody@example.com"}).status_code == 200
    blocked = client.post("/admin/forgot", data={"csrf_token": token, "email": EMAIL})
    assert blocked.status_code == 429
    assert db.memory_emails == []
