"""Stripe Billing with the Stripe client mocked. No live keys and no network."""

import json

from fastapi.testclient import TestClient

import db
import main
import stripe_billing
import tracking

client = TestClient(main.app)

INFLUENCER = {
    "handle": "@creator",
    "areas_of_interest": ["alpha"],
    "preferred_promotion_styles": ["educational"],
    "availability": "open",
}


class _Obj(dict):
    def __getattr__(self, name):
        return self.get(name)


class FakeCheckout:
    def __init__(self):
        self.created = []

    def create(self, payload):
        self.created.append(payload)
        return _Obj(id="cs_test", url="https://checkout.stripe.test/cs_test")


class FakePortal:
    def __init__(self):
        self.created = []

    def create(self, payload):
        self.created.append(payload)
        return _Obj(id="bps_test", url="https://billing.stripe.test/portal")


class FakePrices:
    def list(self, payload):
        return _Obj(data=[_Obj(id="price_test", lookup_key=payload["lookup_keys"][0])])


class FakeCustomers:
    def __init__(self):
        self.created = []

    def create(self, payload):
        self.created.append(payload)
        return _Obj(id="cus_test")


class FakeSubscriptions:
    def __init__(self):
        self.updated = []

    def update(self, subscription_id, payload):
        self.updated.append((subscription_id, payload))
        return _Obj(id=subscription_id)


class FakePromotionCodes:
    def list(self, params):
        code = str(params.get("code") or "")
        if not params.get("active"):
            return _Obj(data=[], has_more=False)
        known = {
            "FOUNDING50": _Obj(id="promo_founding", metadata={"campaign": "founding_curators"}, coupon="founding_curator_50_12mo"),
            "PILOTGROWTH": _Obj(id="promo_pilot", metadata={"campaign": "seller_pilot"}, coupon="seller_growth_50_3mo"),
        }
        match = known.get(code.upper())
        return _Obj(data=[match] if match else [], has_more=False)


class FakeStripe:
    def __init__(self):
        self.checkout = _Obj(sessions=FakeCheckout())
        self.billing_portal = _Obj(sessions=FakePortal())
        self.prices = FakePrices()
        self.customers = FakeCustomers()
        self.subscriptions = FakeSubscriptions()
        self.promotion_codes = FakePromotionCodes()


