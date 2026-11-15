"""Approved copy, invite-only creators, attribution, and consent."""

from fastapi.testclient import TestClient

import db
import main
import tracking

client = TestClient(main.app)


def test_landing_copy_is_approved_and_privacy_stays_draft(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    creators = client.get("/founding-curators?utm_source=tiktok")
    sellers = client.get("/pilot-sellers")
    privacy = client.get("/privacy")
    assert creators.status_code == 200
    assert "Earn from products your audience will actually buy." in creators.text
    assert "Request your invite" in creators.text
    assert "Sell more. Earn more. Matched with care." in creators.text
    assert "Draft" not in creators.text
    assert "sign up free" not in creators.text.lower()
    assert "Sell more through the right creators." in sellers.text
    assert "Apply for a pilot place" in sellers.text
    assert "Draft for legal review" in privacy.text
    for banned in ("passive income", "guaranteed", "go viral", "unlimited reach", "make $"):
        assert banned not in creators.text.lower()
        assert banned not in sellers.text.lower()


def test_first_touch_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    first = client.get("/founding-curators?utm_source=tiktok&utm_campaign=founding")
    assert first.status_code == 200
    second = client.get("/founding-curators?utm_source=meta")
    cookie = second.cookies.get("plantr_attr") or first.cookies.get("plantr_attr")
    assert cookie
    assert "tiktok" in cookie
    assert "meta" in cookie
    # The stored first touch stays tiktok. Last touch may be meta.
    assert cookie.index("tiktok") < cookie.index("meta")


def test_public_creator_login_requires_an_invite():
    response = client.post(
        "/onboard-influencer",
        json={
            "handle": "@open",
            "areas_of_interest": ["home"],
            "preferred_promotion_styles": ["educational"],
            "email": "open@example.com",
            "password": "correct horse battery",
            "age_confirmed": True,
        },
    )
    assert response.status_code == 422
    assert "invite-only" in response.json()["detail"]


def test_approval_numbers_founding_curators_and_logs_internally(monkeypatch):
    from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

    _setup(monkeypatch)
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    page = client.get("/founding-curators", headers={})
    token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    applied = client.post(
        "/founding-curators/apply",
        data={
            "csrf_token": token,
            "name": "Ada",
            "email": "ada@example.com",
            "role": "creator",
            "categories": "home",
            "contact": "@ada",
            "age_confirmed": "yes",
            "event_id": "form-1",
        },
    )
    assert applied.status_code == 200
    application_id = next(row.id for row in db.memory_applications)
    approved = client.post(f"/admin/api/applications/{application_id}/approve", headers=admin)
    assert approved.status_code == 200
    body = approved.json()
    assert body["founding_number"] == 1
    assert body["event_name"] == "application_approved"
    names = [row.event_name for row in db.memory_events]
    assert "application_approved" in names
    assert "apply_submit" in names


def test_platform_send_requires_consent_and_stays_off_by_default(monkeypatch):
    sent = []
    monkeypatch.setattr(tracking, "transport", lambda payload: sent.append(payload))
    monkeypatch.setenv("TRACKING_ENABLED", "1")
    quiet = tracking.track(
        "apply_submit",
        event_id="apply:one",
        role="creator",
        email="ada@example.com",
        consent=False,
    )
    assert quiet["sent"] is False
    assert sent == []
    logged = next(row for row in db.memory_events if row.properties.get("event_id") == "apply:one")
    assert "email" not in logged.properties
    assert "ada@example.com" not in str(logged.properties)
    allowed = tracking.track(
        "apply_submit",
        event_id="apply:two",
        role="creator",
        email="ada@example.com",
        consent=True,
    )
    assert allowed["sent"] is True
    assert sent[0]["hashed_email"] != "ada@example.com"
    assert "ada@example.com" not in str(sent[0])
    monkeypatch.setenv("ENV", "production")
    sent.clear()
    blocked = tracking.track(
        "apply_submit",
        event_id="apply:three",
        role="creator",
        email="ada@example.com",
        consent=True,
    )
    assert blocked["sent"] is False
    assert sent == []


def test_subscribe_replay_does_not_double_fire():
    kwargs = dict(
        role="creator",
        tier="curator",
        period="month",
        value=19,
        currency="aud",
        subject_key="sub-first:sub_9",
    )
    first = tracking.track("subscribe", event_id="sub:in_9", **kwargs)
    second = tracking.track("subscribe", event_id="sub:in_9", **kwargs)
    renewal = tracking.track("subscribe", event_id="sub:in_10", **kwargs)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert renewal["duplicate"] is True
    assert [row.event_name for row in db.memory_events if row.event_name == "subscribe"] == ["subscribe"]
