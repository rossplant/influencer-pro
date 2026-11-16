"""Tracking links, the public redirect, and CSV order import."""

import db
import events
from test_api import INFLUENCER, PRODUCT, client
from tests.test_admin import _cookie_header, _login, _setup


def _catalogue():
    shop = client.post("/shops", json={"name": "Pilot Shop", "country": "US", "market": "US"}).json()
    creator = client.post("/onboard-influencer", json={**INFLUENCER, "handle": "@pilot"}).json()
    enriched = client.post(
        "/enrich",
        json={
            **PRODUCT,
            "shop_id": shop["id"],
            "price": 25,
            "source": "manual",
            "attributes": {"url": "https://brand.example/products/sample"},
        },
    )
    assert enriched.status_code == 200
    return shop, creator, enriched.json()["product_id"]


def _session(monkeypatch):
    _setup(monkeypatch)
    return _cookie_header(_login())


def test_accept_issues_the_link_and_backfill_is_idempotent(monkeypatch):
    import attribution

    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    matched = client.post(
        "/match",
        json={"product_id": product_id, "shop_id": shop["id"], "all_onboarded": True},
        headers=headers,
    )
    assert matched.status_code == 200
    item_id = matched.json()["matches"][0]["shortlist_item_id"]
    assert client.post(f"/shortlist-items/{item_id}/accept", headers=headers).status_code == 200
    links = client.get("/creator-product-links", params={"creator_id": creator["id"]}, headers=headers)
    assert links.status_code == 200
    assert len(links.json()) == 1
    assert links.json()[0]["shortlist_item_id"] == item_id
    assert links.json()[0]["link_url"] == "https://brand.example/products/sample"
    db.memory_creator_product_links.clear()
    assert attribution.backfill_accepted_links() == 1
    assert attribution.backfill_accepted_links() == 0
    assert len(db.memory_creator_product_links) == 1
    assert db.memory_creator_product_links[0].shortlist_item_id == item_id


def test_link_is_idempotent_and_redirect_logs_a_click(monkeypatch):
    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    assert client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id},
    ).status_code == 401

    created = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id},
        headers=headers,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    assert body["tracking_path"] == f"/r/{body['discount_code']}"
    assert body["link_url"] == "https://brand.example/products/sample"
    assert body["tracking_url"].endswith(body["tracking_path"])

    again = client.post(
        "/creator-product-links",
        json={
            "creator_id": creator["id"],
            "product_id": product_id,
            "destination_url": "https://other.example/nope",
        },
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["discount_code"] == body["discount_code"]
    assert again.json()["link_url"] == body["link_url"]

    listed = client.get("/creator-product-links", params={"creator_id": creator["id"]}, headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
    assert client.get("/creator-product-links", params={"shop_id": shop["id"]}, headers=headers).json()[0]["id"] == body["id"]

    monkeypatch.setenv("ENV", "production")
    redirect = client.get(body["tracking_path"], follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://brand.example/products/sample"
    assert any(event.event_name == "link_clicked" for event in db.memory_events)
    assert client.get("/r/not-a-code", follow_redirects=False).status_code == 404

    page = client.get("/admin", headers=headers)
    assert "/admin/collabs/links" in page.text
    assert "/admin/sales/orders" in page.text


def test_csv_import_is_idempotent_and_reports_row_errors(monkeypatch):
    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id},
        headers=headers,
    ).json()["discount_code"]
    other = client.post("/shops", json={"name": "Other Shop"}).json()
    other_creator = client.post("/onboard-influencer", json={**INFLUENCER, "handle": "@other"}).json()
    other_product = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": other["id"], "title": "Other item", "attributes": {"url": "https://other.example/p"}},
    ).json()["product_id"]
    other_code = client.post(
        "/creator-product-links",
        json={"creator_id": other_creator["id"], "product_id": other_product},
        headers=headers,
    ).json()["discount_code"]
    csv_text = "\n".join(
        [
            "order id,date,code,gross amount,currency,shop",
            f"1001,2026-11-01,{code},45.50,usd,{shop['id']}",
            f"1001,2026-11-02,{code},10,USD,{shop['name']}",
            f"1002,2026-11-03,MISSING,10,USD,{shop['id']}",
            f"1001,2026-11-04,{other_code},12,USD,{other['id']}",
        ]
    )
    dry = client.post("/attributed-orders/import", json={"csv": csv_text, "dry_run": True}, headers=headers)
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    assert [row["status"] for row in dry.json()["rows"]] == ["created", "existing", "error", "created"]
    assert db.memory_attributed_orders == []

    imported = client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers)
    assert imported.status_code == 200
    body = imported.json()
    assert body["created"] == 2
    assert body["existing"] == 1
    assert body["errors"] == 1
    assert "Unknown code" in body["rows"][2]["error"]
    assert len(db.memory_attributed_orders) == 2
    assert {row.shop_id for row in db.memory_attributed_orders if row.external_order_id == "1001"} == {shop["id"], other["id"]}
    first = next(row for row in db.memory_attributed_orders if row.external_order_id == "1001" and row.shop_id == shop["id"])
    assert float(first.gmv_gross) == 45.5
    assert first.currency == "USD"
    assert first.creator_id == creator["id"]
    assert first.product_id == product_id
    assert first.ingest_source == "csv"
    repeat = client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers)
    assert repeat.json()["created"] == 0
    assert repeat.json()["existing"] == 3
    assert len(db.memory_attributed_orders) == 2
    orders = client.get("/admin/api/orders", headers=headers)
    assert orders.status_code == 200
    assert orders.json()["total"] == 0
    assert orders.json()["gmv"]["net_gmv"] == 0
    assert float(first.gmv_gross) == 45.5