def _admin(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s" * 16)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "unused")
    from tests.test_admin import PASSWORD, _cookie_header, _login

    monkeypatch.setenv("ADMIN_SETUP_TOKEN", "")
    return _cookie_header(_login(password=PASSWORD))


def test_billing_page_when_stripe_is_unset(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    page = client.get("/creator/billing")
    assert page.status_code == 200
    assert "Sign in" in page.text
    assert client.get("/").json()["version"] == "0.16.1"


def test_checkout_uses_lookup_key_and_refuses_live_keys(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s" * 16)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
    assert stripe_billing.configured() is False
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://pay.example")
    fake = FakeStripe()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

    _setup(monkeypatch)
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    shop = client.post("/shops", json={"name": "Billed"}).json()
    csrf = client.get("/admin", headers=admin).text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "bill@example.com", "password": "password-pass", "password_confirm": "password-pass"},
        headers=admin,
    )
    login = client.get("/seller")
    token = login.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    signed = client.post(
        "/seller/login",
        data={"email": "bill@example.com", "password": "password-pass", "csrf_token": token},
        follow_redirects=False,
    )
    cookie = {"Cookie": signed.headers["set-cookie"].split(";", 1)[0]}
    billing = client.get("/seller/billing", headers=cookie)
    token = billing.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/seller/billing/checkout",
        data={"csrf_token": token, "tier": "growth", "interval": "year", "promo_code": "PILOTGROWTH"},
        headers=cookie,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://checkout.stripe.test/cs_test"
    created = fake.checkout.sessions.created[0]
    assert created["mode"] == "subscription"
    assert created["line_items"][0]["price"] == "price_test"
    assert created["metadata"]["tier"] == "growth"
    assert created["metadata"]["promo_code"] == "PILOTGROWTH"
    assert created["metadata"]["campaign"] == "seller_pilot"
    assert created["discounts"] == [{"promotion_code": "promo_pilot"}]
    assert "allow_promotion_codes" not in created
    assert created["success_url"].startswith("https://pay.example/seller/billing")
    assert created["cancel_url"].startswith("https://pay.example/seller/billing")
    assert "sk_" not in json.dumps(created)
    monkeypatch.delenv("PUBLIC_BASE_URL")
    wrong = client.post(
        "/seller/billing/checkout",
        data={"csrf_token": token, "tier": "growth", "interval": "month", "promo_code": "FOUNDING50"},
        headers=cookie,
        follow_redirects=False,
    )
    assert wrong.status_code == 422
    assert "does not apply" in wrong.json()["detail"]
    unknown = client.post(
        "/seller/billing/checkout",
        data={"csrf_token": token, "tier": "growth", "interval": "month", "promo_code": "NOPE"},
        headers=cookie,
        follow_redirects=False,
    )
    assert unknown.status_code == 422
    assert "not recognised" in unknown.json()["detail"]
    open_codes = client.post(
        "/seller/billing/checkout",
        data={"csrf_token": token, "tier": "growth", "interval": "month"},
        headers={"Cookie": cookie["Cookie"], "X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert open_codes.status_code == 303
    opened = fake.checkout.sessions.created[-1]
    assert opened["allow_promotion_codes"] is True
    assert "discounts" not in opened
    assert opened["success_url"].startswith("https://testserver/seller/billing")


def test_creator_checkout_resolves_founding_code(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    monkeypatch.setattr(stripe_billing, "_portal_email", lambda role, subject_id: "creator@example.com")
    url = stripe_billing.checkout(
        role="creator",
        subject_id="creator-1",
        tier="curator",
        interval="month",
        success_url="https://pay.example/creator/billing",
        cancel_url="https://pay.example/creator/billing",
        promo_code="FOUNDING50",
    )
    assert url == "https://checkout.stripe.test/cs_test"
    created = fake.checkout.sessions.created[0]
    assert created["discounts"] == [{"promotion_code": "promo_founding"}]
    assert "allow_promotion_codes" not in created


def test_subscribe_webhook_is_idempotent(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
    invoice = {
        "id": "in_1",
        "amount_paid": 79000,
        "currency": "aud",
        "subscription": "sub_1",
        "billing_reason": "subscription_create",
        "customer": "cus_1",
        "metadata": {"role": "seller", "subject_id": "shop-1", "tier": "growth", "interval": "year"},
    }
    event = {"id": "evt_1", "type": "invoice.paid", "data": {"object": invoice}}

    def fake_event(payload, signature, secret):
        return event

    import stripe

    monkeypatch.setattr(stripe.Webhook, "construct_event", fake_event)
    calls = []
    original = tracking.track

    def spy(*args, **kwargs):
        calls.append(kwargs.get("event_id"))
        return original(*args, **kwargs)

    monkeypatch.setattr(tracking, "track", spy)
    monkeypatch.setattr(stripe_billing, "secret_key", lambda: "sk_test_placeholder")
    first = client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "good"})
    second = client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["sub:in_1"]
    names = [row.event_name for row in db.memory_events if row.event_name == "subscribe"]
    assert names == ["subscribe"]
    stored = next(row for row in db.memory_events if row.event_name == "subscribe")
    assert stored.properties["value"] == 790.0
    assert stored.properties["currency"] == "aud"
    assert stored.properties["period"] == "annual"


def test_webhook_survives_a_platform_send_failure(monkeypatch):
    from types import SimpleNamespace

    from models import utcnow

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
    monkeypatch.setenv("TRACKING_ENABLED", "1")
    shop = SimpleNamespace(
        memory_kind="shop",
        id="shop-capi",
        plan_tier="growth",
        plan_status="trial",
        first_paid_at=None,
        marketing_consent=True,
        attribution_first={"utm_source": "tiktok"},
        attribution_last={},
        signup_code="INV",
        signup_campaign="seller_pilot",
        created_at=utcnow(),
        is_pilot=True,
    )
    db.memory_shops[shop.id] = shop

    def explode(_payload):
        raise RuntimeError("capi down")

    monkeypatch.setattr(tracking, "transport", explode)
    invoice = {
        "id": "in_fail",
        "amount_paid": 7900,
        "currency": "aud",
        "subscription": "sub_fail",
        "customer": "cus_fail",
        "metadata": {"role": "seller", "subject_id": shop.id, "tier": "growth", "interval": "month"},
    }

    def fake_event(payload, signature, secret):
        return {"id": "evt_fail", "type": "invoice.paid", "data": {"object": invoice}}

    import stripe

    monkeypatch.setattr(stripe.Webhook, "construct_event", fake_event)
    response = client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert response.status_code == 200
    assert any(row.event_name == "founding_free_to_paid" for row in db.memory_events)
    assert db.memory_tracking_retries
