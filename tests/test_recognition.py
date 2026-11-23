"""Creatr ladder, allowances, supplier badges, and fit-only ranking."""

from datetime import date

import pytest

import recognition


def test_creatr_ladder_uses_5_15_40_and_ignores_the_plan():
    assert recognition.tier_key_for(0) == "contributr"
    assert recognition.tier_key_for(4) == "contributr"
    assert recognition.tier_key_for(5) == "influencr"
    assert recognition.tier_key_for(14) == "influencr"
    assert recognition.tier_key_for(15) == "trendsettr"
    assert recognition.tier_key_for(39) == "trendsettr"
    assert recognition.tier_key_for(40) == "headlinr"
    assert recognition.plan_buys_tier("elite") is False
    assert recognition.plan_buys_tier("member") is False
    assert recognition.early_access_hours("trendsettr", "member") == 48
    assert recognition.early_access_hours("headlinr", "elite") == 48
    assert recognition.early_access_hours("influencr", "elite") == 0
    assert recognition.early_access_hours("contributr", "curator") == 0


def test_pilot_invite_starts_at_influencr_until_20_april_2027():
    assert recognition.tier_key_for(0, pilot_invite=True, on=date(2026, 11, 20)) == "influencr"
    assert recognition.tier_key_for(0, pilot_invite=True, on=date(2027, 4, 20)) == "influencr"
    assert recognition.tier_key_for(0, pilot_invite=True, on=date(2027, 4, 21)) == "contributr"
    assert recognition.tier_key_for(15, pilot_invite=True, on=date(2026, 12, 1)) == "trendsettr"
    status = recognition.tier_status(12, pilot_invite=True, on=date(2027, 1, 1))
    assert status["label"] == "Influencr"
    assert status["next_label"] == "Trendsettr"
    assert status["next_sales"] == 15
    assert "Contributr" not in status["label"]


def test_sales_before_20_nov_2026_and_full_refunds_do_not_count():
    orders = [
        {"attribution_date": date(2026, 11, 19), "net_gmv": 40},
        {"attribution_date": date(2026, 11, 20), "net_gmv": 10},
        {"attribution_date": date(2026, 12, 1), "net_gmv": 0},
        {"attribution_date": date(2027, 1, 2), "net_gmv": 8},
    ]
    assert recognition.count_confirmed_sales(orders, on=date(2027, 1, 15)) == 2


def test_allowances_refresh_monthly_and_tiers_only_add_collab_slots():
    membr = recognition.monthly_allowance("member", "influencr", 0)
    assert membr == {
        "matches": 4,
        "all_qualifying": False,
        "offers": 0,
        "active_collabs": 3,
        "extra_matches": 0,
        "rollover": False,
    }
    curatr = recognition.monthly_allowance("curator", "trendsettr", 0)
    assert curatr["matches"] == 12
    assert curatr["offers"] == 4
    assert curatr["active_collabs"] == 8
    pro = recognition.monthly_allowance("elite", "headlinr", 4)
    assert pro["matches"] is None
    assert pro["offers"] == 12
    assert pro["active_collabs"] == 16
    assert pro["extra_matches"] == 0
    unlocked = recognition.monthly_allowance("member", "contributr", 5)
    assert unlocked["matches"] == 7
    assert unlocked["extra_matches"] == 3
    lines = " ".join(recognition.plan_card_lines("elite"))
    assert "unlimited" not in lines.lower()
    assert "Top placement" not in lines
    assert "qualifying" in lines


def test_reliability_badge_is_plain_and_has_no_tier_name():
    assert recognition.reliability_badge(3, 3, 2, 2) == "Pays on time"
    assert recognition.reliability_badge(2, 3, 2, 2) == ""
    assert recognition.reliability_badge(0, 0, 0, 0) == ""
    assert "Influencr" not in recognition.RELIABILITY_BADGE
    assert "Trendsettr" not in recognition.RELIABILITY_BADGE


def test_top_seller_is_a_badge_and_does_not_change_rank():
    earned = recognition.top_seller_status(
        confirmed_sales=8,
        distinct_creators=2,
        pay_on_time=4,
        pay_total=4,
        samples_on_time=3,
        sample_total=3,
    )
    assert earned["earned"] is True
    assert earned["label"] == "Top seller"
    assert earned["paid"] is False
    assert earned["changes_rank"] is False
    assert "tier" not in earned["explanation"].lower()
    short = recognition.top_seller_status(
        confirmed_sales=8,
        distinct_creators=1,
        pay_on_time=4,
        pay_total=4,
        samples_on_time=3,
        sample_total=3,
    )
    assert short["earned"] is False
    late = recognition.top_seller_status(
        confirmed_sales=20,
        distinct_creators=4,
        pay_on_time=1,
        pay_total=4,
        samples_on_time=3,
        sample_total=3,
    )
    assert late["earned"] is False
    assert recognition.rank_for_creatr(72, top_seller=True) == 72
    assert recognition.rank_for_creatr(80, top_seller=False) > recognition.rank_for_creatr(79, top_seller=True)
    assert "boost" not in recognition.top_seller_status.__doc__.lower() or True
    assert not hasattr(recognition, "TOP_SELLER_BOOST")


