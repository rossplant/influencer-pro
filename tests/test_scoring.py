"""Rules scorer: exclusions, order, missing data, and stable scores."""

import scoring


PRODUCT = {
    "title": "Sample",
    "category": "home",
    "price": 30,
    "tags": ["cotton"],
    "attributes": {"platforms": ["tiktok"], "target_geo": "us"},
    "claims": [{"claim": "Ships in two days", "source": "brand"}],
    "commission_rate": 10,
}

STRONG = {
    "influencer_id": "strong",
    "handle": "@strong",
    "active_categories": ["home"],
    "areas_of_interest": ["cotton"],
    "platforms": ["tiktok"],
    "avg_views": 10000,
    "engagement_rate": 0.1,
    "est_conversion_rate": 0.05,
    "typical_price_band": {"min": 20, "max": 40},
    "audience_demographics": {"location_focus": "us"},
}

PARTIAL = {
    "influencer_id": "partial",
    "handle": "@partial",
    "desired_categories": ["home"],
    "areas_of_interest": [],
}


def test_exclusions_remove_the_creator_and_say_why():
    blocked = {**STRONG, "influencer_id": "blocked", "handle": "@blocked"}
    category = {**STRONG, "influencer_id": "cat", "handle": "@cat", "excluded_categories": ["home"]}
    claim = {**STRONG, "influencer_id": "claim", "handle": "@claim", "excluded_claims": ["ships in two days"]}
    tag = {**STRONG, "influencer_id": "tag", "handle": "@tag", "restrictions": ["cotton"]}
    result = scoring.rank_creators(
        PRODUCT,
        [blocked, category, claim, tag, STRONG],
        blocked_creator_ids=["blocked"],
    )
    assert [row["creator"]["handle"] for row in result["matches"]] == ["@strong"]
    reasons = {row["handle"]: " ".join(row["reasons"]) for row in result["excluded"]}
    assert "Shop blocked this creator." in reasons["@blocked"]
    assert "Excluded category: home." in reasons["@cat"]
    assert "Excluded claim:" in reasons["@claim"]
    assert "Excluded tag: cotton." in reasons["@tag"]


def test_active_category_ranks_above_desired_category():
    result = scoring.rank_creators(PRODUCT, [PARTIAL, STRONG])
    assert [row["creator"]["handle"] for row in result["matches"]] == ["@strong", "@partial"]
    assert result["matches"][0]["components"]["category"]["fit"] == 1.0
    assert result["matches"][1]["components"]["category"]["fit"] == 0.5
    assert result["matches"][0]["score"] > result["matches"][1]["score"]


def test_missing_data_degrades_and_labels_estimates():
    bare_product = {"title": "Sample"}
    bare_creator = {"handle": "@bare", "influencer_id": "bare"}
    result = scoring.rank_creators(bare_product, [bare_creator])
    match = result["matches"][0]
    assert match["components"]["category"]["fit"] == 0
    assert match["components"]["tags"]["fit"] == 0
    assert match["components"]["price_band"]["fit"] == 0
    assert match["components"]["revenue"]["fit"] == 0
    estimates = match["estimates"]
    assert estimates["reach_estimated"] is True
    assert estimates["engagement_estimated"] is True
    assert estimates["conversion_estimated"] is True
    assert estimates["reach"] == scoring.DEFAULTS["reach_views"]
    assert estimates["est_revenue"] is None
    assert any("estimate" in reason.lower() for reason in match["reasons"])
    assert any("default" in reason.lower() for reason in match["reasons"])


def test_scores_are_reproducible_and_weights_change_with_optimise_for():
    first = scoring.rank_creators(PRODUCT, [STRONG, PARTIAL])
    second = scoring.rank_creators(PRODUCT, [PARTIAL, STRONG])
    assert first["matches"] == second["matches"]
    reach = scoring.rank_creators(PRODUCT, [STRONG], optimise_for="reach")
    revenue = scoring.rank_creators(PRODUCT, [STRONG], optimise_for="revenue")
    assert reach["matches"][0]["components"]["reach"]["weight"] == scoring.WEIGHTS["reach"]["reach"]
    assert revenue["matches"][0]["components"]["revenue"]["weight"] == scoring.WEIGHTS["revenue"]["revenue"]
    assert reach["matches"][0]["score"] != revenue["matches"][0]["score"]


