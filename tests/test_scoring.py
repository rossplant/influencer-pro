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


def test_known_performance_is_not_marked_estimated():
    result = scoring.rank_creators(PRODUCT, [STRONG])["matches"][0]
    assert result["estimates"]["reach_estimated"] is False
    assert result["estimates"]["engagement_estimated"] is False
    assert result["estimates"]["conversion_estimated"] is False
    assert result["estimates"]["est_sales"] == 10000 * 0.1 * 0.05
    assert result["estimates"]["est_revenue"] == result["estimates"]["est_sales"] * 30
    assert result["estimates"]["est_seller_commission_payout"] == result["estimates"]["est_revenue"] * 0.1
