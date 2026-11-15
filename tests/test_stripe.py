"""Stripe phase 1 with the SDK mocked. No network calls and no real keys."""

import json

import stripe

import db
import stripe_billing
from test_api import INFLUENCER, client
from tests.test_admin import _cookie_header, _login, _setup
from tests.test_portal import CREATOR_PASSWORD, PASSWORD, _cookie, _token


class FakeAccounts:
    def __init__(self):
        self.created = []
        self.retrieved = []

    def create(self, params):
        self.created.append(params)
        return {"id": "acct_test_123"}

    def retrieve(self, account_id):
        self.retrieved.append(account_id)
        return {
            "id": account_id,
            "configuration": {
                "recipient": {
                    "capabilities": {
                        "stripe_balance": {"stripe_transfers": {"status": "active"}}
                    }
                }
            },
            "requirements": {"entries": []},
        }


class FakeLinks:
    def __init__(self):
        self.created = []

    def create(self, params):
        self.created.append(params)
        return {"url": "https://connect.stripe.test/setup/acct_test_123"}


class FakeCustomers:
    def __init__(self):
        self.created = []

    def create(self, params):
        self.created.append(params)
        return {"id": "cus_test_123"}


class FakeStripe:
    def __init__(self):
        self.accounts = FakeAccounts()
        self.links = FakeLinks()
        self.customers = FakeCustomers()
        self.v2 = type("V2", (), {})()
        self.v2.core = type("Core", (), {"accounts": self.accounts, "account_links": self.links})()


def _install_fake(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    return fake


def _seller_and_creator(monkeypatch):
    _setup(monkeypatch)
    admin = _cookie_header(_login())
    page = client.get("/admin", headers=admin)
    csrf = page.text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    shop = client.post("/shops", json={"name": "Billed"}).json()
    creator = client.post(
        "/onboard-influencer",
        json={**INFLUENCER, "handle": "@paid", "email": "paid@example.com", "password": CREATOR_PASSWORD},
    ).json()
    assert client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "bill@example.com", "password": PASSWORD, "password_confirm": PASSWORD},
        headers=admin,
    ).status_code == 200
    seller_page = client.get("/seller")
    seller = client.post(
        "/seller/login",
        data={"email": "bill@example.com", "password": PASSWORD, "csrf_token": _token(seller_page.text)},
        follow_redirects=False,
    )
    creator_page = client.get("/creator")
    signed = client.post(
        "/creator/login",
        data={"email": "paid@example.com", "password": CREATOR_PASSWORD, "csrf_token": _token(creator_page.text)},
        follow_redirects=False,
    )
    return shop, creator, _cookie(seller, "plantr_seller"), _cookie(signed, "plantr_creator"), admin


def test_stripe_features_say_not_configured_without_keys(monkeypatch):
    _shop, _creator, seller, creator_cookie, admin = _seller_and_creator(monkeypatch)
    billing = client.get("/seller/billing", headers=seller)
    assert billing.status_code == 200
    assert "Stripe is not configured" in billing.text
    payouts = client.get("/creator/payouts", headers=creator_cookie)
    assert payouts.status_code == 200
    assert "Stripe is not configured" in payouts.text
    assert client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t"}).status_code == 503
    assert client.get("/").json()["version"] == "0.15.0"
    detail = client.get(f"/admin/api/creators/{_creator['id']}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["stripe_account_id"] is None


def test_payouts_and_billing_reuse_stripe_objects(monkeypatch):
    fake = _install_fake(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_placeholder")
    shop, creator, seller, creator_cookie, admin = _seller_and_creator(monkeypatch)
    first = client.post(
        "/creator/payouts",
        data={"csrf_token": _token(client.get("/creator/payouts", headers=creator_cookie).text)},
        headers=creator_cookie,
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert first.headers["location"] == "https://connect.stripe.test/setup/acct_test_123"
    assert fake.accounts.created[0]["identity"]["country"] == "AU"
    assert fake.accounts.created[0]["defaults"]["currency"] == "aud"
    assert fake.accounts.created[0]["defaults"]["responsibilities"]["fees_collector"] == "application"
    assert fake.accounts.created[0]["defaults"]["responsibilities"]["losses_collector"] == "application"
    assert fake.accounts.created[0]["configuration"]["recipient"]["capabilities"]["stripe_balance"]["stripe_transfers"]["requested"] is True
    second = client.post(
        "/creator/payouts",
        data={"csrf_token": _token(client.get("/creator/payouts", headers=creator_cookie).text)},
        headers=creator_cookie,
        follow_redirects=False,
    )
    assert second.status_code == 303
    assert len(fake.accounts.created) == 1
    assert len(fake.links.created) == 2
    returned = client.get("/creator/payouts/return", headers=creator_cookie)
    assert "acct_test_123" in returned.text
    assert "True" in returned.text
    billing = client.post(
        "/seller/billing",
        data={"csrf_token": _token(client.get("/seller/billing", headers=seller).text)},
        headers=seller,
        follow_redirects=False,
    )
    assert billing.status_code == 303
    again = client.post(
        "/seller/billing",
        data={"csrf_token": _token(client.get("/seller/billing", headers=seller).text)},
        headers=seller,
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert len(fake.customers.created) == 1
    assert "cus_test_123" in client.get("/seller/billing", headers=seller).text
    assert client.get(f"/admin/api/shops/{shop['id']}", headers=admin).json()["stripe_customer_id"] == "cus_test_123"
    assert client.get(f"/admin/api/creators/{creator['id']}", headers=admin).json()["stripe_account_id"] == "acct_test_123"
    assert "sk_test_placeholder" not in json.dumps(fake.accounts.created)


def test_webhook_is_idempotent_and_updates_payout_status(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
    db.memory_creator_stripe["creator-1"] = {"stripe_account_id": "acct_test_123", "payouts_enabled": False}

    def fake_event(payload, signature, secret):
        assert secret == "whsec_placeholder"
        if signature != "good":
            raise ValueError("bad signature")
        return json.loads(payload)

    monkeypatch.setattr(stripe.Webhook, "construct_event", fake_event)
    body = {
        "id": "evt_1",
        "type": "account.updated",
        "data": {"object": {"id": "acct_test_123", "payouts_enabled": True, "details_submitted": True, "requirements": {"currently_due": ["external_account"]}}},
    }
    raw = json.dumps(body).encode()
    first = client.post("/stripe/webhook", content=raw, headers={"stripe-signature": "good"})
    assert first.status_code == 200
    assert db.memory_creator_stripe["creator-1"]["payouts_enabled"] is True
    assert db.memory_creator_stripe["creator-1"]["stripe_requirements_due"] == ["external_account"]
    body["data"]["object"]["payouts_enabled"] = False
    second = client.post("/stripe/webhook", content=json.dumps(body).encode(), headers={"stripe-signature": "good"})
    assert second.status_code == 200
    assert db.memory_creator_stripe["creator-1"]["payouts_enabled"] is True
    paid = {"id": "evt_paid", "type": "invoice.paid", "data": {"object": {"id": "in_1"}}}
    assert client.post("/stripe/webhook", content=json.dumps(paid).encode(), headers={"stripe-signature": "good"}).status_code == 200
    failed = {"id": "evt_failed", "type": "invoice.payment_failed", "data": {"object": {"id": "in_2"}}}
    assert client.post("/stripe/webhook", content=json.dumps(failed).encode(), headers={"stripe-signature": "good"}).status_code == 200
    assert set(db.memory_stripe_events) == {"evt_1", "evt_paid", "evt_failed"}
    assert client.post("/stripe/webhook", content=raw, headers={"stripe-signature": "bad"}).status_code == 400
