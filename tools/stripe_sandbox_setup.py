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
    products = {}
    for tier_name, spec in {**billing_config.SELLER_TIERS, **billing_config.CREATOR_TIERS}.items():
        if not spec.get("lookup_month"):
            continue
        product = _find_or_create_product(client, spec["label"], tier_name, spec["audience"])
        products[(spec["audience"], tier_name)] = product
        _find_or_create_price(client, product, spec["lookup_month"], spec["amount_month_cents"], "month")
        _find_or_create_price(client, product, spec["lookup_year"], spec["amount_year_cents"], "year")
    for coupon_id, spec in billing_config.COUPONS.items():
        _find_or_create_coupon(client, coupon_id, spec, products)
    for code in billing_config.RETIRED_PROMOTION_CODES:
        _deactivate_promo(client, code)
    for code, spec in billing_config.PROMOTION_CODES.items():
        _find_or_create_promo(client, code, spec)
    print("Stripe sandbox catalog is ready.")
    return 0


def _deactivate_promo(client, code):
    """Turn off every active promotion code with this code. Returns how many were turned off."""
    found = client.promotion_codes.list({"code": code, "active": True, "limit": 100})
    count = 0
    for row in _rows(found):
        client.promotion_codes.update(_stripe_id(row), {"active": False})
        count += 1
    return count


def _find_or_create_product(client, name, tier, audience):
    existing = client.products.search({"query": f"metadata['tier']:'{tier}' AND metadata['audience']:'{audience}'"})
    data = _rows(existing)
    if data:
        product_id = _stripe_id(data[0])
        if _name_of(data[0]) != name:
            client.products.update(product_id, {"name": name})
        return product_id
    created = client.products.create({"name": name, "metadata": {"tier": tier, "audience": audience}})
    return _stripe_id(created)


def _find_or_create_price(client, product_id, lookup_key, cents, interval):
    found = client.prices.list({"lookup_keys": [lookup_key], "limit": 1})
    data = _rows(found)
    if data:
        return _stripe_id(data[0])
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
    return _stripe_id(created)


def _find_or_create_coupon(client, coupon_id, spec, products=None):
    try:
        existing = client.coupons.retrieve(coupon_id)
    except Exception:
        existing = None
    if existing is not None:
        if _name_of(existing) != spec["name"]:
            try:
                client.coupons.update(coupon_id, {"name": spec["name"]})
            except Exception:
                print(f"Coupon {coupon_id} already exists and its name was left unchanged.", file=sys.stderr)
        return coupon_id
    payload = {
        "id": coupon_id,
        "name": spec["name"],
        "duration": spec["duration"],
        "currency": billing_config.CURRENCY,
    }
    if spec.get("amount_off"):
        payload["amount_off"] = spec["amount_off"]
    else:
        payload["percent_off"] = spec["percent_off"]
    if spec.get("duration_in_months"):
        payload["duration_in_months"] = spec["duration_in_months"]
    if spec.get("applies_to"):
        ids = [(products or {}).get(tuple(key)) for key in spec["applies_to"]]
        if not all(ids):
            raise SystemExit(f"Coupon {coupon_id} needs products {spec['applies_to']} to exist first.")
        payload["applies_to"] = {"products": ids}
    client.coupons.create(payload)
    return coupon_id


def _find_or_create_promo(client, code, spec):
    """Reuse the active code when it points at the configured coupon. Otherwise retire it and make a new one."""
    found = client.promotion_codes.list({"code": code, "active": True, "limit": 10})
    for row in _rows(found):
        if _promo_coupon(row) == spec["coupon"]:
            return _stripe_id(row)
        client.promotion_codes.update(_stripe_id(row), {"active": False})
    created = client.promotion_codes.create(
        {
            "code": code,
            "promotion": {"type": "coupon", "coupon": spec["coupon"]},
            "max_redemptions": spec["max_redemptions"],
            "metadata": {"campaign": spec["campaign"], "channel": spec["channel"]},
        }
    )
    return _stripe_id(created)


def _promo_coupon(row):
    promotion = row.get("promotion") if isinstance(row, dict) else getattr(row, "promotion", None)
    coupon = None
    if promotion is not None:
        coupon = promotion.get("coupon") if isinstance(promotion, dict) else getattr(promotion, "coupon", None)
    if coupon is None:
        coupon = row.get("coupon") if isinstance(row, dict) else getattr(row, "coupon", None)
    if coupon is not None and not isinstance(coupon, str):
        coupon = coupon.get("id") if isinstance(coupon, dict) else getattr(coupon, "id", None)
    return coupon


def _rows(result):
    """Read a list or search result without calling .get() on a Stripe object.

    An empty ``data`` list is a real answer. ``auto_paging_iter`` is used only
    when Stripe says there is another page, or when the result has no ``data``.
    """
    if result is None:
        return []
    has_data = hasattr(result, "data")
    data = list(result.data) if has_data and result.data is not None else None
    if getattr(result, "has_more", False) and hasattr(result, "auto_paging_iter"):
        try:
            return list(result.auto_paging_iter())
        except Exception:
            return data or []
    if data is not None:
        return data
    if hasattr(result, "auto_paging_iter"):
        try:
            return list(result.auto_paging_iter())
        except Exception:
            return []
    return []


def _stripe_id(obj):
    if isinstance(obj, dict):
        return obj["id"]
    return obj.id


def _name_of(obj):
    if isinstance(obj, dict):
        return obj.get("name")
    return getattr(obj, "name", None)


if __name__ == "__main__":
    raise SystemExit(main())
