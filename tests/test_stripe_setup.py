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
        "foundr9_curatr_month_12mo",
        "pilotgrowth_growth_50_3mo",
        "elite_grant_3mo",
    }
    for coupon_id, spec in billing_config.COUPONS.items():
        assert len(spec["name"]) <= 40, coupon_id
    assert set(billing_config.PROMOTION_CODES) == {"FOUNDR9", "PILOTGROWTH"}
    assert "FOUNDING50" in billing_config.RETIRED_PROMOTION_CODES
    foundr9 = billing_config.COUPONS["foundr9_curatr_month_12mo"]
    assert foundr9["amount_off"] == 1000 and foundr9["duration"] == "repeating" and foundr9["duration_in_months"] == 12
    assert foundr9["applies_to"] == [("creator", "curator")]
    assert billing_config.COUPONS["pilotgrowth_growth_50_3mo"]["applies_to"] == [("seller", "growth")]


def test_empty_stripe_list_is_not_paged_or_get():
    assert setup._rows(_EmptyList()) == []
    more = _MorePages()
    assert [item.id for item in setup._rows(more)] == ["first", "second"]


def test_amount_off_coupon_is_created_without_a_percent():
    created = {}

    class Coupons:
        @staticmethod
        def retrieve(_coupon_id):
            raise RuntimeError("missing")

        @staticmethod
        def create(payload):
            created["payload"] = payload
            return SimpleNamespace(id=payload["id"])

    class Client:
        coupons = Coupons

    spec = billing_config.COUPONS["foundr9_curatr_month_12mo"]
    products = {("creator", "curator"): "prod_curatr"}
    assert setup._find_or_create_coupon(Client(), "foundr9_curatr_month_12mo", spec, products) == "foundr9_curatr_month_12mo"
    assert created["payload"]["amount_off"] == 1000
    assert created["payload"]["applies_to"] == {"products": ["prod_curatr"]}
    assert created["payload"]["currency"] == "aud"
    assert created["payload"]["duration_in_months"] == 12
    assert "percent_off" not in created["payload"]


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

    assert setup._find_or_create_promo(EmptyClient(), "FOUNDR9", billing_config.PROMOTION_CODES["FOUNDR9"]) == "promo_new"
    assert EmptyClient.payload["code"] == "FOUNDR9"
    assert EmptyClient.payload["promotion"]["coupon"] == "foundr9_curatr_month_12mo"

    class Existing:
        data = [SimpleNamespace(id="promo_existing", promotion=SimpleNamespace(coupon="pilotgrowth_growth_50_3mo"))]
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


def test_a_code_on_an_old_coupon_is_retired_and_recreated_and_founding50_is_deactivated():
    updates = []

    class Old:
        data = [SimpleNamespace(id="promo_old", promotion=SimpleNamespace(coupon="foundr_10aud_12mo"))]
        has_more = False

    class Client:
        class promotion_codes:
            @staticmethod
            def list(params):
                assert params["active"] is True
                return Old()

            @staticmethod
            def update(promo_id, payload):
                updates.append((promo_id, payload))

            @staticmethod
            def create(payload):
                return SimpleNamespace(id="promo_new")

    assert setup._find_or_create_promo(Client(), "FOUNDR9", billing_config.PROMOTION_CODES["FOUNDR9"]) == "promo_new"
    assert updates == [("promo_old", {"active": False})]
    updates.clear()
    assert setup._deactivate_promo(Client(), "FOUNDING50") == 1
    assert updates == [("promo_old", {"active": False})]
