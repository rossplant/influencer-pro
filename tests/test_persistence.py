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
    assert events.mrr("growth", status="free") == 0
    assert events.mrr("growth") == 49
    assert events.mrr("scale") == 149
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
                external_order_id="ord_1",
                amount=45,
                currency="USD",
                occurred_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with db.session_scope() as session:
        change = session.get(SubscriptionChange, "c")
        assert change.change_type == "contraction"
        assert change.paid_to_free is True
        assert change.upgrade_trigger_snapshot["to"] == "starter"
        assert session.get(AttributedOrder, "o").external_order_id == "ord_1"


def test_emit_updates_counters_and_milestones(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    with db.session_scope() as session:
        shop = Shop(
            id="shop-1",
            name="Nursery",
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
        events.emit("brief_generated", shop, "api")
        match = session.scalars(select(Event).where(Event.event_name == "match_run")).one()
        assert match.actor == "operator"
        assert match.entity_type == "shop"
        assert match.entity_id == "shop-1"
        assert match.shop_id == "shop-1"
        assert match.properties["plan_tier"] == "starter"
        assert match.properties["plan_status"] == "active"
        assert match.properties["products_enriched"] == 2
        assert match.properties["matches_run"] == 1
        assert match.properties["briefs_generated"] == 0
        assert shop.matches_run == 1
        assert shop.briefs_generated == 1
        assert shop.first_match_at is not None
        assert shop.first_brief_at is not None
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
        "bio": "Grows things",
        "restrictions": ["no_pet_toxic"],
        "platforms": ["tiktok"],
        "follower_band": "10k_50k",
    }
    response = client.post("/onboard-influencer", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "influencer"}
    assert body["influencer"]["influencer_id"] == body["id"]
    assert body["influencer"]["handle"] == "@plantlover"
    assert body["influencer"]["platforms"] == ["tiktok"]
    assert body["influencer"]["restrictions"] == ["no_pet_toxic"]
    assert body["id"] not in main.INFLUENCERS

    with db.session_scope() as session:
        creator = session.get(Creator, body["id"])
        assert creator.handle == "@plantlover"
        assert creator.interests == INFLUENCER["areas_of_interest"]
        assert creator.restrictions == ["no_pet_toxic"]
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
    assert fetched.json()["influencer"]["handle"] == "@plantlover"
    assert client.get("/creators/missing").status_code == 404


def test_shop_pilot_defaults_to_starter_with_zero_mrr(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    pilot = client.post("/shops", json={"name": "Pilot Plants", "is_pilot": True})
    assert pilot.status_code == 200
    body = pilot.json()
    assert body["plan_tier"] == "starter"
    assert body["plan_status"] == "active"
    assert body["is_pilot"] is True
    assert body["mrr"] == 0
    assert client.get(f"/shops/{body['id']}").json()["name"] == "Pilot Plants"

    growth = client.post("/shops", json={"name": "Growth", "plan_tier": "growth"}).json()
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
        json={**PRODUCT, "shop_id": shop["id"], "care_level": "easy", "size": "medium", "price": 24},
    )
    assert enrich.status_code == 200
    product_id = enrich.json()["product_id"]
    influencer = {**INFLUENCER, "influencer_id": onboard["id"]}
    response = client.post(
        "/match",
        json={
            "influencer": influencer,
            "products": [PRODUCT],
            "shop_id": shop["id"],
            "product_id": product_id,
        },
    )
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert matches[0]["product"]["title"] == PRODUCT["title"]
    shortlist_id = response.json()["shortlist_id"]

    with db.session_scope() as session:
        shortlist = session.get(Shortlist, shortlist_id)
        assert shortlist.match_version == "placeholder-0"
        assert shortlist.shop_id == shop["id"]
        assert shortlist.product_id == product_id
        item = session.scalars(select(ShortlistItem).where(ShortlistItem.shortlist_id == shortlist_id)).one()
        assert item.creator_id == onboard["id"]
        assert item.rank == 1
        assert item.score == matches[0]["match_score"]
        assert item.reasons["basis"] == "placeholder-0"
        event = session.scalars(
            select(Event).where(Event.event_name == "match_run", Event.shop_id == shop["id"])
        ).one()
        assert event.actor == "api"
        assert event.properties["plan_tier"] == "starter"
        assert event.properties["matches_run"] == 1
        assert "plan_status" in event.properties
        shop_row = session.get(Shop, shop["id"])
        assert shop_row.matches_run == 1
        assert shop_row.first_match_at is not None
        assert shop_row.products_enriched == 1
        assert shop_row.first_enrich_at is not None
        product = session.get(Product, product_id)
        assert product.care_level == "easy"
        assert float(product.price) == 24


def test_usage_limit_hit_blocks_non_pilot_and_allows_pilot(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    shop = client.post("/shops", json={"name": "Capped", "plan_tier": "starter"}).json()
    with db.session_scope() as session:
        session.get(Shop, shop["id"]).products_enriched = 50

    blocked = client.post("/enrich", json={**PRODUCT, "shop_id": shop["id"]})
    assert blocked.status_code == 402
    assert blocked.json()["detail"] == "Plan limit reached: products (50/50)"
    with db.session_scope() as session:
        row = session.get(Shop, shop["id"])
        assert row.products_enriched == 50
        hits = session.scalars(select(Event).where(Event.event_name == "usage_limit_hit")).all()
        assert len(hits) == 1
        assert hits[0].actor == "api"
        assert hits[0].properties["limit"] == "products"
        assert hits[0].properties["plan_tier"] == "starter"
        assert hits[0].properties["products_enriched"] == 50
        assert session.scalars(select(Product)).all() == []

    pilot = client.post("/shops", json={"name": "Pilot", "is_pilot": True}).json()
    with db.session_scope() as session:
        session.get(Shop, pilot["id"]).products_enriched = 50
    allowed = client.post(
        "/enrich",
        json={
            **PRODUCT,
            "shop_id": pilot["id"],
            "pet_safe": True,
            "light": "low",
            "claims": [{"claim": "Non-toxic", "source": "merchant"}],
        },
    )
    assert allowed.status_code == 200
    with db.session_scope() as session:
        row = session.get(Shop, pilot["id"])
        assert row.products_enriched == 51
        assert row.briefs_generated == 1
        assert row.first_enrich_at is not None
        assert row.is_pilot is True
        product = session.scalars(select(Product).where(Product.shop_id == pilot["id"])).one()
        assert product.pet_safe is True
        assert product.light == "low"
        assert product.claims[0]["source"] == "merchant"
        assert product.claims[0]["claim"] == "Non-toxic"

    matcher = client.post("/shops", json={"name": "Matcher"}).json()
    with db.session_scope() as session:
        shop_row = session.get(Shop, matcher["id"])
        for _ in range(5):
            events.emit("match_run", shop_row, "api")
    denied = client.post(
        "/match",
        json={"influencer": INFLUENCER, "products": [PRODUCT], "shop_id": matcher["id"]},
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
        json={"influencer": INFLUENCER, "products": [PRODUCT], "shop_id": matcher["id"]},
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
            "grok_tokens_prompt",
            "grok_tokens_completion",
            "grok_fallback",
        }
        assert not any("body" in name for name in columns)
        assert row.method == "POST"
        assert row.status == 200
        assert row.latency_ms >= 0
        assert row.api_key_id == main.api_key_label("super-secret-key")
        assert row.grok_fallback is True
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