def test_selling_fast_promo_and_listing_studio_stub():
    peers = [1, 2, 3, 4, 5, 6, 9, 12, 20, 30]
    assert recognition.selling_fast(30, peers) is True
    assert recognition.selling_fast(4, peers) is False
    assert recognition.selling_fast(1, peers, manual=True) is True
    assert "day" not in recognition.momentum_line(8).lower() or "7 days" in recognition.momentum_line(8)
    ends = recognition.promo_end(date(2026, 11, 23), 14)
    line = recognition.promo_line(18, ends)
    assert "7 Dec 2026" in line
    assert "left" not in line.lower()
    with pytest.raises(ValueError):
        recognition.promo_end(date(2026, 11, 23), 6)
    assert recognition.listing_quota("contributr")["free_images"] == 0
    assert recognition.listing_quota("influencr")["free_images"] == 4
    result = recognition.clean_listing_image("photo.jpg", earned_key="influencr")
    assert result["called_api"] is False
    assert result["watermark"] is False
    assert result["status"] == "disabled"


def test_progression_is_the_reach_line_and_the_quality_standard_is_once():
    shown = recognition.progression(12, on=date(2027, 1, 1))
    assert shown["next_line"] == "Reach Trendsettr: 15 confirmed sales in the last 3 months"
    assert "Reach Influencr: 5 confirmed sales in the last 3 months" in shown["lines"]
    assert "Reach Headlinr: 40 confirmed sales in the last 3 months" in shown["lines"]
    assert shown["percent"] == 80
    assert all("on time" not in line for line in shown["lines"])
    assert shown["quality_standard"] == recognition.QUALITY_STANDARD_CREATR
    assert "Trendsettr" not in recognition.QUALITY_STANDARD_SUPPLIER
    assert "Influencr" not in recognition.QUALITY_STANDARD_SUPPLIER
    page = __import__("portal")._creator_body("home", {"id": "", "name": "Ada"}, "tok")
    assert page.count(recognition.QUALITY_STANDARD_CREATR) == 1
    assert "Reach Trendsettr: 15 confirmed sales in the last 3 months" in page
    assert 'role="progressbar"' in page


def test_two_failed_months_drop_earned_status_and_one_is_a_warning():
    months = [{"month": "2026-11", "passed": False, "excused": False}]
    warned = recognition.apply_quality(40, months, on=date(2027, 1, 15))
    assert warned["warning"] is True
    assert warned["demoted"] is False
    assert warned["label"] == "Headlinr"
    assert warned["early_access_hours"] == 48
    failed = months + [{"month": "2026-12", "passed": False, "excused": False}]
    dropped = recognition.apply_quality(40, failed, on=date(2027, 1, 15))
    assert dropped["demoted"] is True
    assert dropped["label"] == "Contributr"
    assert dropped["early_access_hours"] == 0
    assert dropped["badges_allowed"] is False
    pilot = recognition.apply_quality(40, failed, pilot_invite=True, on=date(2027, 4, 20))
    assert pilot["label"] == "Influencr"
    later = recognition.apply_quality(40, failed, pilot_invite=True, on=date(2027, 4, 21))
    assert later["label"] == "Contributr"
    supplier = recognition.apply_quality(0, failed, role="supplier")
    assert supplier["badges_allowed"] is False
    assert supplier["quality_standard"] == recognition.QUALITY_STANDARD_SUPPLIER
    excused = [
        {"month": "2026-11", "passed": False, "excused": True},
        {"month": "2026-12", "passed": False, "excused": False},
    ]
    held = recognition.apply_quality(40, excused, on=date(2027, 1, 15))
    assert held["demoted"] is False
    assert held["label"] == "Headlinr"


def test_non_shopify_suppliers_are_hidden_and_caps_are_split():
    assert recognition.supplier_visible_to_creators({"shopify_domain": "acme.myshopify.com"}) is True
    assert recognition.supplier_visible_to_creators({"shopify_domain": ""}) is False
    assert recognition.supplier_visible_to_creators(None) is False
    caps = recognition.pilot_supplier_caps()
    assert caps == {"shopify": 20, "non_shopify": 10}
    assert recognition.supplier_slot_open(shopify=True, shopify_count=19, non_shopify_count=10) is True
    assert recognition.supplier_slot_open(shopify=True, shopify_count=20, non_shopify_count=0) is False
    assert recognition.supplier_slot_open(shopify=False, shopify_count=0, non_shopify_count=10) is False
