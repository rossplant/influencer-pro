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
    assert billing_config.tier_label("creator", "elite") == "Trendsettr"
    assert billing_config.tier_label("seller", "growth") == "Growth"
    assert billing_config.lookup_key("creator", "elite", "year") == "creator_elite_year"
    assert billing_config.COUPONS["founding_curator_50_12mo"]["duration_in_months"] == 12
    assert billing_config.COUPONS["seller_growth_50_3mo"]["duration_in_months"] == 3
    assert billing_config.COUPONS["founding_curator_50_12mo"]["name"] == "Foundr 50% for 12 months"
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
    assert "Trendsettr" in page
    assert 'value="curator"' in page
    assert 'value="elite"' in page
    assert "Elite" not in page
    assert "Founding Curator" not in page


def test_pilot_window_is_two_months_and_ignores_the_old_week_setting(monkeypatch):
    monkeypatch.setenv("PILOT_WEEKS", "6")
    assert billing_config.pilot_months() == 2
    assert billing_config.pilot_start() == datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert billing_config.pilot_end() == datetime(2027, 1, 20, tzinfo=timezone.utc)
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
    assert db.memory_cohorts["seller_pilot"].ends_at == datetime(2027, 1, 20, tzinfo=timezone.utc)
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
        promo_code="FOUNDING50",
        trial_end=stripe_billing.pilot_trial_end("creator", creator.id),
    )
    created = fake.checkout.sessions.created[0]
    assert created["subscription_data"]["trial_end"] == expected
    assert created["discounts"] == [{"promotion_code": "promo_founding"}]
    assert stripe_billing.pilot_trial_end("creator", "missing") is None
