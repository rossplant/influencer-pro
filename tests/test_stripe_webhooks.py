"""Webhook fixtures for the current Stripe API and the previous field names."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

import db
import main
import stripe_billing
from models import utcnow

client = TestClient(main.app)


def _shop(shop_id="shop-1"):
    shop = SimpleNamespace(
        memory_kind="shop",
        id=shop_id,
        plan_tier="starter",
        plan_status="free",
        stripe_customer_id="cus_seller",
        stripe_subscription_id=None,
        subscription_status=None,
        billing_interval=None,
        first_paid_at=None,
        marketing_consent=True,
        attribution_first={"utm_source": "tiktok"},
        attribution_last={},
        signup_code="",
        signup_campaign="",
        created_at=utcnow(),
        is_pilot=False,
    )
    db.memory_shops[shop_id] = shop
    return shop


def _creator(creator_id="creator-1"):
    creator = SimpleNamespace(
        memory_kind="creator",
        id=creator_id,
        plan_tier="member",
        plan_status="invited",
        stripe_customer_id="cus_creator",
        stripe_subscription_id=None,
        subscription_status=None,
        billing_interval=None,
        marketing_consent=True,
        attribution_first={"utm_source": "meta"},
        attribution_last={},
        signup_code="INV",
        signup_campaign="founding_curators",
        created_at=utcnow(),
    )
    db.memory_creator_objects[creator_id] = creator
    return creator


def _post(monkeypatch, event):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")

    def fake_event(payload, signature, secret):
        return event

    import stripe

    monkeypatch.setattr(stripe.Webhook, "construct_event", fake_event)
    return client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "good"})


def _basil_subscription(subscription_id, customer, metadata, status="active"):
    return {
        "id": subscription_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "metadata": metadata,
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_test",
                    "object": "subscription_item",
                    "current_period_start": 1763200000,
                    "current_period_end": 1765792000,
                    "price": {"id": "price_test", "object": "price", "recurring": {"interval": "month"}},
                }
            ],
        },
    }


def test_current_api_events_update_status_and_do_not_500(monkeypatch):
    shop = _shop()
    creator = _creator()
    seller_meta = {"role": "seller", "subject_id": shop.id, "tier": "growth", "interval": "month"}
    creator_meta = {
        "role": "creator",
        "subject_id": creator.id,
        "tier": "curator",
        "interval": "month",
        "promo_code": "FOUNDING50",
        "campaign": "founding_curators",
    }

    checkout = _post(
        monkeypatch,
        {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test",
                    "object": "checkout.session",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "customer": "cus_seller",
                    "subscription": "sub_1UbynNF1UkkKMhl3LyFSW7Zp",
                    "client_reference_id": shop.id,
                    "metadata": seller_meta,
                }
            },
        },
    )
    assert checkout.status_code == 200
    assert shop.plan_tier == "growth"
    assert shop.plan_status == "active"

    created = _post(
        monkeypatch,
        {
            "id": "evt_sub_created",
            "type": "customer.subscription.created",
            "data": {"object": _basil_subscription("sub_1UbynNF1UkkKMhl3LyFSW7Zp", "cus_seller", seller_meta)},
        },
    )
    assert created.status_code == 200
    assert shop.plan_status == "active"
    assert shop.subscription_status == "active"
    assert shop.billing_interval == "month"

    updated = _post(
        monkeypatch,
        {
            "id": "evt_sub_updated",
            "type": "customer.subscription.updated",
            "data": {"object": _basil_subscription("sub_1UbyobF1UkkKMhl3H6MR9UkA", "cus_creator", creator_meta)},
        },
    )
    assert updated.status_code == 200
    assert creator.plan_tier == "curator"
    assert creator.plan_status == "active"

    invoice = _post(
        monkeypatch,
        {
            "id": "evt_invoice",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_basil",
                    "object": "invoice",
                    "amount_paid": 950,
                    "currency": "aud",
                    "customer": "cus_creator",
                    "parent": {
                        "type": "subscription_details",
                        "subscription_details": {
                            "subscription": "sub_1UbyobF1UkkKMhl3H6MR9UkA",
                            "metadata": creator_meta,
                        },
                    },
                    "lines": {
                        "object": "list",
                        "data": [
                            {
                                "id": "il_basil",
                                "pricing": {
                                    "type": "price_details",
                                    "price_details": {"price": "price_curator", "product": "prod_curator"},
                                },
                            }
                        ],
                    },
                }
            },
        },
    )
    assert invoice.status_code == 200
    subscribe = next(row for row in db.memory_events if row.event_name == "subscribe")
    founding = next(row for row in db.memory_events if row.event_name == "founding_free_to_paid")
    assert subscribe.properties["value"] == 9.5
    assert subscribe.properties["currency"] == "aud"
    assert subscribe.properties["period"] == "monthly"
    assert subscribe.properties["tier"] == "curator"
    assert founding.properties["event_id"] == "founding:in_basil"
    assert stripe_billing._line_price_id(invoice.json() or {}) == ""
    assert stripe_billing._line_price_id(
        {"pricing": {"price_details": {"price": "price_curator"}}}
    ) == "price_curator"

    failed = _post(
        monkeypatch,
        {
            "id": "evt_failed",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_failed",
                    "object": "invoice",
                    "customer": "cus_creator",
                    "parent": {"subscription_details": {"subscription": "sub_1UbyobF1UkkKMhl3H6MR9UkA"}},
                }
            },
        },
    )
    assert failed.status_code == 200

    ending = _post(
        monkeypatch,
        {
            "id": "evt_trial",
            "type": "customer.subscription.trial_will_end",
            "data": {"object": _basil_subscription("sub_trial", "cus_seller", seller_meta, status="trialing")},
        },
    )
    assert ending.status_code == 200

    deleted = _post(
        monkeypatch,
        {
            "id": "evt_deleted",
            "type": "customer.subscription.deleted",
            "data": {"object": _basil_subscription("sub_1UbynNF1UkkKMhl3LyFSW7Zp", "cus_seller", seller_meta, status="canceled")},
        },
    )
    assert deleted.status_code == 200
    assert shop.plan_tier == "starter"
    assert shop.plan_status == "free"

    broken = _post(
        monkeypatch,
        {"id": "evt_broken", "type": "customer.subscription.created", "data": {"object": {"customer": {"unexpected": True}}}},
    )
    assert broken.status_code == 200
