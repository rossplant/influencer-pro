"""Source platforms, direct seller URLs, and Spark Ads codes."""

import csv
import io
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import inspect, select

import attribution
import commission
import db
import main
import shopify_sync
import spark_ads
from models import AttributedOrder, CollabSparkAd, CommissionAuditLog, Creator, CreatorProductLink, Event, Product, Shop
from test_api import client
from tests.test_shopify_sync import VALID_ABN, _connect_bridge, _creator_headers, _order, _seller_headers, _send

SECRET_URL = "https://sunny.example/products/hat?plantr=PCNINA0001&s=tt&c=ad&utm_email=buyer@secret.example"
SPARK = "SPARK-TEST-CODE-001"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'plantr.db'}")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SHOPIFY_TOKEN_KEY", Fernet.generate_key().decode())
    for name in ("SHOPIFY_API_KEY", "SHOPIFY_API_SECRET", "COMMISSION_OVERDUE_RULE", "COMMISSION_EXCLUDE_PLATFORM_PAID",
                 "PAYS_ON_TIME_VISIBILITY", "COMMISSION_CONFIRM_WINDOW_DAYS", "COMMISSION_DISPUTE_RESOLVE_AFTER_DAYS",
                 "PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    main.init_storage()
    ids = {"shop": str(uuid.uuid4()), "creator": str(uuid.uuid4()), "creator2": str(uuid.uuid4()), "product": str(uuid.uuid4())}
    with db.session_scope() as session:
        session.add(Shop(id=ids["shop"], name="Sunny Goods", currency="AUD", country="AU"))
        for key, handle, abn in (("creator", "@nina", None), ("creator2", "@omar", VALID_ABN)):
            session.add(Creator(
                id=ids[key], handle=handle, interests=[], expertise_level={}, preferred_promotion_styles=[],
                content_formats=[], past_categories=[], restrictions=[], excluded_categories=[], excluded_claims=[],
                platforms=[], profile={}, abn=abn,
            ))
        session.flush()
        session.add(Product(
            id=ids["product"], shop_id=ids["shop"], title="Sun Hat", source="manual", commission_rate=10,
            images=[], tags=[], claims=[], attributes={"url": "https://sunny.example/products/hat"},
        ))
        session.flush()
        for key, code in (("creator", "PCNINA0001"), ("creator2", "PCOMAR0002")):
            link_id = str(uuid.uuid4())
            ids[f"link_{key}"] = link_id
            session.add(CreatorProductLink(
                id=link_id, shop_id=ids["shop"], product_id=ids["product"], creator_id=ids[key],
                link_url="https://sunny.example/products/hat", discount_code=code, platform="plantr",
            ))
    return ids


def _orders():
    with db.session_scope() as session:
        return {order.external_order_id: order for order in session.scalars(select(AttributedOrder)).all()}


def _dump(order) -> str:
    data = {column.name: getattr(order, column.name) for column in order.__table__.columns}
    return json.dumps(data, default=str)


def _click(env, key="creator", when=None, source="unknown", channel="link"):
    codes = {"creator": "PCNINA0001", "creator2": "PCOMAR0002"}
    with db.session_scope() as session:
        session.add(Event(
            id=str(uuid.uuid4()),
            occurred_at=when or datetime(2026, 10, 9, tzinfo=timezone.utc),
            event_name="link_clicked",
            entity_type="creator_product_link",
            entity_id=env[f"link_{key}"],
            shop_id=env["shop"],
            actor="api",
            properties={
                "discount_code": codes[key],
                "creator_id": env[key],
                "product_id": env["product"],
                "source_platform": source,
                "channel_kind": channel,
            },
        ))


def _path(env):
    return _connect_bridge(env)["webhook_path"]


def test_redirect_records_platform_and_keeps_the_product_url(env):
    plain = client.get("/r/PCNINA0001?s=ig", follow_redirects=False)
    assert plain.status_code == 302
    assert plain.headers["location"] == "https://sunny.example/products/hat"
    _path(env)
    ad = client.get("/r/PCNINA0001?s=tt&c=ad", follow_redirects=False)
    assert ad.headers["location"] == "https://sunny.example/products/hat?pc_ref=PCNINA0001"
    with db.session_scope() as session:
        clicks = session.scalars(select(Event).where(Event.event_name == "link_clicked").order_by(Event.occurred_at)).all()
    assert clicks[0].properties["source_platform"] == "instagram"
    assert clicks[0].properties["channel_kind"] == "link"
    assert clicks[1].properties["source_platform"] == "tiktok"
    assert clicks[1].properties["channel_kind"] == "ad"
    untagged = client.get("/r/PCNINA0001", follow_redirects=False)
    assert untagged.status_code == 302
    with db.session_scope() as session:
        last = session.scalars(select(Event).where(Event.event_name == "link_clicked").order_by(Event.occurred_at.desc())).first()
    assert last.properties["source_platform"] == "unknown"
    assert last.properties["channel_kind"] == "link"


def test_creator_api_and_portal_issue_a_link_per_platform_and_a_direct_url(env):
    body = client.get("/creator/api/links", headers=_creator_headers(env["creator"])).json()["links"][0]
    assert body["tracking_path"] == "/r/PCNINA0001"
    assert body["direct_url"] == "https://sunny.example/products/hat?plantr=PCNINA0001"
    by_param = {row["param"]: row for row in body["platform_links"]}
    assert set(by_param) == {"tt", "ig", "yt", "fb", "ot"}
    assert by_param["tt"]["tracking_path"] == "/r/PCNINA0001?s=tt"
    assert by_param["tt"]["source_platform"] == "tiktok"
    assert by_param["ig"]["direct_url"] == "https://sunny.example/products/hat?plantr=PCNINA0001&s=ig"
    page = client.get("/creator/links", headers={"Cookie": _creator_headers(env["creator"])["Cookie"]})
    assert page.status_code == 200
    assert "http://testserver/r/PCNINA0001?s=tt" in page.text
    assert "http://testserver/r/PCNINA0001?s=ig" in page.text
    assert "plantr=PCNINA0001&amp;s=yt" in page.text
    assert "http://testserver/r/PCNINA0001" in page.text


def test_direct_landing_is_the_click_and_the_url_is_not_stored(env, caplog):
    caplog.set_level(logging.DEBUG)
    path = _path(env)
    sent = _send(path, "orders/create", _order(7001, landing=SECRET_URL), webhook_id="direct-1")
    assert sent.json()["status"] == "created"
    order = _orders()["7001"]
    assert order.attributed and order.attribution_method == "link"
    assert order.source_platform == "tiktok" and order.channel_kind == "ad"
    assert order.creator_id == env["creator"]
    dumped = _dump(order)
    assert "buyer@secret.example" not in dumped
    assert "utm_email" not in dumped
    assert "secret.example" not in dumped
    assert SECRET_URL not in dumped
    assert "buyer@secret.example" not in caplog.text
    with db.session_scope() as session:
        clicks = session.scalars(select(Event).where(Event.event_name == "link_clicked")).all()
    assert clicks == []


def test_referring_site_plantr_param_credits_when_landing_has_none(env):
    path = _path(env)
    order = _order(7002, landing="/products/hat")
    order["referring_site"] = "https://www.tiktok.com/out?plantr=PCNINA0001&s=tt"
    assert _send(path, "orders/create", order, webhook_id="ref-1").json()["status"] == "created"
    stored = _orders()["7002"]
    assert stored.attributed and stored.source_platform == "tiktok" and stored.channel_kind == "link"
    assert "tiktok.com" not in _dump(stored)


def test_landing_plantr_wins_over_an_older_click_platform(env):
    _click(env, source="instagram", channel="link")
    path = _path(env)
    _send(path, "orders/create", _order(7003, landing="https://sunny.example/products/hat?plantr=PCNINA0001&s=tt&c=ad"), webhook_id="ad-over-click")
    order = _orders()["7003"]
    assert order.source_platform == "tiktok" and order.channel_kind == "ad"


def test_pc_ref_uses_the_latest_click_platform_and_still_needs_the_window(env):
    path = _path(env)
    _send(path, "orders/create", _order(7101, landing="/products/hat?pc_ref=PCNINA0001"), webhook_id="no-click")
    assert _orders()["7101"].attributed is False
    _click(env, when=datetime(2026, 10, 8, tzinfo=timezone.utc), source="instagram")
    _click(env, when=datetime(2026, 10, 9, 12, tzinfo=timezone.utc), source="tiktok")
    _send(path, "orders/create", _order(7102, landing="/products/hat?pc_ref=PCNINA0001"), webhook_id="latest-click")
    credited = _orders()["7102"]
    assert credited.attributed and credited.attribution_method == "link"
    assert credited.source_platform == "tiktok" and credited.channel_kind == "link"
    _click(env, key="creator2", when=datetime(2026, 8, 20, tzinfo=timezone.utc), source="youtube")
    _send(path, "orders/create", _order(7104, landing="/products/hat?pc_ref=PCOMAR0002", created="2026-09-01T00:00:00Z"), webhook_id="outside-window")
    assert _orders()["7104"].attributed is False


def test_ad_redirect_click_credits_like_a_link_inside_the_window(env):
    _click(env, source="tiktok", channel="ad")
    path = _path(env)
    _send(path, "orders/create", _order(7201, landing="/products/hat?pc_ref=PCNINA0001"), webhook_id="ad-click")
    order = _orders()["7201"]
    assert order.attributed and order.channel_kind == "ad" and order.source_platform == "tiktok"
    assert float(order.gmv_gross) == 100.00


def test_platform_code_sets_platform_and_untagged_code_says_platform_not_known(env):
    seller = _seller_headers(env["shop"])
    created = client.post(
        f"/seller/api/links/{env['link_creator']}/platform-codes",
        json={"code": "mia-tt", "source_platform": "tiktok"},
        headers=seller,
    )
    assert created.status_code == 200, created.text
    assert created.json()["code"] == "MIA-TT"
    assert client.post(
        f"/seller/api/links/{env['link_creator']}/platform-codes",
        json={"code": "PCNINA0001", "source_platform": "tiktok"},
        headers=seller,
    ).status_code == 409
    path = _path(env)
    _send(path, "orders/create", _order(7301, codes=["MIA-TT"]), webhook_id="platform-code")
    tagged = _orders()["7301"]
    assert tagged.creator_id == env["creator"] and tagged.attribution_method == "code"
    assert tagged.source_platform == "tiktok" and tagged.channel_kind == "code"
    assert tagged.attribution_note is None
    _send(path, "orders/create", _order(7302, codes=["PCNINA0001"]), webhook_id="plain-code")
    plain = _orders()["7302"]
    assert plain.channel_kind == "code" and plain.source_platform == "unknown"
    assert plain.attribution_note == "code, platform not known"
    assert float(tagged.gmv_gross) == float(plain.gmv_gross) == 100.00


def test_code_beats_an_ad_landing_and_one_order_is_one_credit(env):
    path = _path(env)
    _send(
        path, "orders/create",
        _order(7401, codes=["PCOMAR0002"], landing="https://sunny.example/products/hat?plantr=PCNINA0001&s=tt&c=ad"),
        webhook_id="code-beats-ad",
    )
    orders = _orders()
    assert list(orders) == ["7401"]
    order = orders["7401"]
    assert order.creator_id == env["creator2"] and order.attribution_method == "code"
    assert order.source_platform == "unknown" and order.channel_kind == "code"
    assert "code beat link" in order.attribution_note
    assert "code, platform not known" in order.attribution_note
    same = _order(7402, codes=["PCNINA0001"], landing="https://sunny.example/products/hat?plantr=PCNINA0001&s=ig")
    _send(path, "orders/create", same, webhook_id="same-creator")
    both = _orders()["7402"]
    assert both.attribution_method == "link+code" and both.creator_id == env["creator"]
    assert both.source_platform == "instagram" and both.channel_kind == "link"


def test_direct_instagram_link_and_graphql_referrer(env):
    path = _path(env)
    _send(path, "orders/create", _order(7501, landing="https://sunny.example/products/hat?plantr=PCNINA0001&s=ig"), webhook_id="ig-direct")
    order = _orders()["7501"]
    assert order.attributed and order.source_platform == "instagram" and order.channel_kind == "link"
    node = {
        "legacyResourceId": "7502", "name": "#7502", "createdAt": "2026-10-10T00:00:00Z", "updatedAt": "2026-10-10T00:00:00Z",
        "cancelledAt": None, "currencyCode": "AUD", "taxesIncluded": True, "displayFinancialStatus": "PAID",
        "discountCodes": [], "customAttributes": [],
        "customerJourneySummary": {"firstVisit": {
            "landingPage": "https://sunny.example/products/hat",
            "referrerUrl": "https://instagram.com/out?plantr=PCNINA0001&s=ig",
        }},
        "shippingLines": {"nodes": []},
        "lineItems": {"nodes": [{"quantity": 1, "isGiftCard": False, "originalUnitPriceSet": {"shopMoney": {"amount": "22.00"}},
                                 "discountAllocations": [], "taxLines": [{"priceSet": {"shopMoney": {"amount": "2.00"}}}]}]},
        "refunds": [],
    }
    rest = shopify_sync.graphql_to_rest(node)
    assert rest["referring_site"].endswith("plantr=PCNINA0001&s=ig")
    assert shopify_sync.process_order(env["shop"], rest) == "created"
    mapped = _orders()["7502"]
    assert mapped.source_platform == "instagram" and mapped.channel_kind == "link"
    assert "instagram.com" not in _dump(mapped)


def test_report_and_statement_csv_split_by_platform_without_changing_the_amount(env):
    path = _path(env)
    _send(path, "orders/create", _order(7601, landing=SECRET_URL), webhook_id="report-ad")
    _send(path, "orders/create", _order(7602, codes=["PCNINA0001"]), webhook_id="report-code")
    report = client.get("/seller/api/commission/source-report", params={"period": "2026-10"}, headers=_seller_headers(env["shop"])).json()
    assert report["totals"]["orders"] == 2
    assert report["totals"]["net_sales"] == 200.00
    assert report["totals"]["amount"] == 20.00
    rows = {(row["creator_id"], row["source_platform"]): row for row in report["rows"]}
    assert rows[(env["creator"], "tiktok")]["amount"] == 10.00
    assert rows[(env["creator"], "unknown")]["amount"] == 10.00
    created = commission.generate_statements("2026-10", now=datetime(2026, 11, 2, tzinfo=timezone.utc))
    statement_id = created["created"][0]
    detail = commission.statement_detail(statement_id, viewer="seller", shop_id=env["shop"])
    assert detail["amount_owed"] == 20.00
    downloaded = client.get(f"/seller/api/commission/statements/{statement_id}/csv", headers=_seller_headers(env["shop"]))
    assert downloaded.status_code == 200
    parsed = list(csv.DictReader(io.StringIO(downloaded.text.split("\n", 1)[1])))
    summaries = [row for row in parsed if row["section"] == "summary"]
    total = next(row for row in parsed if row["section"] == "total")
    assert float(total["commission"]) == 20.00
    assert round(sum(float(row["commission"]) for row in summaries), 2) == 20.00
    assert {row["source_platform"] for row in summaries} == {"tiktok", "unknown"}


def test_spark_code_is_encrypted_shown_only_to_the_parties_and_revoke_hides_it(env, caplog):
    caplog.set_level(logging.DEBUG)
    seller = _seller_headers(env["shop"])
    creator = _creator_headers(env["creator2"], email="omar@example.com")
    other = _creator_headers(env["creator"])
    offer = client.post(
        "/seller/api/collabs",
        json={"creator_id": env["creator2"], "product_id": env["product"], "commission_rate": 10},
        headers=seller,
    ).json()
    assert spark_ads.HELPER in offer["spark_ads_helper"]
    assert "spark_ads_helper" not in client.get("/creator/api/collabs", headers=creator).json()[0]
    until = (date.today() + timedelta(days=30)).isoformat()
    body = {
        "post_url": "https://www.tiktok.com/@omar/video/1",
        "spark_code": SPARK,
        "authorised_until": until,
        "usage_note": "Usage rights fee agreed between the parties",
        "consent": True,
    }
    assert client.post(f"/creator/api/collabs/{offer['id']}/spark-ads", json={**body, "consent": False}, headers=creator).status_code == 422
    assert client.post(f"/creator/api/collabs/{offer['id']}/spark-ads", json=body, headers=seller).status_code == 401
    assert client.post(
        f"/creator/api/collabs/{offer['id']}/spark-ads",
        json={**body, "authorised_until": (date.today() + timedelta(days=6)).isoformat()},
        headers=creator,
    ).status_code == 422
    assert client.post(
        f"/creator/api/collabs/{offer['id']}/spark-ads",
        json={**body, "authorised_until": (date.today() + timedelta(days=366)).isoformat()},
        headers=creator,
    ).status_code == 422
    assert client.post(
        f"/creator/api/collabs/{offer['id']}/spark-ads",
        json={**body, "usage_note": "BSB 062000 account 12345678"},
        headers=creator,
    ).status_code == 422
    created = client.post(f"/creator/api/collabs/{offer['id']}/spark-ads", json=body, headers=creator)
    assert created.status_code == 200, created.text
    saved = created.json()
    assert saved["spark_code"] == SPARK
    assert saved["ad_destination"] == "https://sunny.example/products/hat?plantr=PCOMAR0002&s=tt&c=ad"
    assert saved["plantr_alternative"] == "/r/PCOMAR0002?s=tt&c=ad"
    assert saved["ad_destination"].startswith("https://sunny.example/")
    assert saved["consented_at"]
    listed = client.get(f"/seller/api/collabs/{offer['id']}/spark-ads", headers=seller).json()
    assert listed["helper_text"] == spark_ads.HELPER
    assert "spark_code" not in listed["spark_ads"][0]
    assert SPARK not in json.dumps(listed)
    seller_view = client.get(f"/seller/api/collabs/{offer['id']}/spark-ads/{saved['id']}", headers=seller).json()
    assert seller_view["spark_code"] == SPARK and seller_view["helper_text"] == spark_ads.HELPER
    assert client.get(f"/creator/api/collabs/{offer['id']}/spark-ads/{saved['id']}", headers=other).status_code == 404
    with db.session_scope() as session:
        row = session.get(CollabSparkAd, saved["id"])
        assert SPARK not in row.spark_code
        assert shopify_sync.decrypt(row.spark_code) == SPARK
        audits = session.scalars(select(CommissionAuditLog)).all()
        blob = json.dumps([{"action": item.action, "details": item.details, "reason": item.reason} for item in audits])
    assert SPARK not in blob and SPARK not in caplog.text
    path = _path(env)
    _send(path, "orders/create", _order(7701, landing="https://sunny.example/products/hat?plantr=PCOMAR0002&s=tt&c=ad"), webhook_id="spark-sale")
    assert _orders()["7701"].attributed and _orders()["7701"].channel_kind == "ad"
    revoked = client.post(f"/creator/api/collabs/{offer['id']}/spark-ads/{saved['id']}/revoke", headers=creator).json()
    assert "spark_code" not in revoked and "ad_destination" not in revoked and "plantr_alternative" not in revoked
    assert revoked["status"] == "revoked"
    assert _orders()["7701"].attributed
    hidden = client.get(f"/seller/api/collabs/{offer['id']}/spark-ads/{saved['id']}", headers=seller).json()
    assert "spark_code" not in hidden and "ad_destination" not in hidden


def test_spark_code_expires_after_authorised_until(env):
    seller = _seller_headers(env["shop"])
    creator = _creator_headers(env["creator2"], email="omar@example.com")
    offer = client.post(
        "/seller/api/collabs",
        json={"creator_id": env["creator2"], "product_id": env["product"], "commission_rate": 10},
        headers=seller,
    ).json()
    created = client.post(
        f"/creator/api/collabs/{offer['id']}/spark-ads",
        json={
            "post_url": "https://www.tiktok.com/@omar/video/2",
            "spark_code": SPARK,
            "authorised_until": (date.today() + timedelta(days=7)).isoformat(),
            "consent": True,
        },
        headers=creator,
    ).json()
    with db.session_scope() as session:
        session.get(CollabSparkAd, created["id"]).authorised_until = date.today() - timedelta(days=1)
    expired = client.get(f"/creator/api/collabs/{offer['id']}/spark-ads/{created['id']}", headers=creator).json()
    assert expired["status"] == "expired"
    assert expired["spark_code"] == SPARK
    assert "ad_destination" not in expired


def test_migration_downgrade_and_upgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0010_shopify_order_sync")
    command.upgrade(cfg, "head")
    main.init_storage()
    columns = {column["name"] for column in inspect(db.engine).get_columns("attributed_orders")}
    tables = set(inspect(db.engine).get_table_names())
    assert "source_platform" in columns and "channel_kind" in columns
    assert "creator_platform_codes" in tables and "collab_spark_ads" in tables
    variants = attribution.platform_variants("PCNINA0001", "https://sunny.example/products/hat")
    assert [row["param"] for row in variants] == ["tt", "ig", "yt", "fb", "ot"]