def test_import_persists_one_order_per_shop(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from tests.test_persistence import use_database

    use_database(tmp_path, monkeypatch)
    order_day = datetime.now(timezone.utc).date().isoformat()
    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id, "destination_url": "https://brand.example/p"},
        headers=headers,
    ).json()["discount_code"]
    assert client.get(f"/r/{code}", follow_redirects=False).status_code == 302
    csv_text = f"order_id,date,link,gross,currency,shop\n55,{order_day},/r/{code},19,USD,{shop['id']}\n"
    assert client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers).json()["created"] == 1
    assert client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers).json()["existing"] == 1
    with db.session_scope() as session:
        from models import AttributedOrder
        from sqlalchemy import select

        orders = session.scalars(select(AttributedOrder)).all()
        assert len(orders) == 1
        assert orders[0].external_order_id == "55"
        assert orders[0].shortlist_item_id is None or isinstance(orders[0].shortlist_item_id, str)
        shop_row = session.get(__import__("models").Shop, shop["id"])
        assert shop_row.first_attributed_sale_at is not None
        names = set(session.scalars(select(events.Event.event_name)).all())
    assert "attributed_sale" in names


def test_csv_import_records_csv_source_and_refunds_update_the_order(monkeypatch):
    import attribution
    from datetime import datetime, timezone

    from fastapi import HTTPException

    order_day = datetime.now(timezone.utc).date().isoformat()

    shop, creator, product_id = _catalogue()
    headers = _session(monkeypatch)
    code = client.post(
        "/creator-product-links",
        json={"creator_id": creator["id"], "product_id": product_id},
        headers=headers,
    ).json()["discount_code"]
    assert client.get(f"/r/{code}", follow_redirects=False).status_code == 302
    csv_text = f"order_id,date,code,gross,currency,shop\n77,{order_day},{code},20,AUD,{shop['id']}\n"
    imported = client.post("/attributed-orders/import", json={"csv": csv_text}, headers=headers)
    assert imported.json()["created"] == 1
    order = db.memory_attributed_orders[-1]
    assert order.click_window_days == 7
    assert order.click_window_source == "code"
    assert order.net_gmv == 20
    sale = next(row for row in db.memory_events if row.event_name == "attributed_sale" and row.properties.get("order_id") == "77")
    assert sale.properties["source"] == "csv_import"
    adjusted = client.post(
        f"/admin/api/orders/{order.id}/adjustment",
        json={"refunded_amount": 5, "cancelled_amount": 2},
        headers=headers,
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["net_gmv"] == 13
    assert sale.properties["refunded"] is False
    try:
        attribution._validated_row(
            {
                "order_id": "78",
                "date": order_day,
                "code": code,
                "gross": "10",
                "currency": "AUD",
                "shop": shop["id"],
                "click_window_days": "3",
            }
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("a non-7-day click window was accepted")
    missed = client.post(
        "/attributed-orders/import",
        json={"csv": f"order_id,date,code,gross,currency,shop\n88,2026-01-01,{code},8,AUD,{shop['id']}\n"},
        headers=headers,
    )
    assert missed.json()["created"] == 1
    outside = next(row for row in db.memory_attributed_orders if row.external_order_id == "88")
    assert outside.attributed is False
    assert float(outside.gmv_gross) == 8
    assert not any(
        row.event_name == "attributed_sale" and row.properties.get("order_id") == "88" for row in db.memory_events
    )
    report = client.get("/admin/api/orders", headers=headers)
    assert report.status_code == 200
    assert report.json()["gmv"]["orders"] == 1
    assert report.json()["gmv"]["gmv_gross"] == 20
    assert report.json()["gmv"]["net_gmv"] == 13
    assert all(item["external_order_id"] != "88" for item in report.json()["items"])
