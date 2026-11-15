"""Create Stripe Billing products, prices, coupons, and promotion codes.

Reads STRIPE_SECRET_KEY_TEST only. Refuses live keys. Safe to run again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import billing_config


def main() -> int:
    key = os.getenv("STRIPE_SECRET_KEY_TEST", "").strip()
    if not key.startswith("sk_test_"):
        print("STRIPE_SECRET_KEY_TEST must be a Stripe test secret key.", file=sys.stderr)
        return 1
    import stripe

    client = stripe.StripeClient(key)
    for tier_name, spec in {**billing_config.SELLER_TIERS, **billing_config.CREATOR_TIERS}.items():
        if not spec.get("lookup_month"):
            continue
        product = _find_or_create_product(client, spec["label"], tier_name, spec["audience"])
        _find_or_create_price(client, product, spec["lookup_month"], spec["amount_month_cents"], "month")
        _find_or_create_price(client, product, spec["lookup_year"], spec["amount_year_cents"], "year")
    for coupon_id, spec in billing_config.COUPONS.items():
        _find_or_create_coupon(client, coupon_id, spec)
    for code, spec in billing_config.PROMOTION_CODES.items():
        _find_or_create_promo(client, code, spec)
    print("Stripe sandbox catalog is ready.")
    return 0


def _find_or_create_product(client, name, tier, audience):
    existing = client.products.search({"query": f"metadata['tier']:'{tier}' AND metadata['audience']:'{audience}'"})
    data = getattr(existing, "data", None) or existing.get("data", [])
    if data:
        return data[0]["id"] if isinstance(data[0], dict) else data[0].id
    created = client.products.create({"name": name, "metadata": {"tier": tier, "audience": audience}})
    return created["id"] if isinstance(created, dict) else created.id


def _find_or_create_price(client, product_id, lookup_key, cents, interval):
    found = client.prices.list({"lookup_keys": [lookup_key], "limit": 1})
    data = getattr(found, "data", None) or found.get("data", [])
    if data:
        return data[0]["id"] if isinstance(data[0], dict) else data[0].id
    created = client.prices.create(
        {
            "product": product_id,
            "currency": billing_config.CURRENCY,
            "unit_amount": cents,
            "recurring": {"interval": interval},
            "lookup_key": lookup_key,
            "tax_behavior": "exclusive",
        }
    )
    return created["id"] if isinstance(created, dict) else created.id


def _find_or_create_coupon(client, coupon_id, spec):
    try:
        client.coupons.retrieve(coupon_id)
        return coupon_id
    except Exception:
        payload = {
            "id": coupon_id,
            "name": spec["name"],
            "percent_off": spec["percent_off"],
            "duration": spec["duration"],
            "currency": billing_config.CURRENCY,
        }
        if spec.get("duration_in_months"):
            payload["duration_in_months"] = spec["duration_in_months"]
        client.coupons.create(payload)
        return coupon_id


def _find_or_create_promo(client, code, spec):
    found = client.promotion_codes.list({"code": code, "limit": 1})
    data = getattr(found, "data", None) or found.get("data", [])
    if data:
        return data[0]["id"] if isinstance(data[0], dict) else data[0].id
    created = client.promotion_codes.create(
        {
            "code": code,
            "promotion": {"type": "coupon", "coupon": spec["coupon"]},
            "max_redemptions": spec["max_redemptions"],
            "metadata": {"campaign": spec["campaign"], "channel": spec["channel"]},
        }
    )
    return created["id"] if isinstance(created, dict) else created.id


if __name__ == "__main__":
    raise SystemExit(main())
