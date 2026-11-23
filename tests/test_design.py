"""Shared visual system and HTML sign-in errors."""

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
STYLESHEET = "/assets/plantr.css?v=0.27.2"


def test_portal_pages_share_the_stylesheet_and_wordmark(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    pages = [
        "/pilot-sellers",
        "/founding-curators",
        "/privacy",
        "/seller",
        "/creator",
    ]
    for path in pages:
        page = client.get(path)
        assert page.status_code == 200, path
        assert STYLESHEET in page.text
        assert 'class="logo"' in page.text
        assert "Plantr" in page.text
        assert 'name="viewport"' in page.text
    admin = open("admin.html", encoding="utf-8").read()
    assert STYLESHEET in admin
    assert 'class="logo"' in admin
    css = client.get("/assets/plantr.css")
    assert css.status_code == 200
    assert "#C2127F" in css.text
    assert "min-height:44px" in css.text
    assert "overflow-x:hidden" in css.text
    assert client.get("/assets/fonts/inter.woff2").status_code == 200


def test_login_errors_are_html_not_json(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    page = client.get("/seller")
    token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    failed = client.post(
        "/seller/login",
        data={"email": "nobody@example.com", "password": "not-the-password", "csrf_token": token},
    )
    assert failed.status_code == 401
    assert "text/html" in failed.headers["content-type"]
    assert "That email or password is not right. Try again." in failed.text
    assert "Show password" in failed.text
    assert 'autocapitalize="none"' in failed.text
    assert '{"detail"' not in failed.text
    for _ in range(5):
        token = client.get("/seller").text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        client.post(
            "/seller/login",
            data={"email": "nobody@example.com", "password": "not-the-password", "csrf_token": token},
        )
    token = client.get("/seller").text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    blocked = client.post(
        "/seller/login",
        data={"email": "nobody@example.com", "password": "not-the-password", "csrf_token": token},
    )
    assert blocked.status_code == 429
    assert "Too many sign-in attempts. Try again in 15 minutes." in blocked.text
    assert "text/html" in blocked.headers["content-type"]
