"""Plan limits, Stripe price lookup keys, coupons, and pilot settings.

Prices are AUD and shown ex GST. Stripe Tax is not enabled. Annual prices are
ten months, which is two months free. Nothing here is a live Stripe key.
"""

from __future__ import annotations

import os

CURRENCY = "aud"
TAX_DISPLAY = os.getenv("STRIPE_TAX_DISPLAY", "exclusive").strip() or "exclusive"
STRIPE_TAX_ENABLED = False

SELLER_TIERS = {
    "starter": {
        "label": "Starter",
        "audience": "seller",
        "amount_month_cents": 0,
        "amount_year_cents": 0,
        "lookup_month": None,
        "lookup_year": None,
        "limits": {"campaigns": 1, "products": 5, "matches_per_month": 3},
        "reaches": ("member",),
        "features": ("open_catalogue",),
    },
    "growth": {
        "label": "Growth",
        "audience": "seller",
        "amount_month_cents": 7900,
        "amount_year_cents": 79000,
        "lookup_month": "seller_growth_month",
        "lookup_year": "seller_growth_year",
        "limits": {"campaigns": 5, "products": 50, "matches_per_month": 25},
        "reaches": ("member", "curator"),
        "features": ("csv_import", "per_creator_report", "exclusive_first_look"),
    },
    "scale": {
        "label": "Scale",
        "audience": "seller",
        "amount_month_cents": 22900,
        "amount_year_cents": 229000,
        "lookup_month": "seller_scale_month",
        "lookup_year": "seller_scale_year",
        "limits": {"campaigns": None, "products": None, "matches_per_month": 100},
        "products_label": "250+",
        "reaches": ("member", "curator", "elite"),
        "features": ("csv_import", "per_creator_report", "priority_matching", "export", "exclusive_first_look"),
        "coming_soon": ("brief_help", "featured_supplier"),
    },
}

CREATOR_TIERS = {
    "member": {
        "label": "Membr",
        "audience": "creator",
        "amount_month_cents": 0,
        "amount_year_cents": 0,
        "lookup_month": None,
        "lookup_year": None,
        "limits": {"matches_per_month": 2, "curated_offers_per_month": 0},
        "features": ("tracking_links", "basic_sales"),
    },
    "curator": {
        "label": "Curatr",
        "audience": "creator",
        "amount_month_cents": 1900,
        "amount_year_cents": 19000,
        "lookup_month": "creator_curator_month",
        "lookup_year": "creator_curator_year",
        "limits": {"matches_per_month": 10, "curated_offers_per_month": 4},
        "features": ("curated_suppliers", "early_look_48h", "badge", "public_profile", "sales_report"),
        "first_look_hours": 48,
    },
    "elite": {
        "label": "Trendsettr",
        "audience": "creator",
        "amount_month_cents": 4900,
        "amount_year_cents": 49000,
        "lookup_month": "creator_elite_month",
        "lookup_year": "creator_elite_year",
        "limits": {"matches_per_month": None, "curated_offers_per_month": 12},
        "features": ("first_look_7d",),
        "coming_soon": ("top_shortlist", "elite_perks"),
        "first_look_hours": 24 * 7,
    },
}

# Shop plan names the API already accepts. "free" uses the Starter caps.
SHOP_TIER_ALIASES = {"free": "starter"}

COUPONS = {
    "founding_curator_50_12mo": {
        "name": "Foundr 50% for 12 months",
        "percent_off": 50,
        "duration": "repeating",
        "duration_in_months": 12,
    },
    "seller_growth_50_3mo": {
        "name": "Growth 50% for 3 months",
        "percent_off": 50,
        "duration": "repeating",
        "duration_in_months": 3,
    },
    "curator_sale_waiver": {
        "name": "Waiver after 5 sales",
        "percent_off": 100,
        "duration": "once",
    },
    "elite_grant_3mo": {
        "name": "Trendsettr free for 3 months",
        "percent_off": 100,
        "duration": "repeating",
        "duration_in_months": 3,
    },
}

PROMOTION_CODES = {
    "FOUNDING50": {
        "coupon": "founding_curator_50_12mo",
        "campaign": "founding_curators",
        "channel": "promo",
        "max_redemptions": 50,
    },
    "PILOTGROWTH": {
        "coupon": "seller_growth_50_3mo",
        "campaign": "seller_pilot",
        "channel": "promo",
        "max_redemptions": 10,
    },
}

PILOT_COHORTS = {
    "seller_pilot": {
        "name": "Seller pilot",
        "role": "seller",
        "seat_cap": 10,
        "pilot_tier": "growth",
        "free_tier": "starter",
        "post_coupon": "seller_growth_50_3mo",
        "seat_env": "PILOT_SELLER_SEATS",
    },
    "founding_curators": {
        "name": "Foundrs",
        "role": "creator",
        "seat_cap": 50,
        "pilot_tier": "curator",
        "free_tier": "member",
        "post_coupon": "founding_curator_50_12mo",
        "seat_env": "PILOT_CURATOR_SEATS",
    },
}

WAIVER_SALES = 5
SURVEY_WEEK = 5
CONVERSION_TARGET_DEFAULT = 0.30

# Risk And Analytics names. Override with TRACKING_EVENT_<KEY> if the final name changes.
EVENT_NAMES = {
    "page_view": "page_view",
    "view_content": "view_content",
    "apply_start": "apply_start",
    "apply_submit": "apply_submit",
    "application_approved": "application_approved",
    "application_rejected": "application_rejected",
    "complete_registration": "complete_registration",
    "subscribe": "subscribe",
    "founding_free_to_paid": "founding_free_to_paid",
    "attributed_sale": "attributed_sale",
    "consent_revoked": "consent_revoked",
}

