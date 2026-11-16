"""Sandbox setup stays idempotent and does not call .get() on Stripe list objects."""

from types import SimpleNamespace

import billing_config
from tools import stripe_sandbox_setup as setup


class _EmptyList:
    def __init__(self):
        self.data = []
        self.has_more = False

    def get(self, *_args, **_kwargs):
        raise AssertionError("Stripe list objects must not be read with .get()")

    def auto_paging_iter(self):
        raise AssertionError("an empty list must not be paged")


class _MorePages:
    def __init__(self):
        self.data = [SimpleNamespace(id="first")]
        self.has_more = True

    def auto_paging_iter(self):
        yield SimpleNamespace(id="first")
        yield SimpleNamespace(id="second")


def test_coupon_display_names_fit_stripe_and_ids_stay_put():
    assert set(billing_config.COUPONS) == {
        "founding_curator_50_12mo",
        "seller_growth_50_3mo",
        "curator_sale_waiver",
        "elite_grant_3mo",
    }
    for coupon_id, spec in billing_config.COUPONS.items():
        assert len(spec["name"]) <= 40, coupon_id
    assert set(billing_config.PROMOTION_CODES) == {"FOUNDING50", "PILOTGROWTH"}


def test_empty_stripe_list_is_not_paged_or_get():
    assert setup._rows(_EmptyList()) == []
    more = _MorePages()
    assert [item.id for item in setup._rows(more)] == ["first", "second"]


def test_existing_product_name_is_updated_to_the_current_label():
    class Products:
        updated = []

        @staticmethod
        def search(_query):
            return SimpleNamespace(data=[SimpleNamespace(id="prod_curator", name="Curator")], has_more=False)

        @staticmethod
        def update(product_id, payload):
            Products.updated.append((product_id, payload))
            return SimpleNamespace(id=product_id)

        @staticmethod
        def create(_payload):
            raise AssertionError("an existing product must not be created again")

    class Client:
        products = Products

    assert setup._find_or_create_product(Client(), "Curatr", "curator", "creator") == "prod_curator"
    assert Products.updated == [("prod_curator", {"name": "Curatr"})]
    Products.updated.clear()

    class SameName:
        data = [SimpleNamespace(id="prod_trend", name="Trendsettr")]
        has_more = False

    class Quiet:
        class products:
            @staticmethod
            def search(_query):
                return SameName()

            @staticmethod
            def update(_product_id, _payload):
                raise AssertionError("a product that already has the label must not be updated")

    assert setup._find_or_create_product(Quiet(), "Trendsettr", "elite", "creator") == "prod_trend"
    assert billing_config.CREATOR_TIERS["curator"]["label"] == "Curatr"
    assert billing_config.CREATOR_TIERS["elite"]["label"] == "Trendsettr"
    assert billing_config.lookup_key("creator", "curator", "month") == "creator_curator_month"


def test_existing_promo_code_is_reused():
    class EmptyClient:
        class promotion_codes:
            @staticmethod
            def list(_params):
                return _EmptyList()

            @staticmethod
            def create(payload):
                EmptyClient.payload = payload
                return SimpleNamespace(id="promo_new")

    assert setup._find_or_create_promo(EmptyClient(), "FOUNDING50", billing_config.PROMOTION_CODES["FOUNDING50"]) == "promo_new"
    assert EmptyClient.payload["code"] == "FOUNDING50"

    class Existing:
        data = [SimpleNamespace(id="promo_existing")]
        has_more = False

        def get(self, *_args, **_kwargs):
            raise AssertionError("existing results must not use .get()")

    class Reuse:
        class promotion_codes:
            @staticmethod
            def list(_params):
                return Existing()

            @staticmethod
            def create(_payload):
                raise AssertionError("an existing promotion code must not be created again")

    assert setup._find_or_create_promo(Reuse(), "PILOTGROWTH", billing_config.PROMOTION_CODES["PILOTGROWTH"]) == "promo_existing"
