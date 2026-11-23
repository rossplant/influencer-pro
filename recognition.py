"""Creatr tiers, match allowances, and supplier badges.

Tiers are for Creatrs only. A plan never buys a tier. Suppliers do not earn
Contributr, Influencr, Trendsettr or Headlinr.

Suppliers can earn two plain badges, both free:
- "Pays on time" when commission and samples were on time over the last 3 months.
- "Top seller" when that reliability bar is met and confirmed sales through
  Creatrs clear a config threshold, through more than one Creatr.

Top seller does not change ranking. What a Creatr sees is ordered by match fit.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("plantr.recognition")

POINTS_FROM = date(2026, 11, 20)
PILOT_FLOOR_UNTIL = date(2027, 4, 20)
EARLY_ACCESS_HOURS = 48
EARLY_ACCESS_TIERS = frozenset({"trendsettr", "headlinr"})

# Confirmed, refund-adjusted sales over a rolling 3 months.
# v8's 10 / 30 / 75 are replaced by these figures.
TIERS = (
    {"key": "contributr", "label": "Contributr", "sales": 0, "benefit": "Standard matches and briefs."},
    {
        "key": "influencr",
        "label": "Influencr",
        "sales": 5,
        "benefit": "Priority samples when stock is limited.",
    },
    {
        "key": "trendsettr",
        "label": "Trendsettr",
        "sales": 15,
        "benefit": "48-hour early access to new briefs, and first pick on Debutr.",
    },
    {
        "key": "headlinr",
        "label": "Headlinr",
        "sales": 40,
        "benefit": "The same 48-hour early access, plus direct introductions.",
    },
)

# Shown once. Not a per-tier condition.
QUALITY_STANDARD_CREATR = (
    "Quality standard for every Creatr: agreed posts on time, no upheld claims or disclosure breaches, and ratings from suppliers."
)
QUALITY_STANDARD_SUPPLIER = (
    "Quality standard for every supplier: commission paid on time, and samples sent on time."
)
QUALITY_BAR = QUALITY_STANDARD_CREATR
EXCUSE_REASONS = ("supplier_or_courier_delay", "platform_error", "other")
QUALITY_WARNING = (
    "Last month missed the quality standard. One more month in a row removes earned status."
)

# Plans buy matches and collab slots. Tiers do not buy matches.
# elite matches is None: every qualifying match in the Creatr's categories.
PLAN_ALLOWANCES = {
    "member": {"matches": 4, "offers": 0, "active_collabs": 3, "all_qualifying": False},
    "curator": {"matches": 12, "offers": 4, "active_collabs": 6, "all_qualifying": False},
    "elite": {"matches": None, "offers": 12, "active_collabs": 12, "all_qualifying": True},
}
TIER_ACTIVE_BONUS = {"contributr": 0, "influencr": 0, "trendsettr": 2, "headlinr": 4}
SALES_UNLOCK_SALES = 5
SALES_UNLOCK_MATCHES = 3

RELIABILITY_BADGE = "Pays on time"
TOP_SELLER_BADGE = "Top seller"
TOP_SELLER_EXPLANATION = (
    "Top seller is a note Creatrs may see. It takes 20 or more confirmed sales "
    "through 5 or more Creatrs in the last 3 months, and Pays on time. "
    "It is not bought, and it does not change match order."
)

LISTING_STUDIO_NOTE = "Listing Studio is off. No image service is called, and nothing is watermarked."


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def window_start(on: date) -> date:
    """Rolling 3 months, measured back from `on`."""
    month = on.month - 3
    year = on.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(on.day, 28)
    return date(year, month, day)


def count_confirmed_sales(orders, *, on: Optional[date] = None) -> int:
    """Refund-adjusted confirmed sales on or after 20 Nov 2026, inside the rolling window.

    An order counts when net_gmv is above zero. A full refund (net_gmv <= 0) does not.
    """
    on = on or datetime.now(timezone.utc).date()
    start = window_start(on)
    total = 0
    for order in orders or []:
        if isinstance(order, dict):
            when = _as_date(order.get("attribution_date"))
            net = order.get("net_gmv", order.get("net"))
        else:
            when = _as_date(getattr(order, "attribution_date", None))
            net = getattr(order, "net_gmv", None)
        if when is None or when < POINTS_FROM or when < start or when > on:
            continue
        try:
            amount = float(net or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0:
            total += 1
    return total


def tier_key_for(sales: int, *, pilot_invite: bool = False, on: Optional[date] = None) -> str:
    on = on or datetime.now(timezone.utc).date()
    earned = "contributr"
    for row in TIERS:
        if int(sales) >= int(row["sales"]):
            earned = row["key"]
    if pilot_invite and on <= PILOT_FLOOR_UNTIL:
        if _rank(earned) < _rank("influencr"):
            return "influencr"
    return earned


def _rank(key: str) -> int:
    order = [row["key"] for row in TIERS]
    return order.index(key) if key in order else 0


def tier_label(key: str) -> str:
    for row in TIERS:
        if row["key"] == key:
            return row["label"]
    return ""


def tier_status(sales: int, *, pilot_invite: bool = False, on: Optional[date] = None) -> dict:
    """Public Creatr ladder. No plan name can change the result."""
    on = on or datetime.now(timezone.utc).date()
    key = tier_key_for(sales, pilot_invite=pilot_invite, on=on)
    current = next(row for row in TIERS if row["key"] == key)
    nxt = None
    for row in TIERS:
        if row["sales"] > current["sales"]:
            nxt = row
            break
    return {
        "key": key,
        "label": current["label"],
        "sales": int(sales),
        "benefit": current["benefit"],
        "next_label": "" if nxt is None else nxt["label"],
        "next_sales": None if nxt is None else nxt["sales"],
        "early_access_hours": EARLY_ACCESS_HOURS if key in EARLY_ACCESS_TIERS else 0,
        "pilot_floor": bool(pilot_invite and on <= PILOT_FLOOR_UNTIL),
    }


def reach_line(label: str, sales: int) -> str:
    return f"Reach {label}: {sales} confirmed sales in the last 3 months"


def progression(sales: int, *, pilot_invite: bool = False, on: Optional[date] = None) -> dict:
    """Ladder copy is only how you reach the next tier, plus one progress bar."""
    status = tier_status(sales, pilot_invite=pilot_invite, on=on)
    lines = ["Contributr: where you start"]
    lines.extend(reach_line(row["label"], row["sales"]) for row in TIERS if row["sales"] > 0)
    target = status["next_sales"]
    if target:
        percent = min(100, int(round(100 * status["sales"] / target)))
        next_line = reach_line(status["next_label"], target)
    else:
        percent = 100
        next_line = reach_line("Headlinr", 40)
    return {
        **status,
        "lines": lines,
        "next_line": next_line,
        "percent": percent,
        "quality_standard": QUALITY_STANDARD_CREATR,
    }


def failing_months_limit() -> int:
    return max(1, _int_env("QUALITY_FAIL_MONTHS", 2))


def consecutive_failures(months: list) -> int:
    """Unexcused failed months at the end of the record. A pass resets. An excuse is skipped."""
    ordered = sorted(months or [], key=lambda row: str(row.get("month") or ""))
    streak = 0
    for row in reversed(ordered):
        if row.get("excused"):
            continue
        if row.get("passed"):
            break
        streak += 1
    return streak


def floor_tier(pilot_invite: bool, on: Optional[date] = None) -> str:
    on = on or datetime.now(timezone.utc).date()
    if pilot_invite and on <= PILOT_FLOOR_UNTIL:
        return "influencr"
    return "contributr"


def apply_quality(sales: int, months: list, *, pilot_invite: bool = False, on: Optional[date] = None, role: str = "creator") -> dict:
    """N consecutive failed months drop earned tier and badges. The first failure is a warning."""
    on = on or datetime.now(timezone.utc).date()
    streak = consecutive_failures(months)
    limit = failing_months_limit()
    demoted = streak >= limit
    warning = streak == 1
    if role == "supplier":
        return {
            "role": "supplier",
            "demoted": demoted,
            "warning": warning,
            "consecutive_failures": streak,
            "badges_allowed": not demoted,
            "early_access_hours": 0,
            "notice": QUALITY_WARNING if warning else "",
            "quality_standard": QUALITY_STANDARD_SUPPLIER,
        }
    if demoted:
        key = floor_tier(pilot_invite, on)
        early = 0
    else:
        key = tier_key_for(sales, pilot_invite=pilot_invite, on=on)
        early = EARLY_ACCESS_HOURS if key in EARLY_ACCESS_TIERS else 0
    return {
        "role": "creator",
        "demoted": demoted,
        "warning": warning,
        "consecutive_failures": streak,
        "key": key,
        "label": tier_label(key),
        "early_access_hours": early,
        "badges_allowed": not demoted,
        "notice": QUALITY_WARNING if warning else "",
        "quality_standard": QUALITY_STANDARD_CREATR,
    }


def plan_buys_tier(plan_tier: str) -> bool:
    """Always false. Membr, Curatr and Curatr Pro do not buy a tier."""
    return False


def early_access_hours(earned_key: str, plan_tier: str = "") -> int:
    """48 hours for Trendsettr and Headlinr only. The plan is ignored."""
    if plan_buys_tier(plan_tier):
        return 0
    return EARLY_ACCESS_HOURS if earned_key in EARLY_ACCESS_TIERS else 0


def _plan(plan_tier: str) -> dict:
    key = (plan_tier or "member").strip().lower()
    return dict(PLAN_ALLOWANCES.get(key) or PLAN_ALLOWANCES["member"])


def monthly_allowance(plan_tier: str, earned_key: str, sales_this_month: int = 0) -> dict:
    """Monthly matches and offers, plus the active-collab cap. No rollover.

    Numbers come from PLAN_ALLOWANCES and the env overrides below.
    A tier adds only to the active-collab cap (Trendsettr +2, Headlinr +4).
    Five attributed sales in the calendar month add 3 matches that month.
    """
    base = _plan(plan_tier)
    overrides = {
        "member": ("MATCH_MEMBR", "OFFERS_MEMBR", "COLLABS_MEMBR"),
        "curator": ("MATCH_CURATR", "OFFERS_CURATR", "COLLABS_CURATR"),
        "elite": ("MATCH_CURATR_PRO", "OFFERS_CURATR_PRO", "COLLABS_CURATR_PRO"),
    }
    names = overrides.get((plan_tier or "member").strip().lower())
    matches = base["matches"]
    offers = base["offers"]
    collabs = base["active_collabs"]
    if names:
        if names[0] != "MATCH_CURATR_PRO" or os.getenv(names[0], "").strip():
            if os.getenv(names[0], "").strip():
                raw = os.getenv(names[0], "").strip().lower()
                matches = None if raw in {"all", "qualifying", "none"} else _int_env(names[0], matches or 0)
        offers = _int_env(names[1], offers)
        collabs = _int_env(names[2], collabs)
    bonus = _int_env(
        f"TIER_COLLAB_BONUS_{earned_key.upper()}",
        TIER_ACTIVE_BONUS.get(earned_key, 0),
    )
    unlock_at = _int_env("SALES_UNLOCK_SALES", SALES_UNLOCK_SALES)
    unlock_n = _int_env("SALES_UNLOCK_MATCHES", SALES_UNLOCK_MATCHES)
    extra = unlock_n if int(sales_this_month) >= unlock_at else 0
    if matches is not None:
        matches = int(matches) + extra
    return {
        "matches": matches,
        "all_qualifying": matches is None or base["all_qualifying"] and not os.getenv((names or ("",))[0], "").strip(),
        "offers": offers,
        "active_collabs": int(collabs) + int(bonus),
        "extra_matches": extra,
        "rollover": False,
    }


def plan_card_lines(plan_tier: str) -> list[str]:
    """Two short lines for a plan card. No 'unlimited' and no top-placement claim."""
    grant = monthly_allowance(plan_tier, "contributr", 0)
    if grant["matches"] is None:
        matches = "Every qualifying match in your categories"
    else:
        matches = f"{grant['matches']} new matches a month"
    offers = grant["offers"]
    offer_line = "No curated offers" if not offers else f"{offers} curated offers a month"
    return [
        matches,
        offer_line,
        f"{grant['active_collabs']} active collabs at a time",
    ]


def reliability_met(pay_on_time: int, pay_total: int, samples_on_time: int, sample_total: int) -> bool:
    if pay_total < 1 or sample_total < 1:
        return False
    pay_need = _float_env("RELIABILITY_PAY_ON_TIME", 1.0)
    sample_need = _float_env("RELIABILITY_SAMPLES_ON_TIME", 1.0)
    return (pay_on_time / pay_total) >= pay_need and (samples_on_time / sample_total) >= sample_need


def reliability_badge(pay_on_time: int, pay_total: int, samples_on_time: int, sample_total: int) -> str:
    """One factual badge, or an empty string. Never a tier name."""
    if reliability_met(pay_on_time, pay_total, samples_on_time, sample_total):
        return RELIABILITY_BADGE
    return ""


def top_seller_status(
    *,
    confirmed_sales: int,
    distinct_creators: int,
    pay_on_time: int,
    pay_total: int,
    samples_on_time: int,
    sample_total: int,
) -> dict:
    """Earned supplier status. Not a tier, and not a ranking input."""
    min_sales = _int_env("TOP_SELLER_MIN_SALES", 20)
    min_creators = _int_env("TOP_SELLER_MIN_CREATRS", 5)
    reliable = reliability_met(pay_on_time, pay_total, samples_on_time, sample_total)
    earned = int(confirmed_sales) >= min_sales and int(distinct_creators) >= min_creators and reliable
    return {
        "earned": earned,
        "label": TOP_SELLER_BADGE if earned else "",
        "explanation": TOP_SELLER_EXPLANATION,
        "paid": False,
        "changes_rank": False,
    }


def rank_for_creatr(fit_score: float, *, top_seller: bool = False) -> float:
    """Match fit only. Top seller is ignored on purpose."""
    return float(fit_score)


def selling_fast(sales_7d: int, peer_sales_7d: list[int], *, manual: bool = False) -> bool:
    """Top ~10% of a category over 7 days, with a minimum of 5, or a manual flag."""
    if manual:
        return True
    minimum = _int_env("SELLING_FAST_MIN", 5)
    if int(sales_7d) < minimum or not peer_sales_7d:
        return False
    ordered = sorted(int(value) for value in peer_sales_7d)
    # Highest 10%, at least the top row.
    cutoff_index = max(0, len(ordered) - max(1, (len(ordered) + 9) // 10))
    return int(sales_7d) >= ordered[cutoff_index]


def momentum_line(confirmed_7d: int) -> str:
    count = int(confirmed_7d)
    noun = "sale" if count == 1 else "sales"
    return f"{count} confirmed {noun} in the last 7 days."


def promo_end(start: date, days: int) -> date:
    length = int(days)
    if length < 7 or length > 30:
        raise ValueError("Promo commission runs for 7 to 30 days")
    return start + timedelta(days=length)


def promo_line(rate: float, ends: date) -> str:
    """End date only. Never a countdown."""
    return f"Promo commission {float(rate):g}% until {ends.day} {ends.strftime('%b %Y')}."


def listing_studio_enabled() -> bool:
    return os.getenv("LISTING_STUDIO_ENABLED", "").strip().lower() in {"1", "true", "on", "yes"}


INCLUDED_IMAGES = 4
EXTRA_IMAGE_CENTS = 150
POST_PILOT_PRODUCT_CENTS = 499
LISTING_STATUSES = ("Not started", "Awaiting your approval", "Approved")


def listing_studio_price(image_count: int, *, on: Optional[date] = None) -> dict:
    """Per product. Pilot includes 4 images and tips at no charge. Extras are A$1.50. Not a tier perk."""
    import billing_config
    from zoneinfo import ZoneInfo

    moment = on or datetime.now(timezone.utc).date()
    end = billing_config.pilot_end().astimezone(ZoneInfo(billing_config.PILOT_TIMEZONE)).date()
    in_pilot = moment < end
    extra = max(0, int(image_count) - INCLUDED_IMAGES)
    base = 0 if in_pilot else _int_env("LISTING_PRODUCT_CENTS", POST_PILOT_PRODUCT_CENTS)
    extra_each = _int_env("LISTING_EXTRA_IMAGE_CENTS", EXTRA_IMAGE_CENTS)
    return {
        "in_pilot": in_pilot,
        "included_images": INCLUDED_IMAGES,
        "extra_images": extra,
        "base_cents": base,
        "extra_each_cents": extra_each,
        "total_cents": base + extra * extra_each,
        "tips_included": True,
        "extra_label": "A$1.50",
    }


def listing_status(attributes: Optional[dict]) -> str:
    raw = str((attributes or {}).get("listing_studio_status") or "")
    if raw in LISTING_STATUSES:
        return raw
    return "Not started"


def tips_problem(text: str) -> str:
    """Tips stay inside the brief. No invented health, safety, eco, or best claims."""
    lowered = (text or "").lower()
    for phrase in ("clinically", "eco-friendly", "the best", "safest", "guaranteed"):
        if phrase in lowered:
            return "Keep tips to the approved brief. Remove health, safety, eco, or best claims."
    return ""


def clean_listing_image(image_ref: str) -> dict:
    """Stub. The paid image API is not called while the flag is off."""
    if not listing_studio_enabled():
        logger.info("Listing Studio is off. No image API was called.")
        return {"status": "disabled", "called_api": False, "watermark": False, "note": LISTING_STUDIO_NOTE}
    logger.info("Listing Studio flag is on, but no image provider is wired. Nothing was sent.")
    return {"status": "stub", "called_api": False, "watermark": False, "image_ref": image_ref}


def supplier_visible_to_creators(shop) -> bool:
    """Non-Shopify suppliers stay hidden from Creatrs until tracking exists."""
    if shop is None:
        return False
    if isinstance(shop, dict):
        domain = shop.get("shopify_domain")
    else:
        domain = getattr(shop, "shopify_domain", None)
    return bool(str(domain or "").strip())


def pilot_supplier_caps() -> dict:
    return {
        "shopify": _int_env("PILOT_SHOPIFY_CAP", 20),
        "non_shopify": _int_env("PILOT_NON_SHOPIFY_CAP", 10),
    }


def supplier_slot_open(*, shopify: bool, shopify_count: int, non_shopify_count: int) -> bool:
    caps = pilot_supplier_caps()
    if shopify:
        return int(shopify_count) < caps["shopify"]
    return int(non_shopify_count) < caps["non_shopify"]