# server: fan out from the API. browser: the page may fire the same event_id.
# internal: never sent to an ad platform.
PLATFORM_EVENTS = {
    "page_view": {"meta": "PageView", "tiktok": "Pageview", "server": True, "browser": True},
    "view_content": {"meta": "ViewContent", "tiktok": "ViewContent", "server": True, "browser": True},
    "apply_start": {"custom": True, "server": False, "browser": True},
    "apply_submit": {"meta": "Lead", "tiktok": ["SubmitForm", "Lead"], "server": True, "browser": True},
    # Custom server events. Meta and TikTok receive the name as a custom event.
    "application_approved": {
        "meta": "application_approved",
        "tiktok": "application_approved",
        "custom": True,
        "server": True,
        "browser": False,
    },
    "application_rejected": {"internal": True},
    # complete_registration is server-only. Do not fire it from the browser.
    "complete_registration": {
        "meta": "CompleteRegistration",
        "tiktok": "CompleteRegistration",
        "server": True,
        "browser": False,
    },
    "subscribe": {"meta": "Subscribe", "tiktok": "Subscribe", "server": True, "browser": False},
    "founding_free_to_paid": {
        "meta": "founding_free_to_paid",
        "tiktok": "founding_free_to_paid",
        "custom": True,
        "server": True,
        "browser": False,
    },
    "consent_revoked": {"internal": True},
    "attributed_sale": {"internal": True},
}


def event_name(key: str) -> str:
    override = os.getenv(f"TRACKING_EVENT_{key.upper()}", "").strip()
    return override or EVENT_NAMES[key]


def pilot_months() -> int:
    """Length of the free pilot. A leftover PILOT_WEEKS value is not used."""
    raw = os.getenv("PILOT_MONTHS", "2").strip() or "2"
    try:
        months = int(raw)
    except ValueError:
        months = 2
    return max(1, months)


def pilot_start():
    """Paid launch is this date plus pilot_months(). Default is 20 Nov 2026."""
    from datetime import datetime, timezone

    raw = os.getenv("PILOT_START", "2026-11-20").strip() or "2026-11-20"
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        day = datetime(2026, 11, 20)
    return day.replace(tzinfo=timezone.utc)


def pilot_end():
    return add_months(pilot_start(), pilot_months())


def add_months(moment, months: int):
    from datetime import datetime, timedelta

    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        following = datetime(year + 1, 1, 1, tzinfo=moment.tzinfo)
    else:
        following = datetime(year, month + 1, 1, tzinfo=moment.tzinfo)
    last_day = (following - timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def tier_label(audience: str, tier: str) -> str:
    table = SELLER_TIERS if audience == "seller" else CREATOR_TIERS
    spec = table.get((tier or "").strip().lower())
    if spec is None:
        return tier or ""
    return spec["label"]


def conversion_target() -> float:
    raw = os.getenv("PILOT_CONVERSION_TARGET", "").strip()
    if not raw:
        return CONVERSION_TARGET_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return CONVERSION_TARGET_DEFAULT


def seat_cap(slug: str) -> int:
    cohort = PILOT_COHORTS[slug]
    raw = os.getenv(cohort["seat_env"], "").strip()
    if not raw:
        return int(cohort["seat_cap"])
    try:
        return max(0, int(raw))
    except ValueError:
        return int(cohort["seat_cap"])


def shop_tier(name: str) -> str:
    cleaned = (name or "").strip().lower()
    return SHOP_TIER_ALIASES.get(cleaned, cleaned or "starter")


def shop_limits(tier: str) -> dict:
    key = shop_tier(tier)
    spec = SELLER_TIERS.get(key) or SELLER_TIERS["starter"]
    return dict(spec["limits"])


def creator_limits(tier: str) -> dict:
    spec = CREATOR_TIERS.get((tier or "member").strip().lower()) or CREATOR_TIERS["member"]
    return dict(spec["limits"])


def monthly_price(tier: str) -> float:
    spec = SELLER_TIERS.get(shop_tier(tier))
    if spec is None:
        return 0.0
    return spec["amount_month_cents"] / 100.0


def annual_price(tier: str) -> float:
    spec = SELLER_TIERS.get(shop_tier(tier))
    if spec is None:
        return 0.0
    return spec["amount_year_cents"] / 100.0


def lookup_key(audience: str, tier: str, interval: str) -> str | None:
    table = SELLER_TIERS if audience == "seller" else CREATOR_TIERS
    spec = table.get(tier)
    if spec is None:
        return None
    return spec["lookup_year"] if interval == "year" else spec["lookup_month"]


def seller_reaches(tier: str) -> set[str]:
    spec = SELLER_TIERS.get(shop_tier(tier)) or SELLER_TIERS["starter"]
    return set(spec["reaches"])


def feature_enabled(audience: str, tier: str, feature: str) -> bool:
    table = SELLER_TIERS if audience == "seller" else CREATOR_TIERS
    spec = table.get(shop_tier(tier) if audience == "seller" else (tier or "member"))
    if spec is None:
        return False
    return feature in spec.get("features", ())


def public_base_url(request) -> str:
    """Absolute site origin. PUBLIC_BASE_URL wins. Otherwise use the request, which is https behind the proxy."""
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if request is None:
        return ""
    return str(request.base_url).rstrip("/")


def price_label(cents: int) -> str:
    if cents <= 0:
        return "Included with your invite"
    dollars = cents / 100.0
    text = f"{dollars:.0f}" if cents % 100 == 0 else f"{dollars:.2f}"
    return f"A${text} ex GST"
