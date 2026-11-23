"""Plan limits, Stripe price lookup keys, coupons, and pilot settings.

Prices are AUD and shown ex GST. Stripe Tax is not enabled. Annual prices are
ten months, which is two months free. Nothing here is a live Stripe key.
The pilot runs 20 Nov 2026 to 20 Jan 2027.
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
        "amount_month_cents": 2900,
        "amount_year_cents": 29000,
        "lookup_month": "seller_starter_month",
        "lookup_year": "seller_starter_year",
        # products is logged as product_limit. It is not a hard block.
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
        "features": ("csv_import", "per_creator_report"),
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
        "features": ("csv_import", "per_creator_report", "priority_matching", "export"),
        "coming_soon": ("brief_help",),
    },
}

CREATOR_TIERS = {
    "member": {
        "label": "Membr",
        "audience": "creator",
        "amount_month_cents": 1500,
        "amount_year_cents": 15000,
        "lookup_month": "creator_member_month",
        "lookup_year": "creator_member_year",
        "limits": {"matches_per_month": 4, "curated_offers_per_month": 0, "active_collabs": 3},
        "features": ("tracking_links", "basic_sales"),
    },
    "curator": {
        "label": "Curatr",
        "audience": "creator",
        "amount_month_cents": 1900,
        "amount_year_cents": 19000,
        "lookup_month": "creator_curator_month",
        "lookup_year": "creator_curator_year",
        "limits": {"matches_per_month": 12, "curated_offers_per_month": 4, "active_collabs": 6},
        "features": ("curated_suppliers", "badge", "public_profile", "sales_report"),
    },
    "elite": {
        # Display label is Curatr Pro. The id and Stripe lookup keys stay elite.
        "label": "Curatr Pro",
        "audience": "creator",
        "amount_month_cents": 4900,
        "amount_year_cents": 49000,
        "lookup_month": "creator_elite_month",
        "lookup_year": "creator_elite_year",
        "limits": {"matches_per_month": None, "curated_offers_per_month": 12, "active_collabs": 12},
        "features": ("category_matches",),
    },
}

# Shop plan names the API already accepts. "free" uses the Starter caps.
SHOP_TIER_ALIASES = {"free": "starter"}

# Coupon ids are Stripe ids. A coupon's amount, duration and applies_to cannot be
# changed in Stripe, so a changed spec gets a new id and the old one is retired.
# applies_to names the plan tiers whose Stripe product may take the coupon.
COUPONS = {
    "foundr9_curatr_month_12mo": {
        "name": "Foundr A$10 off Curatr for 12 months",
        "amount_off": 1000,
        "duration": "repeating",
        "duration_in_months": 12,
        "applies_to": [("creator", "curator")],
    },
    "pilotgrowth_growth_50_3mo": {
        "name": "Pilot Growth A$39.50 for 3 months",
        "percent_off": 50,
        "duration": "repeating",
        "duration_in_months": 3,
        "applies_to": [("seller", "growth")],
    },
    "elite_grant_3mo": {
        "name": "Curatr Pro at no charge for 3 months",
        "percent_off": 100,
        "duration": "repeating",
        "duration_in_months": 3,
    },
}

# Old Stripe coupons and codes. The setup script deactivates these promotion codes.
RETIRED_COUPONS = ("founding_curator_50_12mo", "foundr_10aud_12mo", "seller_growth_50_3mo", "curator_sale_waiver")
RETIRED_PROMOTION_CODES = ("FOUNDING50",)

# Codes are applied by the app for eligible accounts. They are not typed at Stripe
# Checkout. Each code is limited to one plan tier and one interval.
PROMOTION_CODES = {
    "FOUNDR9": {
        "coupon": "foundr9_curatr_month_12mo",
        "campaign": "founding_curators",
        "channel": "promo",
        "max_redemptions": 50,
        "role": "creator",
        "tier": "curator",
        "interval": "month",
        "months": 12,
        "discounted_month_cents": 900,
    },
    "PILOTGROWTH": {
        "coupon": "pilotgrowth_growth_50_3mo",
        "campaign": "seller_pilot",
        "channel": "promo",
        "max_redemptions": 10,
        "role": "seller",
        "tier": "growth",
        "interval": "month",
        "months": 3,
        "discounted_month_cents": 3950,
    },
}

PILOT_COHORTS = {
    "seller_pilot": {
        "name": "Seller pilot",
        "role": "seller",
        "seat_cap": 10,
        "pilot_tier": "growth",
        "free_tier": "starter",
        "post_coupon": "pilotgrowth_growth_50_3mo",
        "post_code": "PILOTGROWTH",
        "seat_env": "PILOT_SELLER_SEATS",
    },
    "founding_curators": {
        "name": "Foundrs",
        "role": "creator",
        "seat_cap": 50,
        "pilot_tier": "curator",
        "free_tier": "member",
        "post_coupon": "foundr9_curatr_month_12mo",
        "post_code": "FOUNDR9",
        "seat_env": "PILOT_CURATOR_SEATS",
    },
}

WAIVER_SALES = 5
# The 5-sale waiver is for Curatr only.
WAIVER_TIERS = ("curator",)
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
    "owed_calculated": "owed_calculated",
    "owed_marked_paid": "owed_marked_paid",
    "owed_confirmed": "owed_confirmed",
    "owed_disputed": "owed_disputed",
    "owed_dispute_withdrawn": "owed_dispute_withdrawn",
    "owed_dispute_resolved": "owed_dispute_resolved",
    "owed_overdue": "owed_overdue",
    "seller_blocked": "seller_blocked",
    "seller_unblocked": "seller_unblocked",
    "shopify_connected": "shopify_connected",
    "shopify_disconnected": "shopify_disconnected",
    "collab_agreed": "collab_agreed",
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
    # Light commission and Shopify sync. Internal only; never sent to ad platforms.
    "owed_calculated": {"internal": True},
    "owed_marked_paid": {"internal": True},
    "owed_confirmed": {"internal": True},
    "owed_disputed": {"internal": True},
    "owed_dispute_withdrawn": {"internal": True},
    "owed_dispute_resolved": {"internal": True},
    "owed_overdue": {"internal": True},
    "seller_blocked": {"internal": True},
    "seller_unblocked": {"internal": True},
    "shopify_connected": {"internal": True},
    "shopify_disconnected": {"internal": True},
    "collab_agreed": {"internal": True},
}


def event_name(key: str) -> str:
    override = os.getenv(f"TRACKING_EVENT_{key.upper()}", "").strip()
    return override or EVENT_NAMES[key]


PILOT_TIMEZONE = "Australia/Sydney"
# The pilot is free from 20 Nov 2026 until midday Sydney time on 20 Jan 2027.
# Midday keeps every monthly invoice and card charge on the 20th in Sydney in
# both AEDT and AEST, and matches "first charge on 20 January 2027".
PILOT_START_DEFAULT = "2026-11-20T00:00:00+11:00"
PILOT_END_DEFAULT = "2027-01-20T12:00:00+11:00"
# The pilot-ending email goes out by 6 Jan 2027. The job sends it from this time.
PILOT_REMINDER_DEFAULT = "2027-01-05T10:00:00+11:00"
PILOT_LABEL = "20 November 2026 to 20 January 2027"
PILOT_END_LABEL = "20 January 2027"


def _env_moment(name: str, default: str):
    from datetime import datetime, timezone

    raw = os.getenv(name, "").strip() or default
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        moment = datetime.fromisoformat(default)
    if moment.tzinfo is None:
        from zoneinfo import ZoneInfo

        moment = moment.replace(tzinfo=ZoneInfo(PILOT_TIMEZONE))
    return moment.astimezone(timezone.utc)


def pilot_months() -> int:
    """Whole months in the pilot (2). The dates come from pilot_start and pilot_end."""
    return 2


def pilot_start():
    """20 Nov 2026, Sydney time. PILOT_START may override it with an ISO date."""
    return _env_moment("PILOT_START", PILOT_START_DEFAULT)


def pilot_end():
    """The trial end and first charge: 20 Jan 2027 12:00 Sydney. PILOT_END may override it."""
    return _env_moment("PILOT_END", PILOT_END_DEFAULT)


def pilot_reminder_at():
    return _env_moment("PILOT_REMINDER_AT", PILOT_REMINDER_DEFAULT)


def sydney_date_label(moment) -> str:
    from zoneinfo import ZoneInfo

    local = moment.astimezone(ZoneInfo(PILOT_TIMEZONE))
    return f"{local.day} {local.strftime('%B %Y')}"


def operator_legal_name() -> str:
    """The legal operator shown on public pages. Unset means pilot applications stay closed."""
    return os.getenv("OPERATOR_LEGAL_NAME", "").strip()


def promo_spec(code: str) -> dict:
    return PROMOTION_CODES.get((code or "").strip().upper(), {})


def promo_allows(code: str, *, role: str, tier: str, interval: str) -> bool:
    spec = promo_spec(code)
    if not spec:
        return False
    return spec.get("role") == role and spec.get("tier") == tier and spec.get("interval") == interval


def pilot_offer_code(role: str, account: dict, tier: str, interval: str) -> str:
    """The post-pilot code an account gets automatically for this plan, or ""."""
    account = account or {}
    slug = str(account.get("signup_campaign") or "")
    if role == "seller" and not slug and account.get("is_pilot"):
        slug = "seller_pilot"
    if role == "creator" and not slug and account.get("founding_number") not in (None, ""):
        slug = "founding_curators"
    cohort = PILOT_COHORTS.get(slug)
    if cohort is None or cohort["role"] != role:
        return ""
    code = cohort.get("post_code") or ""
    if not code or not promo_allows(code, role=role, tier=tier, interval=interval):
        return ""
    return code


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


def amount_label(cents: int) -> str:
    dollars = cents / 100.0
    text = f"{dollars:,.0f}" if cents % 100 == 0 else f"{dollars:,.2f}"
    return f"A${text}"


def price_label(cents: int) -> str:
    return f"{amount_label(cents)} ex GST"


def tier_spec(audience: str, tier: str) -> dict:
    table = SELLER_TIERS if audience == "seller" else CREATOR_TIERS
    return table.get((tier or "").strip().lower()) or {}
