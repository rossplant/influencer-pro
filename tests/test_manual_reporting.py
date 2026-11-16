"""Manual sales reporting: profile, approval, visibility, CSV upload, and importer."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select

import db
import manual_reporting
import shopify_sync
from models import AttributedOrder, CommissionStatement, Shop
from test_api import INFLUENCER, PRODUCT, client
from tests.test_admin import EMAIL, PASSWORD as ADMIN_PASSWORD, _cookie_header, _login, _setup
from tests.test_attribution import _catalogue, _session
from tests.test_persistence import use_database
from tests.test_portal import CREATOR_PASSWORD, PASSWORD, _cookie, _token
from tests.test_seller_catalogue import _seller

LABEL = "Manual sales reporting: sales are credited to Creatrs when the seller reports them each month."


def _admin(monkeypatch):
    _setup(monkeypatch)
    return _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))


def _creator_login(admin_cookie, handle="@manual-creatr"):
    creator = client.post("/onboard-influencer", json={**INFLUENCER, "handle": handle, "active_categories": ["home"]}).json()
    page = client.get("/admin", headers=admin_cookie)
    from tests.test_portal import _meta_csrf

    csrf = _meta_csrf(page.text)
    email = f"{handle.strip('@')}@example.com"
    created = client.post(
        f"/admin/api/creators/{creator['id']}/login",
        json={"csrf_token": csrf, "email": email, "password": CREATOR_PASSWORD, "password_confirm": CREATOR_PASSWORD},
        headers=admin_cookie,
    )
    assert created.status_code == 200
    login = client.get("/creator")
    signed = client.post(
        "/creator/login",
        data={"email": email, "password": CREATOR_PASSWORD, "csrf_token": _token(login.text)},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    return creator, _cookie(signed, "plantr_creator")


def _product_and_offer(shop, seller_cookie, admin_cookie):
    page = client.get("/seller/products", headers=seller_cookie)
    saved = client.post(
        "/seller/products",
        data={
            "csrf_token": _token(page.text),
            "title": "Fern",
            "category": "home",
            "price": "40",
            "url": "https://brand.example/fern",
            "why_chosen": "It is the plant we want Creatrs to show",
            "value_to_creators": "Easy to film on a table",
            "commission": "15",
        },
        headers=seller_cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303, saved.text
    product_id = next(iter(db.memory_products))
    creator, creator_cookie = _creator_login(admin_cookie)
    found = client.post(
        "/seller/find",
        data={"csrf_token": _token(client.get("/seller/products", headers=seller_cookie).text), "product_id": product_id},
        headers=seller_cookie,
    )
    assert found.status_code == 200, found.text
    assert "Fern" not in found.text or "@manual-creatr" in found.text
    return product_id, creator, creator_cookie


def test_unset_seller_is_prompted_and_shopify_domain_is_normalised(monkeypatch):
    shop, cookie = _seller(monkeypatch)
    page = client.get("/seller/profile", headers=cookie)
    assert "Please answer this" in page.text
    assert "Creatrs" in page.text
    assert 'name="sells_on_shopify"' in page.text
    missing = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(page.text),
            "business_name": "Fern Co",
            "primary_category": "home",
            "shopify_domain": "fern.myshopify.com",
        },
        headers=cookie,
    )
    assert missing.status_code == 422
    assert "Do you sell on Shopify" in missing.text
    bad_host = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(missing.text),
            "business_name": "Fern Co",
            "primary_category": "home",
            "sells_on_shopify": "yes",
            "shopify_domain": "fern.example.com",
        },
        headers=cookie,
    )
    assert bad_host.status_code == 422
    assert "myshopify.com" in bad_host.text
    saved = client.post(
        "/seller/profile",
        data={
            "csrf_token": _token(bad_host.text),
            "business_name": "Fern Co",
            "primary_category": "home",
            "sells_on_shopify": "no",
            "shopify_domain": "https://Should-Not-Stick.myshopify.com/",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303
    row = db.memory_shops[shop["id"]]
    assert row.shopify_domain is None
    assert row.sales_reporting == "manual"
    assert "Sales report" in client.get("/seller/profile", headers=cookie).text


def test_manual_seller_is_hidden_until_approved_and_live(monkeypatch):
    shop, cookie = _seller(monkeypatch, name="Hidden Ferns")
    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    product_id, creator, creator_cookie = _product_and_offer(shop, cookie, admin_cookie)
    offers = client.get("/creator/offers", headers=creator_cookie)
    assert "Fern" in offers.text
    row = db.memory_shops[shop["id"]]
    row.sales_reporting = "manual"
    row.sells_on_shopify = False
    row.manual_approved = False
    hidden = client.get("/creator/offers", headers=creator_cookie)
    assert "Fern" not in hidden.text
    assert LABEL not in hidden.text
    links = client.get("/creator/links", headers=creator_cookie)
    assert "Fern" not in links.text or "plantr" not in links.text.lower()
    blocked = client.post(
        "/seller/products",
        data={"csrf_token": _token(client.get("/seller/products", headers=cookie).text), "title": "Another"},
        headers=cookie,
    )
    assert blocked.status_code == 403
    assert "approves manual sales reporting" in blocked.text
    sales = client.get("/seller/sales", headers=cookie)
    assert sales.status_code == 200
    assert "Sales report" in sales.text
    assert "Download the template" in sales.text
    page = client.get("/admin/manual-reporting", headers=admin_cookie)
    assert "Approved manual sellers: 0" in page.text
    assert "Hidden Ferns" in page.text
    approved = client.post(
        f"/admin/manual-reporting/sellers/{shop['id']}/approve",
        data={"csrf_token": _token(page.text)},
        headers=admin_cookie,
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert row.manual_approved is True
    assert row.manual_approved_by == EMAIL
    assert any(event.event_name == "manual_seller_approved" for event in db.memory_events)
    still_hidden = client.get("/creator/offers", headers=creator_cookie)
    assert "Fern" not in still_hidden.text
    monkeypatch.setenv("MANUAL_REPORTING_LIVE", "true")
    shown = client.get("/creator/offers", headers=creator_cookie)
    assert "Fern" in shown.text
    assert LABEL in shown.text
    assert client.post(
        "/seller/products",
        data={
            "csrf_token": _token(client.get("/seller/products", headers=cookie).text),
            "title": "Second fern",
            "category": "home",
            "price": "12",
            "url": "https://brand.example/second",
            "why_chosen": "A second plant with a clear reason",
            "value_to_creators": "Short and easy to mention",
            "commission": "10",
        },
        headers=cookie,
        follow_redirects=False,
    ).status_code == 303


def test_cap_requires_confirmation_to_exceed(monkeypatch):
    monkeypatch.setenv("NON_SHOPIFY_CAP", "2")
    admin_cookie = _admin(monkeypatch)
    ids = []
    for index in range(3):
        shop = client.post("/shops", json={"name": f"Manual {index}"}).json()
        row = db.memory_shops[shop["id"]]
        row.sales_reporting = "manual"
        row.sells_on_shopify = False
        row.manual_approved = index < 2
        ids.append(shop["id"])
    page = client.get("/admin/manual-reporting", headers=admin_cookie)
    assert "Approved manual sellers: 2. The cap is 2." in page.text
    assert "goes over the cap" in page.text
    refused = client.post(
        f"/admin/manual-reporting/sellers/{ids[2]}/approve",
        data={"csrf_token": _token(page.text)},
        headers=admin_cookie,
    )
    assert refused.status_code == 409
    assert "cap is 2" in refused.text
    assert db.memory_shops[ids[2]].manual_approved is False
    allowed = client.post(
        f"/admin/manual-reporting/sellers/{ids[2]}/approve",
        data={"csrf_token": _token(refused.text), "confirm_over_cap": "yes"},
        headers=admin_cookie,
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    assert db.memory_shops[ids[2]].manual_approved is True


def _csv(code, **overrides):
    row = {
        "order_id": "A-1",
        "order_date": "2026-10-15",
        "discount_code": code,
        "product_sku_or_name": "Fern",
        "quantity": "1",
        "gross_amount_aud": "20.00",
        "shipping_aud": "5.00",
        "gst_aud": "1.50",
        "net_amount_aud": "13.50",
    }
    row.update(overrides)
    header = ",".join(manual_reporting.COLUMNS)
    values = ",".join(row[name] for name in manual_reporting.COLUMNS)
    return f"{header}\n{values}\n".encode()


def test_csv_rejects_the_whole_file_and_a_good_file_can_be_replaced_while_received(monkeypatch):
    shop, cookie = _seller(monkeypatch, name="Report Ferns")
    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    product_id, creator, _creator_cookie = _product_and_offer(shop, cookie, admin_cookie)
    link = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/fern"},
        headers=admin_cookie,
    )
    assert link.status_code == 200
    code = link.json()["discount_code"]
    row = db.memory_shops[shop["id"]]
    row.sales_reporting = "manual"
    row.sells_on_shopify = False
    row.manual_approved = True
    page = client.get("/seller/sales?period=2026-10", headers=cookie)
    assert "No sales report for this month yet." in page.text
    assert "October 2026" in page.text
    bad = client.post(
        "/seller/sales",
        data={"csrf_token": _token(page.text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code, order_date="2026-11-02", net_amount_aud="10.00", discount_code="NOPE"), "text/csv")},
        headers=cookie,
    )
    assert bad.status_code == 422
    assert "was not saved" in bad.text
    assert "not in October 2026" in bad.text
    assert "not a collab code for this shop" in bad.text
    assert "gross minus shipping minus GST" in bad.text
    assert "A$13.50" in bad.text
    assert db.memory_sales_reports == []
    excel = client.post(
        "/seller/sales",
        data={"csrf_token": _token(bad.text), "period": "2026-10"},
        files={"file": ("sales.xlsx", b"PK\x03\x04not-a-csv", "application/vnd.ms-excel")},
        headers=cookie,
    )
    assert excel.status_code == 422
    assert "Excel" in excel.text
    uploaded = client.post(
        "/seller/sales",
        data={"csrf_token": _token(excel.text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code), "text/csv")},
        headers=cookie,
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    shown = client.get("/seller/sales?period=2026-10", headers=cookie)
    assert "Received." in shown.text
    assert "A$13.50" in shown.text
    assert "Accepted." not in shown.text
    replaced = client.post(
        "/seller/sales",
        data={"csrf_token": _token(shown.text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code, order_id="A-2"), "text/csv")},
        headers=cookie,
        follow_redirects=False,
    )
    assert replaced.status_code == 303
    assert len(db.memory_sales_reports) == 1
    assert "A-2" in db.memory_sales_reports[0].csv_text
    assert any(event.event_name == "sales_report_submitted" and event.properties.get("replaced") for event in db.memory_events)
    none = client.post(
        "/seller/sales/none",
        data={"csrf_token": _token(client.get("/seller/sales?period=2026-10", headers=cookie).text), "period": "2026-10"},
        headers=cookie,
        follow_redirects=False,
    )
    assert none.status_code == 303
    quiet = client.get("/seller/sales?period=2026-10", headers=cookie)
    assert "Received." in quiet.text
    assert "No sales this month." in quiet.text


def test_shopify_sync_cannot_upload_and_admin_rejects_with_a_reason(monkeypatch):
    shop, cookie = _seller(monkeypatch, name="Sync Shop")
    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    product_id, creator, _creator_cookie = _product_and_offer(shop, cookie, admin_cookie)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/fern"},
        headers=admin_cookie,
    ).json()["discount_code"]
    row = db.memory_shops[shop["id"]]
    row.sales_reporting = "shopify_sync"
    row.sells_on_shopify = True
    blocked = client.post(
        "/seller/sales",
        data={"csrf_token": _token(client.get("/seller", headers=cookie).text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code), "text/csv")},
        headers=cookie,
    )
    assert blocked.status_code == 403
    assert "sync from Shopify" in blocked.text
    row.sales_reporting = "manual"
    row.sells_on_shopify = False
    row.manual_approved = True
    uploaded = client.post(
        "/seller/sales",
        data={"csrf_token": _token(client.get("/seller/sales?period=2026-10", headers=cookie).text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code, order_id="SYNC-1"), "text/csv")},
        headers=cookie,
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    report_id = db.memory_sales_reports[0].id
    review = client.get("/admin/manual-reporting", headers=admin_cookie)
    assert "A$13.50" in review.text
    assert "October 2026" in review.text
    rejected = client.post(
        f"/admin/manual-reporting/reports/{report_id}/reject",
        data={"csrf_token": _token(review.text), "reason": ""},
        headers=admin_cookie,
    )
    assert rejected.status_code == 422
    done = client.post(
        f"/admin/manual-reporting/reports/{report_id}/reject",
        data={"csrf_token": _token(rejected.text), "reason": "The net amount does not match the till."},
        headers=admin_cookie,
        follow_redirects=False,
    )
    assert done.status_code == 303
    seller = client.get("/seller/sales?period=2026-10", headers=cookie)
    assert "Rejected." in seller.text
    assert "does not match the till" in seller.text
    assert "received" not in seller.text.lower() or "Rejected." in seller.text
    again = client.post(
        "/seller/sales",
        data={"csrf_token": _token(seller.text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code, order_id="SYNC-2"), "text/csv")},
        headers=cookie,
    )
    assert again.status_code == 409
    assert "already" in again.text
    assert any(event.event_name == "sales_report_rejected" for event in db.memory_events)


def test_code_only_credits_once_and_net_is_not_gross(monkeypatch):
    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/p"},
        headers=headers,
    ).json()["discount_code"]
    other = client.post("/onboard-influencer", json={**INFLUENCER, "handle": "@other-manual"}).json()
    other_product = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "title": "Other", "attributes": {"url": "https://brand.example/other"}},
    ).json()["product_id"]
    other_code = client.post(
        "/creator-product-links",
        json={"creator_id": other["id"], "product_id": other_product, "destination_url": "https://brand.example/other"},
        headers=headers,
    ).json()["discount_code"]
    code_only = (
        f"order_id,date,code,gross,currency,shop\n"
        f"C-1,2020-01-02,{code},20.00,AUD,{shop['id']}\n"
    )
    imported = client.post("/attributed-orders/import", json={"csv": code_only}, headers=headers)
    assert imported.json()["created"] == 1
    order = next(row for row in db.memory_attributed_orders if row.external_order_id == "C-1")
    assert order.attributed is True
    assert order.creator_id == creator["id"]
    assert order.click_window_source == "code"
    link_only = (
        f"order_id,date,link,gross,currency,shop\n"
        f"L-1,2020-01-02,/r/{code},8,AUD,{shop['id']}\n"
    )
    missed = client.post("/attributed-orders/import", json={"csv": link_only}, headers=headers)
    assert missed.json()["created"] == 1
    link_order = next(row for row in db.memory_attributed_orders if row.external_order_id == "L-1")
    assert link_order.attributed is False
    both = (
        f"order_id,date,code,link,gross,currency,shop\n"
        f"B-1,2026-11-01,{code},/r/{other_code},30,AUD,{shop['id']}\n"
    )
    once = client.post("/attributed-orders/import", json={"csv": both}, headers=headers)
    assert once.json()["created"] == 1
    shared = next(row for row in db.memory_attributed_orders if row.external_order_id == "B-1")
    assert shared.attributed is True
    assert shared.creator_id == creator["id"]
    assert shared.click_window_source == "code"
    repeat = client.post("/attributed-orders/import", json={"csv": both}, headers=headers)
    assert repeat.json()["created"] == 0
    assert repeat.json()["existing"] == 1
    assert sum(1 for row in db.memory_attributed_orders if row.external_order_id == "B-1") == 1
    net_csv = (
        f"order_id,date,code,gross,net,shipping,gst,currency,shop\n"
        f"N-1,2026-10-15,{code},20.00,13.50,5.00,1.50,AUD,{shop['id']}\n"
    )
    netted = client.post("/attributed-orders/import", json={"csv": net_csv}, headers=headers)
    assert netted.json()["created"] == 1
    stored = next(row for row in db.memory_attributed_orders if row.external_order_id == "N-1")
    assert float(stored.net_gmv) == 13.5
    assert float(stored.gmv_gross) == 13.5
    assert float(stored.raw_payload["reported_gross"]) == 20.0
    assert float(stored.shipping_excluded) == 5
    assert float(stored.tax_excluded) == 1.5
    assert manual_reporting.format_aud("14.5") == "A$14.50"
    assert manual_reporting.format_aud(stored.net_gmv) == "A$13.50"


def test_shopify_sync_attribution_is_unchanged_and_reimport_does_not_double_count(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/p"},
        headers=headers,
    ).json()["discount_code"]
    with db.session_scope() as session:
        stored = session.get(Shop, shop["id"])
        stored.sales_reporting = "shopify_sync"
        stored.sells_on_shopify = True
        session.flush()
        code_sale = shopify_sync.attribute(
            session,
            shop["id"],
            {"id": 1, "discount_codes": [{"code": code}]},
            datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        assert code_sale["attributed"] is True
        assert code_sale["method"] == "code"
        link_sale = shopify_sync.attribute(
            session,
            shop["id"],
            {"id": 2, "landing_site": f"/products/hat?pc_ref={code}"},
            datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        assert link_sale["attributed"] is False
        assert link_sale["method"] == "link"
        both = shopify_sync.attribute(
            session,
            shop["id"],
            {"id": 3, "discount_codes": [{"code": code}], "landing_site": f"/p?pc_ref={code}"},
            datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        assert both["method"] == "link+code"
        assert both["attributed"] is True
        assert both["link"].creator_id == creator["id"]
    columns = {column["name"] for column in inspect(db.engine).get_columns("shops")}
    assert {"sells_on_shopify", "sales_reporting", "manual_approved", "manual_approved_by", "manual_approved_at"} <= columns
    assert "sales_reports" in inspect(db.engine).get_table_names()
    csv_text = (
        f"order_id,date,code,gross,net,shipping,gst,currency,shop\n"
        f"R-1,2026-10-15,{code},20.00,13.50,5.00,1.50,AUD,{shop['id']}\n"
    )
    first = client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers)
    assert first.json()["created"] == 1
    second = client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers)
    assert second.json()["existing"] == 1
    assert second.json()["created"] == 0
    with db.session_scope() as session:
        orders = session.scalars(select(AttributedOrder)).all()
        assert len(orders) == 1
        assert float(orders[0].net_gmv) == 13.5
        assert float(orders[0].gmv_gross) == 13.5


def test_approved_manual_file_imports_and_statements_use_net(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    admin_cookie = _admin(monkeypatch)
    shop = client.post("/shops", json={"name": "Statement Ferns", "primary_category": "home"}).json()
    with db.session_scope() as session:
        session.get(Shop, shop["id"]).subscription_status = "active"
    from tests.test_portal import _meta_csrf

    admin_csrf = _meta_csrf(client.get("/admin", headers=admin_cookie).text)
    email = "statement-seller@example.com"
    assert client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": admin_csrf, "email": email, "password": PASSWORD, "password_confirm": PASSWORD},
        headers=admin_cookie,
    ).status_code == 200
    login = client.get("/seller")
    signed = client.post(
        "/seller/login",
        data={"email": email, "password": PASSWORD, "csrf_token": _token(login.text)},
        follow_redirects=False,
    )
    cookie = _cookie(signed, "plantr_seller")
    page = client.get("/seller/products", headers=cookie)
    saved = client.post(
        "/seller/products",
        data={
            "csrf_token": _token(page.text),
            "title": "Fern",
            "category": "home",
            "price": "40",
            "url": "https://brand.example/fern",
            "why_chosen": "It is the plant we want shown",
            "value_to_creators": "Easy to film",
            "commission": "10",
        },
        headers=cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303, saved.text
    with db.session_scope() as session:
        from models import Product

        product = session.scalars(select(Product).where(Product.shop_id == shop["id"])).one()
        product_id = product.id
        shop_row = session.get(Shop, shop["id"])
        shop_row.sales_reporting = "manual"
        shop_row.sells_on_shopify = False
        shop_row.manual_approved = True
    creator = client.post("/onboard-influencer", json={**INFLUENCER, "handle": "@statement"}).json()
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/fern"},
        headers=admin_cookie,
    ).json()["discount_code"]
    uploaded = client.post(
        "/seller/sales",
        data={"csrf_token": _token(client.get("/seller/sales?period=2026-10", headers=cookie).text), "period": "2026-10"},
        files={"file": ("sales.csv", _csv(code, order_id="ST-1"), "text/csv")},
        headers=cookie,
        follow_redirects=False,
    )
    assert uploaded.status_code == 303, uploaded.text
    with db.session_scope() as session:
        from models import SalesReport

        report_id = session.scalars(select(SalesReport)).one().id
    review = client.get("/admin/manual-reporting", headers=admin_cookie)
    accepted = client.post(
        f"/admin/manual-reporting/reports/{report_id}/approve",
        data={"csrf_token": _token(review.text)},
        headers=admin_cookie,
        follow_redirects=False,
    )
    assert accepted.status_code == 303, accepted.text
    again = client.post(
        f"/admin/manual-reporting/reports/{report_id}/approve",
        data={"csrf_token": _token(client.get("/admin/manual-reporting", headers=admin_cookie).text)},
        headers=admin_cookie,
    )
    assert again.status_code == 409
    with db.session_scope() as session:
        orders = session.scalars(select(AttributedOrder)).all()
        assert len(orders) == 1
        assert float(orders[0].net_gmv) == 13.5
        assert orders[0].ingest_source == "manual_sales"
        assert orders[0].attributed is True
        statement = session.scalars(select(CommissionStatement)).one()
        assert float(statement.net_sales) == 13.5
        assert float(statement.amount_owed) == pytest.approx(1.35)
    seller = client.get("/seller/sales?period=2026-10", headers=cookie)
    assert "Accepted." in seller.text
    assert "A$13.50" in seller.text
    assert "Rejected" not in seller.text
