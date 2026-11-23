"""Approved copy, invite-only creators, attribution, and consent."""

from fastapi.testclient import TestClient

import billing_config
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
    assert "Find products you genuinely rate." in creators.text
    assert "Request your invite" in creators.text
    assert "Sell more. Earn more. Matched with care." in creators.text
    assert "Draft" not in creators.text
    assert "sign up free" not in creators.text.lower()
    assert "Sell more through the right Creatrs." in sellers.text
    assert "Apply for a pilot place" in sellers.text
    assert "Foundr number" in creators.text
    assert "48-hour early access is earned at Trendsettr and Headlinr." in creators.text
    assert "Curatr for 48 hours" not in creators.text
    assert "Trendsettr for 7 days" not in creators.text
    assert "underserved" not in creators.text.lower()
    assert "underserved" not in sellers.text.lower()
    assert "untapped" not in (creators.text + sellers.text).lower()
    assert "A$9 a month for 12 months" in creators.text
    assert "A$15 a month" in creators.text
    assert "A$9.50" not in creators.text
    assert "Founding Curator" not in creators.text
    assert "/founding-curators/apply" in creators.text
    assert "A$39.50 a month for 3 months, then A$79" in sellers.text
    assert "A$29 a month" in sellers.text
    assert "Draft for legal review" in privacy.text
    for banned in ("passive income", "guaranteed", "go viral", "unlimited reach", "make $"):
        assert banned not in creators.text.lower()
        assert banned not in sellers.text.lower()


def test_first_touch_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    consent = {"Cookie": "plantr_consent=marketing"}
    first = client.get("/founding-curators?utm_source=tiktok&utm_campaign=founding", headers=consent)
    assert first.status_code == 200
    stored = first.cookies.get("plantr_attr")
    second = client.get(
        "/founding-curators?utm_source=meta",
        headers={"Cookie": f"plantr_consent=marketing; plantr_attr={stored}"},
    )
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
            "paid_service_confirmed": "yes",
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
    tracking.flush_pending()
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
    tracking.flush_pending()
    assert allowed["duplicate"] is False
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


def test_signed_in_consent_revocation_updates_the_account(monkeypatch):
    from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

    _setup(monkeypatch)
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    shop = client.post("/shops", json={"name": "Consent Shop"}).json()
    csrf = client.get("/admin", headers=admin).text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "consent@example.com", "password": "password-pass", "password_confirm": "password-pass"},
        headers=admin,
    )
    login = client.get("/seller")
    token = login.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    signed = client.post(
        "/seller/login",
        data={"email": "consent@example.com", "password": "password-pass", "csrf_token": token},
        follow_redirects=False,
    )
    cookie = {"Cookie": signed.headers["set-cookie"].split(";", 1)[0]}
    revoked = client.post("/track/consent", json={"consent": False}, headers=cookie)
    assert revoked.status_code == 200
    assert revoked.json()["marketing_consent"] is False
    assert db.memory_shops[shop["id"]].marketing_consent is False
    assert any(row.event_name == "consent_revoked" for row in db.memory_events)
    allowed = client.post("/track/consent", json={"consent": True}, headers=cookie)
    assert allowed.json()["marketing_consent"] is True
    assert db.memory_shops[shop["id"]].marketing_consent is True


def test_custom_events_are_mapped_for_meta_and_tiktok():
    approved = billing_config.PLATFORM_EVENTS["application_approved"]
    founding = billing_config.PLATFORM_EVENTS["founding_free_to_paid"]
    registration = billing_config.PLATFORM_EVENTS["complete_registration"]
    assert approved["meta"] == "application_approved"
    assert approved["tiktok"] == "application_approved"
    assert founding["meta"] == "founding_free_to_paid"
    assert founding["tiktok"] == "founding_free_to_paid"
    assert registration["browser"] is False
    assert registration["server"] is True


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


