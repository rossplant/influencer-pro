"""Postgres-phase behaviour. CI uses SQLite; the app normalises a Postgres URL."""

import json
import logging
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select

import db
import events
import main
from models import (
    ApiRequest,
    AttributedOrder,
    Creator,
    Event,
    MonthlyMrrSnapshot,
    Product,
    Shop,
    Shortlist,
    ShortlistItem,
    SubscriptionChange,
    utcnow,
)
from test_api import INFLUENCER, PRODUCT, client

PILOT_TABLES = {
    "shops",
    "creators",
    "products",
    "events",
    "shortlists",
    "shortlist_items",
    "briefs",
    "api_requests",
    "subscriptions",
    "subscription_changes",
    "monthly_mrr_snapshots",
    "attributed_orders",
    "admin_users",
    "portal_accounts",
}


def use_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'plantr.db'}")
    main.init_storage()


def test_database_url_normalisation():
    assert db.normalise_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert db.normalise_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert db.normalise_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert db.normalise_database_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"


def test_mrr_rules():
    assert events.mrr("starter") == 0
    assert events.mrr("free") == 0
    assert events.mrr("growth", status="trial") == 0
    assert events.mrr("growth", status="trialing") == 0
    assert events.mrr("growth", status="free") == 0
    assert events.mrr("growth") == 49
    assert events.mrr("scale") == 149
    assert events.mrr("growth", interval="year") == pytest.approx(490 / 12)
    assert events.mrr("scale", interval="year") == pytest.approx(1490 / 12)
    assert events.mrr("scale", price=149, interval="year") == pytest.approx(149 / 12)
    assert events.mrr("growth", price=588, interval="year", status="active") == pytest.approx(49)


