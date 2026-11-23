"""Seller profile, curated catalogue, and CSV pick-to-import."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import catalogue
import db
import events
import pilots
from models import Product, utcnow
from test_api import PRODUCT, client
from tests.test_admin import EMAIL, PASSWORD as ADMIN_PASSWORD, _cookie_header, _login, _setup
from tests.test_persistence import use_database
from tests.test_portal import PASSWORD, _cookie, _meta_csrf, _token

VALID_ABN = "51 824 753 556"


def _seller(monkeypatch, *, subscribed=True, name="Catalogue Shop"):
    _setup(monkeypatch)
    shop = client.post("/shops", json={"name": name, "primary_category": "home"}).json()
    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    admin_csrf = _meta_csrf(client.get("/admin", headers=admin_cookie).text)
    if subscribed:
        db.memory_shops[shop["id"]].subscription_status = "active"
    email = f"seller-{shop['id'][:8]}@example.com"
    created = client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={
            "csrf_token": admin_csrf,
            "email": email,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        },
        headers=admin_cookie,
    )
    assert created.status_code == 200
    login = client.get("/seller")
    signed = client.post(
        "/seller/login",
        data={"email": email, "password": PASSWORD, "csrf_token": _token(login.text)},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    return shop, _cookie(signed, "plantr_seller")


def _curation(**extra):
    fields = {
        "why_chosen": "It is the one we want creators to talk about",
        "value_to_creators": "Easy to show and explain in one sentence",
        "commission": "12",
    }
    fields.update(extra)
    return fields


def test_profile_edit_validates_abn_and_keeps_the_account_name(monkeypatch):
    shop, cookie = _seller(monkeypatch)
    other = client.post("/shops", json={"name": "Other", "shopify_domain": "taken.myshopify.com"})
    assert other.status_code == 200
    page = client.get("/seller/profile", headers=cookie)
    assert page.status_code == 200
    assert "Catalogue Shop" in page.text
    assert "trading name" in page.text
    assert 'name="business_name"' in page.text
    assert 'name="abn"' in page.text
    assert 'name="primary_category"' in page.text
    assert 'name="store_url"' in page.text
    assert 'name="shopify_domain"' in page.text
    bad = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(page.text),
            "business_name": "Catalogue Plants",
            "abn": "51 824 753 557",
            "primary_category": "home",
            "store_url": "https://plants.example",
            "shopify_domain": "catalogue.myshopify.com",
        },
        headers=cookie,
    )
    assert bad.status_code == 422
    assert "not valid" in bad.text.lower() or "11 digits" in bad.text
    assert 'name="abn"' in bad.text
    saved = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(bad.text),
            "business_name": "Catalogue Plants",
            "abn": VALID_ABN,
            "primary_category": "beauty",
            "store_url": "https://plants.example",
            "shopify_domain": "catalogue.myshopify.com",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303
    row = db.memory_shops[shop["id"]]
    assert row.name == "Catalogue Shop"
    assert row.business_name == "Catalogue Plants"
    assert row.abn == "51824753556"
    assert row.primary_category == "beauty"
    assert row.store_url == "https://plants.example"
    assert row.shopify_domain == "catalogue.myshopify.com"
    shown = client.get("/seller/profile", headers=cookie).text
    assert "Catalogue Plants" in shown
    assert "51824753556" in shown
    clash = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(shown),
            "business_name": "Catalogue Plants",
            "abn": "",
            "primary_category": "beauty",
            "store_url": "https://plants.example",
            "shopify_domain": "taken.myshopify.com",
        },
        headers=cookie,
    )
    assert clash.status_code == 409
    cleared = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(clash.text),
            "business_name": "Catalogue Plants",
            "abn": "",
            "primary_category": "home",
            "store_url": "",
            "shopify_domain": "",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert row.abn is None
    assert row.store_url is None
    assert row.shopify_domain is None
    assert row.primary_category == "home"


def test_join_copies_the_application_onto_the_shop(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    applied = pilots.apply(
        "seller_pilot",
        name="Sam",
        email="prefill@example.com",
        role="seller",
        categories="Home, nope",
        contact="plants.example",
        age_confirmed=True,
        date_of_birth=__import__("datetime").date(1990, 1, 1),
        touch_state=None,
        consent=False,
        event_id="prefill-apply",
        paid_service_confirmed=True,
        authority_confirmed=True,
        business_name="Sam's Plants",
        abn=VALID_ABN,
    )
    approved = pilots.approve(applied["id"])
    page = client.get(f"/join/{approved['invite_code']}").text
    token = _token(page)
    joined = client.post(
        f"/join/{approved['invite_code']}",
        data={
            "csrf_token": token,
            "name": "Jo",
            "email": "prefill@example.com",
            "password": "a-long-password-1",
            "categories": "beauty",
            "age_confirmed": "yes",
            "date_of_birth": "1990-01-01",
            "terms_accepted": "yes",
            "pilot_terms_accepted": "yes",
        },
        follow_redirects=False,
    )
    assert joined.status_code == 303
    shop = next(iter(db.memory_shops.values()))
    assert shop.name == "Jo"
    assert shop.business_name == "Sam's Plants"
    assert shop.abn == "51824753556"
    assert shop.primary_category == "home"
    assert shop.store_url == "https://plants.example"
    assert shop.shopify_domain is None


def test_manual_product_requires_why_value_and_commission(monkeypatch):
    shop, cookie = _seller(monkeypatch)
    page = client.get("/seller/products", headers=cookie)
    assert "No products yet." in page.text
    assert "id='first-product'" in page.text
    assert "Start with one product you have chosen on purpose." in page.text
    assert "A short list, with a plain reason for each one, is what we use." in page.text
    assert 'id="product-count">0 products' in page.text
    missing = client.post(
        "/seller/products",
        data={
            "csrf_token": _token(page.text),
            "title": "Lamp",
            "category": "home",
            "price": "40",
            "url": "https://brand.example/lamp",
        },
        headers=cookie,
    )
    assert missing.status_code == 422
    assert db.memory_products == {}
    saved = client.post(
        "/seller/products",
        data={
            "csrf_token": _token(page.text),
            "title": "Lamp",
            "category": "home",
            "price": "40",
            "url": "https://brand.example/lamp",
            "image_url": "https://brand.example/lamp.jpg",
            **_curation(),
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303
    product = next(iter(db.memory_products.values()))
    assert product.source == "manual"
    assert product.url == "https://brand.example/lamp"
    assert product.attributes["url"] == "https://brand.example/lamp"
    assert product.why_chosen.startswith("It is the one")
    assert product.image_url == "https://brand.example/lamp.jpg"
    assert float(product.commission_rate) == 12
    listed = client.get("/seller/products", headers=cookie).text
    assert "1 product" in listed
    assert "Complete this product" not in listed


def test_soft_threshold_warns_and_does_not_block_or_charge(monkeypatch, caplog):
    monkeypatch.setenv("CATALOGUE_FEE_THRESHOLD", "1")
    shop, cookie = _seller(monkeypatch)
    page = client.get("/seller/products", headers=cookie)

    def _add(title):
        return client.post(
            "/seller/products",
            data={
                "csrf_token": _token(client.get("/seller/products", headers=cookie).text),
                "title": title,
                "category": "home",
                "url": f"https://brand.example/{title}",
                **_curation(),
            },
            headers=cookie,
            follow_redirects=False,
        )

    assert _add("One").status_code == 303
    with caplog.at_level(logging.INFO, logger="plantr.catalogue"):
        second = _add("Two")
    assert second.status_code == 303
    assert len(db.memory_products) == 2
    assert getattr(db.memory_shops[shop["id"]], "stripe_customer_id", None) in (None, "")
    warning = client.get("/seller/products", headers=cookie).text
    assert "may attract a fee after the pilot" in warning
    assert "There is no charge now." in warning
    assert "catalogue_fee_threshold" in caplog.text
    crossings = [event for event in db.memory_events if event.event_name == "catalogue_fee_threshold"]
    assert len(crossings) == 1
    assert crossings[0].properties["threshold"] == 1
    assert crossings[0].properties["products_before"] == 1
    assert crossings[0].properties["products_after"] == 2
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="plantr.catalogue"):
        assert _add("Three").status_code == 303
    assert len([event for event in db.memory_events if event.event_name == "catalogue_fee_threshold"]) == 1
    assert "catalogue_fee_threshold" not in caplog.text


def test_scale_product_cap_stays_open_and_the_match_cap_remains():
    import billing_config

    assert billing_config.shop_limits("scale")["products"] is None
    assert billing_config.shop_limits("scale")["matches_per_month"] == 100
    assert events.PLAN_CAPS["starter"]["products"] == 5
    shop = SimpleNamespace(id="scale-shop", plan_tier="scale", is_pilot=False, plan_status="active")
    events.guard_plan_limit(shop, "products", None)


def test_incomplete_products_are_excluded_from_matching_and_briefs(monkeypatch):
    shop, cookie = _seller(monkeypatch)
    bare = client.post("/enrich", json={**PRODUCT, "shop_id": shop["id"], "title": "Bare lamp", "attributes": {"url": "https://brand.example/bare"}})
    assert bare.status_code == 200
    assert "brief_id" not in bare.json()
    ready = client.post(
        "/enrich",
        json={
            **PRODUCT,
            "shop_id": shop["id"],
            "title": "Ready lamp",
            "why_chosen": "Chosen for the pilot list",
            "value_to_creators": "Clear to explain on camera",
            "commission_rate": 10,
            "attributes": {"url": "https://brand.example/ready"},
        },
    )
    assert ready.status_code == 200
    assert ready.json()["brief_id"]
    products = client.get("/seller/products", headers=cookie).text
    assert products.count("Complete this product") == 1
    assert "Bare lamp" in products
    found = client.get("/seller/find", headers=cookie).text
    assert "Ready lamp" in found
    assert "Bare lamp" not in found
    bare_id = next(row.id for row in db.memory_products.values() if row.title == "Bare lamp")
    refused = client.post(
        "/seller/find",
        data={"csrf_token": _token(found), "product_id": bare_id},
        headers=cookie,
    )
    assert refused.status_code == 422
    assert "Complete this product" in refused.json()["detail"]
    stored = next(row for row in db.memory_products.values() if row.title == "Bare lamp")
    assert stored.url == "https://brand.example/bare"
    assert catalogue.product_public_url(SimpleNamespace(url=None, attributes={"url": "https://brand.example/legacy"})) == (
        "https://brand.example/legacy"
    )


def test_csv_preview_is_unticked_and_drafts_wait_for_completion(monkeypatch):
    shop, cookie = _seller(monkeypatch, subscribed=False)
    locked = client.get("/seller/products", headers=cookie)
    blocked = client.post(
        "/seller/products/import",
        data={"csrf_token": _token(locked.text)},
        files={"file": ("products.csv", "title,url,price,category,image_url\nLamp,https://brand.example/lamp,1,home,\n", "text/csv")},
        headers=cookie,
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/seller/billing")
    db.memory_shops[shop["id"]].subscription_status = "active"
    template = client.get("/seller/products/csv-template", headers=cookie)
    assert template.status_code == 200
    assert template.headers["content-type"].startswith("text/csv")
    assert template.text.splitlines()[0] == "title,url,price,category,image_url,commission"
    page = client.get("/seller/products", headers=cookie)
    csv_text = "\n".join(
        [
            "title,url,price,category,image_url,commission",
            "Lamp,https://brand.example/lamp,40,home,https://brand.example/lamp.jpg,15",
            "Chair,https://brand.example/chair,80,home,,",
            "Bad,not-a-url,nope,home,,",
        ]
    )
    preview = client.post(
        "/seller/products/import",
        data={"csrf_token": _token(page.text)},
        files={"file": ("products.csv", csv_text, "text/csv")},
        headers=cookie,
    )
    assert preview.status_code == 200
    assert preview.text.count("name='pick'") == 2
    assert "checked" not in preview.text
    assert "URL must be an http" in preview.text
    none = client.post(
        "/seller/products/import/drafts",
        data={"csrf_token": _token(preview.text), "row_count": "3"},
        headers=cookie,
    )
    assert none.status_code == 422
    drafted = client.post(
        "/seller/products/import/drafts",
        data={
            "csrf_token": _token(preview.text),
            "row_count": "3",
            "pick": "0",
            "title_0": "Lamp",
            "url_0": "https://brand.example/lamp",
            "price_0": "40",
            "category_0": "home",
            "image_url_0": "https://brand.example/lamp.jpg",
            "commission_0": "15",
            "title_1": "Chair",
            "url_1": "https://brand.example/chair",
            "price_1": "80",
            "category_1": "home",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert drafted.status_code == 303
    assert len(db.memory_products) == 1
    draft = next(iter(db.memory_products.values()))
    assert draft.title == "Lamp"
    assert draft.source == "csv"
    assert catalogue.product_is_complete(draft) is False
    quiet = client.get("/seller/find", headers=cookie).text
    assert "Lamp" not in quiet
    refused = client.post(
        "/seller/find",
        data={"csrf_token": _token(quiet), "product_id": draft.id},
        headers=cookie,
    )
    assert refused.status_code == 422
    details = client.post(
        "/seller/products/import/details",
        data={
            "csrf_token": _token(preview.text),
            "row_count": "3",
            "pick": "1",
            "title_1": "Chair",
            "url_1": "https://brand.example/chair",
            "price_1": "80",
            "category_1": "home",
            "image_url_1": "",
            "commission_1": "",
        },
        headers=cookie,
    )
    assert details.status_code == 200
    assert "Finish each product" in details.text
    assert "Chair" in details.text
    unfinished = client.post(
        "/seller/products/import/save",
        data={
            "csrf_token": _token(details.text),
            "finish": "1",
            "row_count": "1",
            "title_0": "Chair",
            "url_0": "https://brand.example/chair",
            "price_0": "80",
            "category_0": "home",
            "why_chosen_0": "",
            "value_to_creators_0": "",
            "commission_0": "",
        },
        headers=cookie,
    )
    assert unfinished.status_code == 422
    assert len(db.memory_products) == 1
    finished = client.post(
        "/seller/products/import/save",
        data={
            "csrf_token": _token(details.text),
            "finish": "1",
            "row_count": "1",
            "title_0": "Chair",
            "url_0": "https://brand.example/chair",
            "price_0": "80",
            "category_0": "home",
            "why_chosen_0": "The chair is the one we want in the list",
            "value_to_creators_0": "It is easy to show in a room",
            "commission_0": "8",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert finished.status_code == 303
    chair = next(row for row in db.memory_products.values() if row.title == "Chair")
    assert chair.source == "csv"
    assert catalogue.product_is_complete(chair) is True
    found = client.get("/seller/find", headers=cookie).text
    assert "Chair" in found
    assert "Lamp" not in found


def test_csv_rejects_too_many_rows_and_a_file_over_1mb(monkeypatch):
    _shop, cookie = _seller(monkeypatch)
    monkeypatch.setenv("MAX_BODY_BYTES", "3000000")
    page = client.get("/seller/products", headers=cookie)
    lines = ["title,url,price,category,image_url,commission"]
    lines.extend(f"Item {index},https://brand.example/{index},10,home,," for index in range(501))
    too_many = client.post(
        "/seller/products/import",
        data={"csrf_token": _token(page.text)},
        files={"file": ("products.csv", "\n".join(lines), "text/csv")},
        headers=cookie,
    )
    assert too_many.status_code == 422
    assert "500" in too_many.json()["detail"]
    huge = b"x" * (catalogue.MAX_CSV_BYTES + 1)
    too_big = client.post(
        "/seller/products/import",
        data={"csrf_token": _token(page.text)},
        files={"file": ("products.csv", huge, "text/csv")},
        headers=cookie,
    )
    assert too_big.status_code == 422
    assert "1MB" in too_big.json()["detail"]
    with pytest.raises(HTTPException):
        catalogue.parse_product_csv(huge)


def test_attribute_url_is_copied_onto_the_column(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    with db.session_scope() as session:
        from models import Shop

        session.add(
            Shop(
                id="shop-url",
                name="URL Shop",
                plan_tier="starter",
                plan_status="active",
                is_pilot=False,
                created_at=utcnow(),
            )
        )
        session.add(
            Product(
                id="prod-url",
                shop_id="shop-url",
                title="Legacy",
                images=[],
                tags=[],
                attributes={"url": "https://brand.example/legacy"},
                claims=[],
                currency="AUD",
                source="manual",
                url=None,
                created_at=utcnow(),
            )
        )
    with db.engine.begin() as connection:
        assert catalogue.copy_urls_from_attributes(connection) == 1
    with db.session_scope() as session:
        stored = session.get(Product, "prod-url")
        assert stored.url == "https://brand.example/legacy"
        assert stored.attributes["url"] == "https://brand.example/legacy"
    with db.engine.begin() as connection:
        assert catalogue.copy_urls_from_attributes(connection) == 0


def test_website_and_category_helpers():
    assert catalogue.website_url("https://plants.example/shop") == "https://plants.example/shop"
    assert catalogue.website_url("plants.example") == "https://plants.example"
    assert catalogue.website_url("@sam") is None
    assert catalogue.website_url("call me") is None
    assert catalogue.first_category("Home, beauty") == "home"
    assert catalogue.first_category("nope") is None