def test_waitlisted_application_still_emits_apply_submit(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("PILOT_CURATOR_SEATS", "0")
    page = client.get("/founding-curators?invite_code=INVITE1")
    token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    event_id = page.text.split('name="event_id" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/founding-curators/apply",
        data={
            "csrf_token": token,
            "name": "Bea",
            "email": "bea@example.com",
            "role": "creator",
            "categories": "home",
            "contact": "@bea",
            "age_confirmed": "yes",
            "paid_service_confirmed": "yes",
            "event_id": event_id,
        },
    )
    assert response.status_code == 200
    assert "waitlist" in response.text.lower()
    assert event_id in response.text
    submit = next(row for row in db.memory_events if row.event_name == "apply_submit")
    assert submit.properties["event_id"] == event_id
    assert submit.properties["invite_code"] == "INVITE1"


def test_pixels_render_only_with_id_and_consent(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("META_PIXEL_ID", "meta-pixel")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "tiktok-pixel")
    client.cookies.clear()
    quiet = client.get("/founding-curators")
    assert "fbq(" not in quiet.text
    assert "ttq.load" not in quiet.text
    allowed = client.get("/founding-curators", headers={"Cookie": "plantr_consent=marketing"})
    assert "fbq('init', 'meta-pixel')" in allowed.text
    assert "ttq.load('tiktok-pixel')" in allowed.text
    assert "fbq('track', \"PageView\"" in allowed.text or "fbq('track', \"PageView\"" in allowed.text
    assert "founding-curators" in allowed.text
    assert "connect.facebook.net" in allowed.text
    assert "analytics.tiktok.com" in allowed.text


def test_platform_cookies_are_captured():
    response = client.get(
        "/pilot-sellers",
        headers={"Cookie": "plantr_consent=marketing; _fbp=fb.1.2.3; _fbc=fb.1.9.clid; _ttp=ttp-1"},
    )
    cookie = response.cookies.get("plantr_attr")
    assert "fbp" in cookie
    assert "ttp-1" in cookie or "ttp" in cookie
    assert "fb.1.9.clid" in cookie


def test_subscribe_uses_account_consent_and_shop(monkeypatch):
    from types import SimpleNamespace

    from models import utcnow
    import stripe_billing

    shop = SimpleNamespace(
        memory_kind="shop",
        id="shop-sub",
        plan_tier="growth",
        plan_status="trial",
        first_paid_at=None,
        marketing_consent=True,
        attribution_first={"utm_source": "tiktok"},
        attribution_last={"fbp": "fb.1.2"},
        signup_code="INV9",
        signup_campaign="seller_pilot",
        created_at=utcnow(),
        is_pilot=True,
    )
    db.memory_shops[shop.id] = shop
    stripe_billing._apply_invoice_paid(
        {
            "id": "in_shop",
            "amount_paid": 7900,
            "currency": "aud",
            "subscription": "sub_shop",
            "billing_reason": "subscription_create",
            "customer": "cus_shop",
            "metadata": {"role": "seller", "subject_id": shop.id, "tier": "growth", "interval": "month"},
        }
    )
    subscribe = next(row for row in db.memory_events if row.event_name == "subscribe")
    founding = next(row for row in db.memory_events if row.event_name == "founding_free_to_paid")
    assert subscribe.properties["plan_tier"] == "growth"
    assert subscribe.properties["period"] == "monthly"
    assert subscribe.properties["invite_code"] == "INV9"
    assert subscribe.properties["utm_first"]["utm_source"] == "tiktok"
    assert subscribe.properties["fbp"] == "fb.1.2"
    assert subscribe.shop_id == shop.id
    assert shop.first_paid_at is not None
    assert founding.properties["event_id"] == "founding:in_shop"
    assert "founding-first:sub_shop" in db.memory_tracked_subjects
    stripe_billing._apply_invoice_paid(
        {
            "id": "in_shop_2",
            "amount_paid": 7900,
            "currency": "aud",
            "subscription": "sub_shop",
            "metadata": {"role": "seller", "subject_id": shop.id, "tier": "growth", "interval": "month"},
        }
    )
    assert [row.event_name for row in db.memory_events if row.event_name == "founding_free_to_paid"] == ["founding_free_to_paid"]


def test_capi_posts_only_when_flag_id_and_token_are_set(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("TRACKING_ENABLED", "1")
    monkeypatch.setenv("META_PIXEL_ID", "meta-pixel")
    tracking.track("apply_submit", event_id="apply:capi", role="creator", consent=True, email="ada@example.com")
    tracking.flush_pending()
    assert calls == []
    monkeypatch.setenv("META_CAPI_TOKEN", "meta-token")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("META_TEST_EVENT_CODE", "TEST123")
    tracking.track("apply_submit", event_id="apply:capi-2", role="creator", consent=True, email="ada@example.com")
    tracking.flush_pending()
    assert calls
    assert "graph.facebook.com" in calls[0][0]
    assert calls[0][1]["json"]["test_event_code"] == "TEST123"
    assert calls[0][1]["json"]["data"][0]["event_id"] == "apply:capi-2"
    assert calls[0][1]["json"]["data"][0]["event_name"] == "Lead"
