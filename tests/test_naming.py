"""Public names and the two-month pilot. Internal ids stay put."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import billing_config
import db
import pilots
import portal
import stripe_billing
from tests.test_stripe import FakeStripe


def test_creator_labels_and_pilot_prices_keep_internal_ids():
    assert billing_config.tier_label("creator", "member") == "Membr"
    assert billing_config.tier_label("creator", "curator") == "Curatr"
    assert billing_config.tier_label("creator", "elite") == "Curatr Pro"
    assert billing_config.tier_label("seller", "growth") == "Growth"
    assert billing_config.lookup_key("creator", "elite", "year") == "creator_elite_year"
    assert "founding_curator_50_12mo" not in billing_config.COUPONS
    assert billing_config.COUPONS["foundr9_curatr_month_12mo"]["amount_off"] == 1000
    assert billing_config.COUPONS["foundr9_curatr_month_12mo"]["duration_in_months"] == 12
    assert billing_config.COUPONS["pilotgrowth_growth_50_3mo"]["duration_in_months"] == 3
    assert billing_config.lookup_key("seller", "starter", "month") == "seller_starter_month"
    assert billing_config.lookup_key("creator", "member", "year") == "creator_member_year"
    assert billing_config.price_label(1500) == "A$15 ex GST"
    assert "Included" not in billing_config.price_label(0)
    assert billing_config.PILOT_COHORTS["founding_curators"]["name"] == "Foundrs"
    page = portal._billing_body(
        "creator",
        {
            "configured": True,
            "plan_tier": "member",
            "plan_status": "invited",
            "plans": stripe_billing.plans("creator"),
        },
        "tok",
        "/creator/billing",
    )
    assert "Current plan: Membr" in page
    assert "Curatr" in page
    assert "Curatr Pro" in page
    assert 'value="curator"' in page
    assert 'value="elite"' in page
    assert "Elite" not in page
    assert "Founding Curator" not in page


def test_pilot_window_is_20_nov_to_20_jan_sydney_and_ignores_the_old_week_setting(monkeypatch):
    monkeypatch.setenv("PILOT_WEEKS", "6")
    assert billing_config.pilot_months() == 2
    # 20 Nov 2026 00:00 AEDT and 20 Jan 2027 12:00 AEDT.
    assert billing_config.pilot_start() == datetime(2026, 11, 19, 13, 0, tzinfo=timezone.utc)
    assert billing_config.pilot_end() == datetime(2027, 1, 20, 1, 0, tzinfo=timezone.utc)
    assert billing_config.sydney_date_label(billing_config.pilot_end()) == "20 January 2027"
    now = datetime.now(timezone.utc)
    db.memory_cohorts["seller_pilot"] = SimpleNamespace(
        id="old-seller",
        slug="seller_pilot",
        name="Seller pilot",
        role="seller",
        seat_cap=10,
        starts_at=now,
        ends_at=now + timedelta(weeks=8),
        pilot_tier="growth",
        post_coupon_key="seller_growth_50_3mo",
        active=True,
    )
    pilots.ensure_cohorts()
    assert db.memory_cohorts["seller_pilot"].ends_at == datetime(2027, 1, 20, 1, 0, tzinfo=timezone.utc)
    assert db.memory_cohorts["founding_curators"].name == "Foundrs"
    assert db.memory_cohorts["founding_curators"].ends_at == db.memory_cohorts["seller_pilot"].ends_at


def test_pilot_checkout_is_free_until_the_cohort_end(monkeypatch):
    pilots.ensure_cohorts()
    creator = SimpleNamespace(
        memory_kind="creator",
        id="creator-pilot",
        signup_campaign="founding_curators",
        subscription_status=None,
        plan_tier="curator",
        plan_status="trial",
    )
    db.memory_creator_objects[creator.id] = creator
    db.memory_portal_accounts.append(
        SimpleNamespace(role="creator", creator_id=creator.id, shop_id=None, email="foundr@example.com")
    )
    expected = int(billing_config.pilot_end().timestamp())
    assert stripe_billing.pilot_trial_end("creator", creator.id) == expected
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    stripe_billing.checkout(
        role="creator",
        subject_id=creator.id,
        tier="curator",
        interval="month",
        success_url="https://pay.example/creator/billing",
        cancel_url="https://pay.example/creator/billing",
        trial_end=stripe_billing.pilot_trial_end("creator", creator.id),
    )
    created = fake.checkout.sessions.created[0]
    assert created["subscription_data"]["trial_end"] == expected
    # FOUNDR9 is applied without typing it, and attached when the free period ends.
    assert "discounts" not in created
    assert "allow_promotion_codes" not in created
    assert created["subscription_data"]["metadata"]["pilot_offer"] == "FOUNDR9"
    assert created["subscription_data"]["metadata"]["pilot_promotion_code"] == "promo_foundr9"
    message = created["custom_text"]["submit"]["message"]
    assert message.startswith("No charge today. Code FOUNDR9 applied. Your first charge of A$9 (ex GST) will be on 20 January 2027.")
    assert "then A$19 a month from 20 January 2028" in message
    assert stripe_billing.pilot_trial_end("creator", "missing") is None


def test_unsubscribed_accounts_see_a_subscribe_prompt(monkeypatch):
    from fastapi.testclient import TestClient

    import main
    from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

    _setup(monkeypatch)
    client = TestClient(main.app)
    shop = client.post("/shops", json={"name": "Needs a plan"}).json()
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    csrf = client.get("/admin", headers=admin).text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "need@example.com", "password": "password-pass", "password_confirm": "password-pass"},
        headers=admin,
    )
    login = client.get("/seller")
    token = login.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    signed = client.post(
        "/seller/login",
        data={"email": "need@example.com", "password": "password-pass", "csrf_token": token},
        follow_redirects=False,
    )
    cookie = {"Cookie": signed.headers["set-cookie"].split(";", 1)[0]}
    profile = client.get("/seller/profile", headers=cookie)
    assert profile.status_code == 200
    assert "Subscribe to add products" not in profile.text
    products = client.get("/seller/products", headers=cookie)
    assert "Subscribe to add products" in products.text
    assert "Save product" not in products.text
    import re

    page_token = re.search(r"name=[\"']csrf_token[\"'] value=[\"']([^\"']+)", profile.text).group(1)
    blocked = client.post(
        "/seller/products",
        data={"csrf_token": page_token, "title": "Blocked", "category": "home", "price": "10"},
        headers=cookie,
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/seller/billing")
    assert db.memory_products == {}
    billing = client.get(blocked.headers["location"], headers=cookie)
    assert "Subscribe to add products" in billing.text
    assert "A$29 ex GST" in billing.text
    assert "A$15 ex GST" not in billing.text
    assert stripe_billing.commerce_open("seller", shop["id"]) is False
    db.memory_shops[shop["id"]].is_pilot = True
    monkeypatch.setenv("PILOT_START", "2026-11-01")
    assert stripe_billing.commerce_open("seller", shop["id"]) is True
    monkeypatch.setenv("PILOT_START", "2020-01-01")
    monkeypatch.setenv("PILOT_END", "2020-03-01")
    assert stripe_billing.commerce_open("seller", shop["id"]) is False
    db.memory_shops[shop["id"]].is_pilot = False
    db.memory_shops[shop["id"]].subscription_status = "active"
    assert stripe_billing.commerce_open("seller", shop["id"]) is True