def test_startup_without_database_url(monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with caplog.at_level(logging.WARNING, logger="plantr.db"):
        main.init_storage()
    assert db.configured() is False
    assert db.engine is None
    assert any("DATABASE_URL" in record.message for record in caplog.records)

    assert client.get("/").status_code == 200
    onboard = client.post("/onboard-influencer", json=INFLUENCER)
    assert onboard.status_code == 200
    assert onboard.json()["id"] in main.INFLUENCERS

    shop = client.post("/shops", json={"name": "Local", "is_pilot": True})
    assert shop.status_code == 200
    body = shop.json()
    assert body["plan_tier"] == "starter"
    assert body["is_pilot"] is True
    assert body["mrr"] == 0
    assert client.get(f"/shops/{body['id']}").json()["name"] == "Local"

    enriched = client.post("/enrich", json={**PRODUCT, "shop_id": body["id"]})
    assert enriched.status_code == 200
    assert enriched.json()["source"] == "fallback"
    assert enriched.json()["product_id"]


def test_migration_creates_pilot_and_schema_only_tables(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    names = set(inspect(db.engine).get_table_names())
    assert PILOT_TABLES <= names
    with db.session_scope() as session:
        session.add(
            Shop(
                id="s",
                name="Schema Shop",
                plan_tier="growth",
                plan_status="active",
                is_pilot=False,
                products_enriched=0,
                matches_run=0,
                briefs_generated=0,
                created_at=utcnow(),
            )
        )
        session.flush()
        session.add(
            SubscriptionChange(
                id="c",
                shop_id="s",
                change_type="contraction",
                paid_to_free=True,
                upgrade_trigger_snapshot={"from": "growth", "to": "starter"},
                from_tier="growth",
                to_tier="starter",
                created_at=utcnow(),
            )
        )
        session.add(
            MonthlyMrrSnapshot(
                id="m",
                shop_id="s",
                month=utcnow().date().replace(day=1),
                plan_tier="starter",
                status="active",
                interval="year",
                price=0,
                mrr=0,
                created_at=utcnow(),
            )
        )
        session.add(
            AttributedOrder(
                id="o",
                shop_id="s",
                platform="tiktok",
                external_order_id="ord_1",
                gmv_gross=45,
                refunded_amount=0,
                cancelled_amount=0,
                net_gmv=45,
                currency="USD",
                attribution_date=utcnow().date(),
                click_window_days=7,
                fee_revenue=0,
                occurred_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with db.session_scope() as session:
        change = session.get(SubscriptionChange, "c")
        assert change.change_type == "contraction"
        assert change.paid_to_free is True
        assert change.upgrade_trigger_snapshot["to"] == "starter"
        order = session.get(AttributedOrder, "o")
        assert order.platform == "tiktok"
        assert order.external_order_id == "ord_1"
        assert float(order.gmv_gross) == 45
        assert "amount" not in AttributedOrder.__table__.columns


def test_emit_updates_counters_and_milestones(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    with db.session_scope() as session:
        shop = Shop(
            id="shop-1",
            name="Studio",
            plan_tier="starter",
            plan_status="active",
            is_pilot=False,
            products_enriched=0,
            matches_run=0,
            briefs_generated=0,
            created_at=utcnow(),
        )
        session.add(shop)
        session.flush()
        events.emit("product_enriched", shop, "system", product_id="p")
        first = shop.first_enrich_at
        assert shop.products_enriched == 1
        assert first is not None
        events.emit("product_enriched", shop, "operator", product_id="p")
        assert shop.products_enriched == 2
        assert shop.first_enrich_at == first
        events.emit("match_run", shop, "operator")
        events.emit("brief_sent", shop, "api")
        match = session.scalars(select(Event).where(Event.event_name == "match_run")).one()
        assert match.actor == "operator"
        assert match.entity_type == "shop"
        assert match.entity_id == "shop-1"
        assert match.shop_id == "shop-1"
        assert match.properties["plan_tier"] == "starter"
        assert match.properties["plan_status"] == "active"
        assert "products_enriched" not in match.properties
        assert match.products_count == 0
        assert match.product_limit == 50
        assert match.matches_count_period == 1
        assert match.match_limit == 5
        assert match.enrichments_count_period == 2
        assert match.briefs_count_period == 0
        assert match.billing_period_start is not None
        assert match.limit_hit is False
        assert shop.matches_run == 1
        assert shop.briefs_generated == 1
        assert shop.first_match_at is not None
        assert shop.last_match_at is not None
        assert shop.first_brief_at is not None
        assert shop.last_campaign_at is not None
        with pytest.raises(ValueError):
            events.emit("product_enriched", shop, "user")
    with db.session_scope() as session:
        shop = session.get(Shop, "shop-1")
        assert shop.products_enriched == 2
        assert shop.matches_run == 1
        assert shop.briefs_generated == 1
        assert shop.first_enrich_at is not None


def test_onboarding_persists_creator_and_emits(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    payload = {
        **INFLUENCER,
        "bio": "Makes short videos",
        "restrictions": ["no_competitor_brands"],
        "excluded_categories": ["alcohol"],
        "excluded_claims": ["clinically proven"],
        "platforms": ["tiktok"],
        "follower_band": "10k_50k",
    }
    response = client.post("/onboard-influencer", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "influencer"}
    assert body["influencer"]["influencer_id"] == body["id"]
    assert body["influencer"]["handle"] == "@creator"
    assert body["influencer"]["platforms"] == ["tiktok"]
    assert body["influencer"]["restrictions"] == ["no_competitor_brands"]
    assert body["influencer"]["excluded_categories"] == ["alcohol"]
    assert body["id"] not in main.INFLUENCERS

    with db.session_scope() as session:
        creator = session.get(Creator, body["id"])
        assert creator.handle == "@creator"
        assert creator.interests == INFLUENCER["areas_of_interest"]
        assert creator.restrictions == ["no_competitor_brands"]
        assert creator.excluded_categories == ["alcohol"]
        assert creator.excluded_claims == ["clinically proven"]
        assert creator.profile_completed_at is None
        assert creator.platforms == ["tiktok"]
        assert creator.follower_band == "10k_50k"
        assert creator.profile["influencer_id"] == body["id"]
        event = session.scalars(select(Event).where(Event.event_name == "creator_onboarded")).one()
        assert event.entity_type == "creator"
        assert event.entity_id == body["id"]
        assert event.actor == "api"
        assert event.shop_id is None

    fetched = client.get(f"/creators/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["influencer"]["handle"] == "@creator"
    assert client.get("/creators/missing").status_code == 404


def test_shop_pilot_defaults_to_starter_with_zero_mrr(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    pilot = client.post("/shops", json={"name": "Pilot Brand", "is_pilot": True})
    assert pilot.status_code == 200
    body = pilot.json()
    assert body["plan_tier"] == "starter"
    assert body["plan_status"] == "free"
    assert body["is_pilot"] is True
    assert body["mrr"] == 0
    assert client.get(f"/shops/{body['id']}").json()["name"] == "Pilot Brand"
    with db.session_scope() as session:
        signed = session.scalars(select(Event).where(Event.event_name == "shop_signed_up")).one()
        assert signed.shop_id == body["id"]
        assert signed.product_limit == 50
        assert signed.products_count == 0

    growth = client.post("/shops", json={"name": "Growth", "plan_tier": "growth"}).json()
    assert growth["plan_status"] == "active"
    assert growth["mrr"] == 49
    trial = client.post(
        "/shops",
        json={"name": "Trial", "plan_tier": "growth", "plan_status": "trial"},
    ).json()
    assert trial["mrr"] == 0
    assert client.post("/shops", json={"name": "X", "plan_tier": "enterprise"}).status_code == 422
    assert client.get("/shops/missing").status_code == 404


def test_match_records_placeholder_shortlist(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop = client.post("/shops", json={"name": "Shop"}).json()
    onboard = client.post("/onboard-influencer", json=INFLUENCER).json()
    enrich = client.post(
        "/enrich",
        json={
            **PRODUCT,
            "shop_id": shop["id"],
            "subcategory": "earbuds",
            "price": 24,
            "tags": ["wireless"],
            "attributes": {"color": "black"},
            "claims": [{"claim": "24-hour battery", "source": "brand"}],
        },
    )
    assert enrich.status_code == 200
    product_id = enrich.json()["product_id"]
    influencer = {**INFLUENCER, "influencer_id": onboard["id"], "active_categories": ["alpha"]}
    response = client.post(
        "/match",
        json={
            "product_id": product_id,
            "creators": [influencer],
            "shop_id": shop["id"],
        },
    )
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert matches[0]["creator"]["handle"] == INFLUENCER["handle"]
    assert response.json()["match_version"] == "rules-v1"
    shortlist_id = response.json()["shortlist_id"]

    with db.session_scope() as session:
        shortlist = session.get(Shortlist, shortlist_id)
        assert shortlist.match_version == "rules-v1"
        assert shortlist.shop_id == shop["id"]
        assert shortlist.product_id == product_id
        item = session.scalars(select(ShortlistItem).where(ShortlistItem.shortlist_id == shortlist_id)).one()
        assert item.creator_id == onboard["id"]
        assert item.rank == 1
        assert item.score == matches[0]["score"]
        assert item.reasons["reasons"]
        event = session.scalars(
            select(Event).where(Event.event_name == "match_run", Event.shop_id == shop["id"])
        ).one()
        assert event.actor == "api"
        assert event.properties["plan_tier"] == "starter"
        assert event.properties["plan_status"] == "free"
        assert "matches_run" not in event.properties
        assert event.matches_count_period == 1
        assert event.match_limit == 5
        assert event.products_count == 1
        shop_row = session.get(Shop, shop["id"])
        assert shop_row.matches_run == 1
        assert shop_row.first_match_at is not None
        assert shop_row.last_match_at is not None
        assert shop_row.products_enriched == 1
        assert shop_row.first_enrich_at is not None
        assert shop_row.briefs_generated == 0
        assert shop_row.first_brief_at is None
        product = session.get(Product, product_id)
        assert product.subcategory == "earbuds"
        assert product.tags == ["wireless"]
        assert product.attributes == {"color": "black"}
        assert product.claims[0]["source"] == "brand"
        assert product.currency == "USD"
        assert float(product.price) == 24
    sent = client.post(f"/briefs/{enrich.json()['brief_id']}/sent", json={"actor": "operator"})
    assert sent.status_code == 200
    with db.session_scope() as session:
        shop_row = session.get(Shop, shop["id"])
        assert shop_row.briefs_generated == 1
        assert shop_row.first_brief_at is not None
        assert shop_row.last_campaign_at is not None
        from models import Brief
        brief = session.get(Brief, enrich.json()["brief_id"])
        assert brief.sent_at is not None
        assert brief.shop_id == shop["id"]


def _fill_catalogue(shop_id: str, count: int) -> str:
    first_id = f"{shop_id}-0"
    with db.session_scope() as session:
        for index in range(count):
            session.add(
                Product(
                    id=f"{shop_id}-{index}",
                    shop_id=shop_id,
                    title=f"Item {index}",
                    images=[],
                    tags=[],
                    attributes={},
                    claims=[],
                    currency="USD",
                    created_at=utcnow(),
                )
            )
    return first_id


def test_usage_limit_hit_blocks_non_pilot_and_allows_pilot(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop = client.post("/shops", json={"name": "Capped", "plan_tier": "starter"}).json()
    existing_id = _fill_catalogue(shop["id"], 50)

    again = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "product_id": existing_id, "tags": ["sample"]},
    )
    assert again.status_code == 200
    with db.session_scope() as session:
        assert session.get(Shop, shop["id"]).products_enriched == 1
        assert len(session.scalars(select(Product).where(Product.shop_id == shop["id"])).all()) == 50
        assert session.scalars(select(Event).where(Event.event_name == "usage_limit_hit")).all() == []

    blocked = client.post("/enrich", json={**PRODUCT, "shop_id": shop["id"], "title": "One more"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"] == "Plan limit reached: products (50/50)"
    with db.session_scope() as session:
        row = session.get(Shop, shop["id"])
        assert row.products_enriched == 1
        assert len(session.scalars(select(Product).where(Product.shop_id == shop["id"])).all()) == 50
        hits = session.scalars(select(Event).where(Event.shop_id == shop["id"], Event.event_name == "usage_limit_hit")).all()
        assert len(hits) == 1
        assert hits[0].actor == "api"
        assert hits[0].properties["limit"] == "products"
        assert hits[0].properties["plan_tier"] == "starter"
        assert hits[0].products_count == 50
        assert hits[0].product_limit == 50
        assert hits[0].limit_hit is True

    pilot = client.post("/shops", json={"name": "Pilot", "is_pilot": True}).json()
    _fill_catalogue(pilot["id"], 50)
    allowed = client.post(
        "/enrich",
        json={
            **PRODUCT,
            "shop_id": pilot["id"],
            "title": "Extra item",
            "claims": [{"claim": "Ships in two days", "source": "brand"}],
        },
    )
    assert allowed.status_code == 200
    with db.session_scope() as session:
        row = session.get(Shop, pilot["id"])
        assert row.products_enriched == 1
        assert row.briefs_generated == 0
        assert row.first_brief_at is None
        assert row.first_enrich_at is not None
        assert len(session.scalars(select(Product).where(Product.shop_id == pilot["id"])).all()) == 51
        product = session.scalars(select(Product).where(Product.title == "Extra item")).one()
        assert product.claims[0]["source"] == "brand"
        assert product.claims[0]["claim"] == "Ships in two days"

    matcher = client.post("/shops", json={"name": "Matcher"}).json()
    with db.session_scope() as session:
        shop_row = session.get(Shop, matcher["id"])
        for _ in range(5):
            events.emit("match_run", shop_row, "api")
    denied = client.post(
        "/match",
        json={"product": PRODUCT, "creators": [INFLUENCER], "shop_id": matcher["id"]},
    )
    assert denied.status_code == 402
    assert "matches_per_month" in denied.json()["detail"]
    with db.session_scope() as session:
        hit = session.scalars(
            select(Event).where(Event.shop_id == matcher["id"], Event.event_name == "usage_limit_hit")
        ).one()
        assert hit.properties["limit"] == "matches_per_month"
        assert hit.properties["cap"] == 5
        assert hit.properties["observed"] == 5
        session.get(Shop, matcher["id"]).is_pilot = True
    allowed_match = client.post(
        "/match",
        json={"product": PRODUCT, "creators": [INFLUENCER], "shop_id": matcher["id"]},
    )
    assert allowed_match.status_code == 200
    assert allowed_match.json()["matches"]
    assert allowed_match.json()["shortlist_id"]
    with db.session_scope() as session:
        assert session.get(Shop, matcher["id"]).matches_run == 6
        hits = session.scalars(
            select(Event).where(Event.shop_id == matcher["id"], Event.event_name == "usage_limit_hit")
        ).all()
        assert len(hits) == 2


def test_api_requests_and_logs_omit_bodies_and_raw_keys(tmp_path, monkeypatch, capsys):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("API_KEYS", "super-secret-key")
    marker = "BODY_SHOULD_NOT_BE_STORED"
    response = client.post(
        "/enrich",
        json={**PRODUCT, "title": marker},
        headers={"X-API-Key": "super-secret-key"},
    )
    assert response.status_code == 200
    logged = capsys.readouterr().out
    assert "super-secret-key" not in logged
    assert marker not in logged
    parsed = [json.loads(line) for line in logged.splitlines() if line.startswith("{")]
    request_log = next(item for item in parsed if item.get("kind") == "request" and item.get("route") == "/enrich")
    assert request_log["method"] == "POST"
    assert request_log["status"] == 200
    assert request_log["key"] == main.api_key_label("super-secret-key")
    assert request_log["request_id"]
    assert isinstance(request_log["latency_ms"], int)
    grok_log = next(item for item in parsed if item.get("kind") == "grok")
    assert grok_log["fallback"] is True
    assert "model" in grok_log
    assert "prompt_tokens" in grok_log
    assert "completion_tokens" in grok_log
    assert "latency_ms" in grok_log

    with db.session_scope() as session:
        row = session.scalars(select(ApiRequest).where(ApiRequest.route == "/enrich")).one()
        columns = {column.name for column in ApiRequest.__table__.columns}
        assert columns == {
            "id",
            "ts",
            "route",
            "method",
            "status",
            "latency_ms",
            "api_key_id",
            "request_id",
            "grok_tokens_prompt",
            "grok_tokens_completion",
            "grok_fallback",
            "grok_latency_ms",
            "grok_model",
        }
        assert not any("body" in name for name in columns)
        assert row.method == "POST"
        assert row.status == 200
        assert row.latency_ms >= 0
        assert row.api_key_id == main.api_key_label("super-secret-key")
        assert row.grok_fallback is True
        assert row.request_id == request_log["request_id"]
        assert row.grok_model == "grok-4.3"
        assert row.grok_latency_ms == 0
        blob = json.dumps(
            {column.name: getattr(row, column.name) for column in ApiRequest.__table__.columns},
            default=str,
        )
        assert marker not in blob
        assert "super-secret-key" not in blob


def test_purge_api_requests_older_than_90_days(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    now = utcnow()
    with db.session_scope() as session:
        session.add(
            ApiRequest(
                id="old",
                ts=now - timedelta(days=91),
                route="/enrich",
                method="POST",
                status=200,
                latency_ms=3,
            )
        )
        session.add(
            ApiRequest(
                id="kept",
                ts=now - timedelta(days=89),
                route="/",
                method="GET",
                status=200,
                latency_ms=1,
            )
        )
    with db.session_scope() as session:
        removed = db.purge_api_requests(session)
        assert removed == 1
    with db.session_scope() as session:
        assert session.scalars(select(ApiRequest.id)).all() == ["kept"]


def test_shop_and_creator_routes_use_the_api_key(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("API_KEYS", "secret-key")
    headers = {"X-API-Key": "secret-key"}
    assert client.post("/shops", json={"name": "A"}).status_code == 401
    created = client.post("/shops", json={"name": "A"}, headers=headers)
    assert created.status_code == 200
    assert client.get(f"/shops/{created.json()['id']}").status_code == 401
    assert client.get(f"/shops/{created.json()['id']}", headers=headers).status_code == 200
    assert client.get("/creators/missing").status_code == 401
    assert client.get("/creators/missing", headers=headers).status_code == 404


def test_marketplace_fields_round_trip(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop = client.post(
        "/shops",
        json={"name": "Small seller", "is_underserved": True, "underserved_reason": "Few wholesale accounts"},
    )
    assert shop.status_code == 200
    assert shop.json()["is_underserved"] is True
    assert shop.json()["underserved_reason"] == "Few wholesale accounts"
    unknown = client.post(
        "/onboard-influencer",
        json={**INFLUENCER, "handle": "@unknown", "active_categories": ["not-a-category"]},
    )
    assert unknown.status_code == 422
    onboard = client.post(
        "/onboard-influencer",
        json={
            **INFLUENCER,
            "handle": "@markets",
            "active_categories": ["home"],
            "desired_categories": ["other"],
            "avg_views": 1200,
            "engagement_rate": 0.04,
            "est_conversion_rate": 0.02,
            "avg_order_value": 35,
            "typical_price_band": {"min": 20, "max": 50},
            "platforms": ["tiktok"],
            "average_engagement_rate": 0.04,
        },
    )
    assert onboard.status_code == 200
    body = onboard.json()["influencer"]
    assert body["active_categories"] == ["home"]
    assert body["desired_categories"] == ["other"]
    with db.session_scope() as session:
        creator = session.get(Creator, onboard.json()["id"])
        assert creator.active_categories == ["home"]
        assert creator.desired_categories == ["other"]
        assert creator.avg_views == 1200
        assert creator.engagement_rate == 0.04
        assert creator.metrics_source == "self_reported"
        assert creator.profile_completed_at is not None
        assert not hasattr(creator, "average_engagement_rate")
        assert creator.typical_price_band == {"min": 20, "max": 50}
        from sqlalchemy import func
        from models import Category

        assert int(session.scalar(select(func.count()).select_from(Category)) or 0) >= 9
    enriched = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop.json()["id"], "commission_rate": 12.5, "category": "home"},
    )
    assert enriched.status_code == 200
    with db.session_scope() as session:
        product = session.get(Product, enriched.json()["product_id"])
        assert float(product.commission_rate) == 12.5
        columns = {column.name for column in Product.__table__.columns}
        assert "pet_safe" not in columns
        assert "light" not in columns
        assert "care_level" not in columns


def test_market_category_and_lineage_schema(tmp_path, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from models import CreatorProductLink, Shortlist, ShortlistItem, Subscription

    use_database(tmp_path, monkeypatch)
    shop = client.post(
        "/shops",
        json={
            "name": "Market",
            "country": "us",
            "market": "us",
            "currency": "usd",
            "primary_category": "home",
        },
    )
    assert shop.status_code == 200
    body = shop.json()
    assert body["country"] == "US"
    assert body["market"] == "US"
    assert body["currency"] == "USD"
    assert body["primary_category"] == "home"
    assert client.post("/shops", json={"name": "Bad", "country": "USA"}).status_code == 422
    assert client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": body["id"], "category": "not-a-category"},
    ).status_code == 422
    alias = client.post(
        "/onboard-influencer",
        json={
            **INFLUENCER,
            "handle": "@alias",
            "average_engagement_rate": 0.2,
        },
    )
    assert alias.status_code == 200
    assert alias.json()["influencer"]["engagement_rate"] == 0.2
    assert "average_engagement_rate" not in alias.json()["influencer"]
    with db.session_scope() as session:
        assert session.get(Creator, alias.json()["id"]).engagement_rate == 0.2
        subscription = Subscription(
            id="sub-1",
            shop_id=body["id"],
            plan_tier="starter",
            status="free",
            interval="month",
            price=0,
            mrr=0,
            currency="USD",
            created_at=utcnow(),
        )
        session.add(subscription)
        session.flush()
        assert subscription.is_current is True
        product = Product(
            id="prod-link",
            shop_id=body["id"],
            title="Linked",
            images=[],
            tags=[],
            attributes={},
            claims=[],
            currency="USD",
            source="manual",
            created_at=utcnow(),
        )
        creator = session.get(Creator, alias.json()["id"])
        session.add(product)
        session.flush()
        session.add(
            CreatorProductLink(
                id="link-1",
                shop_id=body["id"],
                product_id=product.id,
                creator_id=creator.id,
                link_url="https://example.com/p",
                discount_code="SAVE10",
                platform="tiktok",
                status="active",
                created_at=utcnow(),
            )
        )
        shortlist = Shortlist(
            id="sl-1",
            shop_id=body["id"],
            product_id=product.id,
            match_version="placeholder-0",
            created_at=utcnow(),
        )
        session.add(shortlist)
        session.flush()
        session.add(
            ShortlistItem(
                id="item-1",
                shortlist_id=shortlist.id,
                creator_id=creator.id,
                score=1,
                reasons={},
                rank=1,
            )
        )
    with pytest.raises(IntegrityError):
        with db.session_scope() as session:
            session.add(
                ShortlistItem(
                    id="item-2",
                    shortlist_id="sl-1",
                    creator_id=alias.json()["id"],
                    score=1,
                    reasons={},
                    rank=2,
                )
            )


def test_duplicate_handle_is_rejected(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    assert client.post("/onboard-influencer", json=INFLUENCER).status_code == 200
    again = client.post("/onboard-influencer", json=INFLUENCER)
    assert again.status_code == 409


def test_subscription_change_type_is_restricted(tmp_path, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    use_database(tmp_path, monkeypatch)
    with pytest.raises(IntegrityError):
        with db.session_scope() as session:
            session.add(
                Shop(
                    id="s2",
                    name="Limits",
                    plan_tier="starter",
                    plan_status="free",
                    is_pilot=False,
                    products_enriched=0,
                    matches_run=0,
                    briefs_generated=0,
                    created_at=utcnow(),
                )
            )
            session.flush()
            session.add(
                SubscriptionChange(
                    id="bad",
                    shop_id="s2",
                    change_type="upgrade",
                    paid_to_free=False,
                    created_at=utcnow(),
                )
            )


def test_stale_0001_schema_is_recreated(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_initial')"))
        connection.execute(text("CREATE TABLE attributed_orders (id VARCHAR(36) PRIMARY KEY, amount NUMERIC)"))
        connection.execute(text("CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, event_name VARCHAR(64))"))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    main.init_storage()
    order_columns = {column["name"] for column in inspect(db.engine).get_columns("attributed_orders")}
    event_columns = {column["name"] for column in inspect(db.engine).get_columns("events")}
    assert "gmv_gross" in order_columns
    assert "amount" not in order_columns
    assert "products_count" in event_columns
    assert "pet_safe" not in {column["name"] for column in inspect(db.engine).get_columns("products")}


def test_emit_sets_creator_and_item_milestones_once(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    with db.session_scope() as session:
        creator = Creator(
            id="creator-1",
            handle="@once",
            interests=[],
            expertise_level={},
            preferred_promotion_styles=[],
            content_formats=[],
            past_categories=[],
            restrictions=[],
            excluded_categories=[],
            excluded_claims=[],
            platforms=[],
            profile={"influencer_id": "creator-1", "handle": "@once"},
            created_at=utcnow(),
        )
        session.add(creator)
        session.flush()
        events.emit("creator_onboarded", creator, "api")
        assert creator.profile_completed_at is None
        creator.active_categories = ["home"]
        creator.preferred_promotion_styles = ["educational"]
        creator.platforms = ["tiktok"]
        events.emit("creator_onboarded", creator, "operator")
        first = creator.profile_completed_at
        assert first is not None
        events.emit("creator_onboarded", creator, "api")
        assert creator.profile_completed_at == first
        events.emit("creator_posted", creator, "operator")
        assert creator.first_post_at is not None
        events.emit("match_accepted", creator, "api")
        assert creator.first_match_accepted_at is not None


def test_metrics_market_and_subcategory_validation(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop = client.post("/shops", json={"name": "Codes", "market": "us-east", "country": "gb"}).json()
    assert shop["market"] == "US-EAST"
    assert shop["country"] == "GB"
    assert client.post("/shops", json={"name": "Bad market", "market": "not a market"}).status_code == 422

    stamped = client.post(
        "/onboard-influencer",
        json={
            **INFLUENCER,
            "handle": "@stamped",
            "metrics_as_of": "2026-11-15T01:02:03Z",
        },
    )
    assert stamped.status_code == 200
    with db.session_scope() as session:
        creator = session.get(Creator, stamped.json()["id"])
        assert creator.metrics_as_of is not None
        assert creator.metrics_as_of.tzinfo is not None
        assert creator.metrics_as_of.year == 2026
    assert client.post(
        "/onboard-influencer",
        json={**INFLUENCER, "handle": "@badtime", "metrics_as_of": "yesterday"},
    ).status_code == 422

    ok = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "subcategory": "audio-gear"},
    )
    assert ok.status_code == 200
    assert client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "subcategory": "Not a slug!"},
    ).status_code == 422

    from models import Category

    with db.session_scope() as session:
        session.add(Category(slug="audio-gear", name="Audio gear", parent_slug="home"))
    listed = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "category": "home", "subcategory": "audio-gear"},
    )
    assert listed.status_code == 200
    refused = client.post(
        "/enrich",
        json={**PRODUCT, "shop_id": shop["id"], "category": "home", "subcategory": "other-gear"},
    )
    assert refused.status_code == 422


def test_stale_0001_with_rows_is_not_dropped(tmp_path, monkeypatch, caplog):
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'populated.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_initial')"))
        connection.execute(text("CREATE TABLE shops (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255))"))
        connection.execute(text("INSERT INTO shops (id, name) VALUES ('s', 'Keep me')"))
        connection.execute(text("CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, event_name VARCHAR(64))"))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    with caplog.at_level(logging.WARNING, logger="plantr.db"):
        with pytest.raises(RuntimeError, match="Refusing to drop"):
            main.init_storage()
    assert not any("Dropping an empty stale" in record.message for record in caplog.records)
    check = create_engine(url)
    with check.connect() as connection:
        assert connection.execute(text("SELECT name FROM shops")).scalar() == "Keep me"
    check.dispose()
    monkeypatch.delenv("DATABASE_URL", raising=False)
