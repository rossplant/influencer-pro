"""Admin setup, session cookie, and the locked API."""

import json

import pytest

import db
import main
from test_api import INFLUENCER, PRODUCT, client

PASSWORD = "correct horse battery"
EMAIL = "admin@example.com"


def _configure(monkeypatch, **extra):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("ADMIN_EMAIL", EMAIL)
    monkeypatch.setenv("ADMIN_SETUP_TOKEN", "setup-token-value")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def _setup(monkeypatch, password=PASSWORD):
    _configure(monkeypatch)
    response = client.post(
        "/admin/setup",
        json={"token": "setup-token-value", "password": password, "password_confirm": password},
    )
    assert response.status_code == 200
    return response


def _cookie_header(response) -> dict:
    raw = response.headers["set-cookie"]
    lowered = raw.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "max-age=43200" in lowered
    pair = raw.split(";", 1)[0]
    return {"Cookie": pair}


def _login(password=PASSWORD, email=EMAIL):
    response = client.post("/admin/login", json={"email": email, "password": password})
    return response


def _event_blob() -> str:
    return json.dumps(
        [
            {"name": event.event_name, "properties": event.properties}
            for event in db.memory_events
            if str(event.event_name).startswith("admin_login")
        ]
    )


def test_setup_once_then_410_and_login(monkeypatch):
    created = _setup(monkeypatch)
    assert created.json()["ok"] is True
    page = client.get("/admin/setup")
    assert page.status_code == 410
    assert client.post(
        "/admin/setup",
        json={"token": "setup-token-value", "password": PASSWORD, "password_confirm": PASSWORD},
    ).status_code == 410

    form = client.get("/admin")
    assert form.status_code == 200
    assert 'name="email"' in form.text
    assert 'name="password"' in form.text
    assert 'autocomplete="username"' in form.text
    assert 'autocomplete="current-password"' in form.text

    logged_in = _login()
    assert logged_in.status_code == 200
    cookie = _cookie_header(logged_in)
    assert client.get("/admin/api/summary", headers=cookie).status_code == 200
    assert client.get("/admin/api/summary").status_code == 401
    blob = _event_blob()
    assert "admin_login_succeeded" in blob
    assert PASSWORD not in blob
    assert "password" not in blob


def test_setup_rejects_a_wrong_token(monkeypatch):
    _configure(monkeypatch)
    wrong = client.post(
        "/admin/setup",
        json={"token": "nope", "password": PASSWORD, "password_confirm": PASSWORD},
    )
    assert wrong.status_code == 401
    assert admin_data_missing()
    page = client.get("/admin/setup")
    assert page.status_code == 200
    assert 'name="token"' in page.text
    assert 'name="password"' in page.text
    assert 'name="password_confirm"' in page.text
    assert 'autocomplete="new-password"' in page.text
    assert EMAIL in page.text


def admin_data_missing() -> bool:
    return db.memory_admin_users.get(EMAIL) is None


def test_setup_and_login_are_disabled_without_secrets(monkeypatch):
    assert client.get("/admin").status_code == 503
    assert client.get("/admin/setup").status_code == 503
    assert client.post("/admin/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 503
    _configure(monkeypatch)
    monkeypatch.delenv("SESSION_SECRET")
    assert client.get("/admin").status_code == 503
    assert client.post("/admin/setup", json={"token": "setup-token-value", "password": PASSWORD, "password_confirm": PASSWORD}).status_code == 503


def test_login_failure_and_rate_limit(monkeypatch):
    _setup(monkeypatch)
    for _ in range(5):
        failed = _login(password="not-the-password")
        assert failed.status_code == 401
    blocked = _login(password="not-the-password")
    assert blocked.status_code == 429
    blob = _event_blob()
    assert "admin_login_failed" in blob
    assert "not-the-password" not in blob
    assert PASSWORD not in blob


def test_production_requires_a_key_or_session_and_onboarding_stays_public(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("API_KEYS", raising=False)
    assert client.get("/").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.post("/onboard-influencer", json=INFLUENCER).status_code == 200
    assert client.post("/enrich", json=PRODUCT).status_code == 401

    monkeypatch.setenv("API_KEYS", "secret-key")
    assert client.post("/enrich", json=PRODUCT).status_code == 401
    assert client.post("/enrich", json=PRODUCT, headers={"X-API-Key": "secret-key"}).status_code == 200

    monkeypatch.delenv("API_KEYS")
    cookie = _cookie_header(_login())
    assert client.post("/enrich", json=PRODUCT, headers=cookie).status_code == 200
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.delenv("ENV")
    assert client.post("/shops", json={"name": "Closed"}).status_code == 401
    assert client.post("/shops", json={"name": "Open"}, headers=cookie).status_code == 200


def test_hash_password_cli_matches_login(monkeypatch):
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "tools.hash_password"],
        input=PASSWORD + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    hashed = completed.stdout.strip()
    assert hashed.startswith("scrypt$")
    _configure(monkeypatch, ADMIN_PASSWORD_HASH=hashed)
    monkeypatch.delenv("ADMIN_SETUP_TOKEN")
    logged_in = _login()
    assert logged_in.status_code == 200


def test_admin_user_is_stored_in_the_database(tmp_path, monkeypatch):
    from tests.test_persistence import use_database

    use_database(tmp_path, monkeypatch)
    _setup(monkeypatch)
    _login()
    with db.session_scope() as session:
        from models import AdminUser
        from sqlalchemy import select

        user = session.scalar(select(AdminUser))
        assert user.email == EMAIL
        assert user.password_hash.startswith("scrypt$")
        assert PASSWORD not in user.password_hash
        assert user.last_login_at is not None
        assert "admin_users" in set(__import__("sqlalchemy").inspect(db.engine).get_table_names())


def test_staging_test_admin_is_ignored_in_production(monkeypatch):
    import admin_data
    import auth

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("STAGING_TEST_ADMIN_EMAIL", "stage-admin@example.com")
    monkeypatch.setenv("STAGING_TEST_ADMIN_PASSWORD", "a-long-password-value")
    admin_data.ensure_staging_test_admin()
    assert admin_data.find_admin("stage-admin@example.com") is None
    monkeypatch.setenv("ENV", "staging")
    admin_data.ensure_staging_test_admin()
    user = admin_data.find_admin("stage-admin@example.com")
    assert user is not None
    assert user.password_hash.startswith("scrypt$")
    assert "a-long-password-value" not in user.password_hash
    assert auth.verify_password("a-long-password-value", user.password_hash)
    monkeypatch.setenv("STAGING_TEST_ADMIN_PASSWORD", "another-long-password")
    admin_data.ensure_staging_test_admin()
    updated = admin_data.find_admin("stage-admin@example.com")
    assert auth.verify_password("another-long-password", updated.password_hash)