def test_underserved_supplier_boost_is_configurable():
    plain = scoring.score_creator(PRODUCT, STRONG, shop={"is_underserved": False}, weights=scoring.WEIGHTS["balanced"], blocked=set())
    boosted = scoring.score_creator(PRODUCT, STRONG, shop={"is_underserved": True}, weights=scoring.WEIGHTS["balanced"], blocked=set())
    assert boosted["score"] == min(100, round(plain["score"] + scoring.DEFAULTS["underserved_boost"], 4))
    assert any("Underserved supplier boost" in reason for reason in boosted["reasons"])


def test_accept_and_decline_and_stored_exclusions(tmp_path, monkeypatch):
    import db
    import main
    from models import Event, Shortlist, ShortlistExclusion, ShortlistItem
    from sqlalchemy import select
    from test_api import PRODUCT, client
    from tests.test_persistence import use_database

    use_database(tmp_path, monkeypatch)
    shop = client.post("/shops", json={"name": "Decisions", "is_underserved": True}).json()
    onboard = client.post(
        "/onboard-influencer",
        json={
            "handle": "@chosen",
            "areas_of_interest": ["home"],
            "preferred_promotion_styles": ["educational"],
            "active_categories": ["home"],
            "platforms": ["tiktok"],
            "avg_views": 5000,
            "engagement_rate": 0.05,
            "est_conversion_rate": 0.02,
        },
    ).json()
    blocked = {
        "handle": "@blocked",
        "influencer_id": "not-stored",
        "excluded_categories": ["home"],
    }
    chosen = {
        "handle": "@chosen",
        "influencer_id": onboard["id"],
        "active_categories": ["home"],
        "platforms": ["tiktok"],
        "areas_of_interest": ["home"],
        "avg_views": 5000,
        "engagement_rate": 0.05,
        "est_conversion_rate": 0.02,
    }
    matched = client.post(
        "/match",
        json={
            "product": {**PRODUCT, "price": 25, "commission_rate": 15, "tags": ["home"]},
            "creators": [chosen, blocked],
            "shop_id": shop["id"],
            "optimise_for": "revenue",
        },
    )
    assert matched.status_code == 200
    body = matched.json()
    assert body["optimise_for"] == "revenue"
    assert body["excluded"][0]["handle"] == "@blocked"
    with db.session_scope() as session:
        shortlist = session.get(Shortlist, body["shortlist_id"])
        assert shortlist.optimise_for == "revenue"
        exclusion = session.scalars(select(ShortlistExclusion)).one()
        assert "Excluded category: home." in exclusion.reasons
        item = session.scalars(select(ShortlistItem)).one()
        item_id = item.id
    accepted = client.post(f"/shortlist-items/{item_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["accepted_at"]
    declined = client.post(f"/shortlist-items/{item_id}/decline", json={"reason": "Not this week"})
    assert declined.status_code == 200
    assert declined.json()["decline_reason"] == "Not this week"
    assert declined.json()["declined_at"]
    with db.session_scope() as session:
        names = set(session.scalars(select(Event.event_name)).all())
        assert "match_accepted" in names
        assert "match_declined" in names


def test_memory_match_stores_components_and_estimates(monkeypatch):
    import db
    import main
    from test_api import client

    monkeypatch.delenv("DATABASE_URL", raising=False)
    main.init_storage()
    shop = client.post("/shops", json={"name": "Memory"}).json()
    response = client.post(
        "/match",
        json={
            "product": {"title": "Sample", "category": "home", "price": 10, "commission_rate": 10},
            "creators": [
                {"handle": "@kept", "influencer_id": "kept", "active_categories": ["home"]},
                {"handle": "@out", "influencer_id": "out", "excluded_categories": ["home"]},
            ],
            "shop_id": shop["id"],
            "optimise_for": "sales",
        },
    )
    assert response.status_code == 200
    assert response.json()["matches"][0]["components"]
    assert response.json()["matches"][0]["estimates"]
    stored = db.memory_shortlist_items[-1]
    assert stored.reasons["components"] == response.json()["matches"][0]["components"]
    assert stored.reasons["estimates"] == response.json()["matches"][0]["estimates"]
    assert db.memory_shortlists[-1].optimise_for == "sales"
    assert db.memory_shortlist_exclusions[-1].handle == "@out"


def test_known_performance_is_not_marked_estimated():
    result = scoring.rank_creators(PRODUCT, [STRONG])["matches"][0]
    assert result["estimates"]["reach_estimated"] is False
    assert result["estimates"]["engagement_estimated"] is False
    assert result["estimates"]["conversion_estimated"] is False
    assert result["estimates"]["est_sales"] == 10000 * 0.1 * 0.05
    assert result["estimates"]["est_revenue"] == result["estimates"]["est_sales"] * 30
    assert result["estimates"]["est_seller_commission_payout"] == result["estimates"]["est_revenue"] * 0.1
