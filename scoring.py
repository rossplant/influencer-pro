"""Deterministic Plantr Collab match score. No language model.

The score is 0–100. It adds a fit score to an expected-performance score.
`optimise_for` selects the weights. Missing performance inputs use DEFAULTS
and are labeled estimates. Hard exclusions remove a creator before scoring.

est_sales = reach × engagement_rate × conversion_rate
est_revenue = est_sales × price
est_seller_commission_payout = est_revenue × (commission_rate / 100)

Reach uses avg_views when present. Otherwise it is estimated from the follower
band midpoint times DEFAULTS["view_rate_of_followers"], or from
DEFAULTS["reach_views"] when the band is missing too.
"""

from __future__ import annotations

from typing import Any, Optional

MATCH_VERSION = "rules-v1"

DEFAULTS = {
    "reach_views": 1000.0,
    "view_rate_of_followers": 0.1,
    "engagement_rate": 0.03,
    "conversion_rate": 0.01,
    "reference_reach": 10000.0,
    "reference_sales": 50.0,
    "reference_revenue": 2500.0,
    "underserved_boost": 8.0,
}

WEIGHTS = {
    "balanced": {
        "category": 22,
        "tags": 14,
        "price_band": 10,
        "platform": 10,
        "audience": 10,
        "reach": 10,
        "sales": 12,
        "revenue": 12,
    },
    "reach": {
        "category": 12,
        "tags": 8,
        "price_band": 5,
        "platform": 8,
        "audience": 7,
        "reach": 40,
        "sales": 10,
        "revenue": 10,
    },
    "sales": {
        "category": 12,
        "tags": 8,
        "price_band": 8,
        "platform": 6,
        "audience": 6,
        "reach": 10,
        "sales": 35,
        "revenue": 15,
    },
    "revenue": {
        "category": 12,
        "tags": 6,
        "price_band": 10,
        "platform": 5,
        "audience": 5,
        "reach": 7,
        "sales": 15,
        "revenue": 40,
    },
}

OPTIMISE_FOR = frozenset(WEIGHTS)


def rank_creators(
    product: dict,
    creators: list[dict],
    *,
    shop: Optional[dict] = None,
    optimise_for: str = "balanced",
    blocked_creator_ids: Optional[list[str]] = None,
    limit: int = 10,
) -> dict:
    if optimise_for not in WEIGHTS:
        raise ValueError("optimise_for must be balanced, reach, sales, or revenue")
    weights = WEIGHTS[optimise_for]
    blocked = {str(item) for item in (blocked_creator_ids or [])}
    ranked = []
    excluded = []
    for creator in creators:
        decision = score_creator(product, creator, shop=shop, weights=weights, blocked=blocked)
        if decision["excluded"]:
            excluded.append(
                {
                    "creator_id": decision["creator_id"],
                    "handle": decision["handle"],
                    "reasons": decision["reasons"],
                }
            )
            continue
        ranked.append(decision)
    ranked.sort(key=lambda row: (-row["score"], (row["handle"] or "").lower(), row["creator_id"] or ""))
    limited = ranked[: max(limit, 0)]
    return {
        "match_version": MATCH_VERSION,
        "optimise_for": optimise_for,
        "matches": [
            {
                "creator": row["creator"],
                "score": row["score"],
                "components": row["components"],
                "estimates": row["estimates"],
                "reasons": row["reasons"],
            }
            for row in limited
        ],
        "excluded": excluded,
    }


def score_creator(
    product: dict,
    creator: dict,
    *,
    shop: Optional[dict],
    weights: dict,
    blocked: set[str],
) -> dict:
    creator_id = str(creator.get("influencer_id") or creator.get("id") or "")
    handle = creator.get("handle")
    exclusions = _exclusions(product, creator, creator_id, blocked)
    if exclusions:
        return {
            "excluded": True,
            "creator_id": creator_id or None,
            "handle": handle,
            "creator": creator,
            "reasons": exclusions,
            "score": 0,
            "components": {},
            "estimates": {},
        }

    category = _category_fit(product, creator)
    tags = _tag_fit(product, creator)
    price_band = _price_fit(product, creator)
    platform = _platform_fit(product, creator)
    audience = _audience_fit(product, creator, shop)
    performance = _performance(product, creator, shop)
    fits = {
        "category": category,
        "tags": tags,
        "price_band": price_band,
        "platform": platform,
        "audience": audience,
        "reach": performance["reach_fit"],
        "sales": performance["sales_fit"],
        "revenue": performance["revenue_fit"],
    }
    components = {}
    total = 0.0
    for name, weight in weights.items():
        fit = float(fits[name]["fit"])
        points = round(fit * weight, 4)
        total += points
        components[name] = {
            "weight": weight,
            "fit": fit,
            "points": points,
            "detail": fits[name]["detail"],
            "estimated": bool(fits[name].get("estimated")),
        }
    boost = 0.0
    boost_reason = None
    if shop and shop.get("is_underserved"):
        boost = float(DEFAULTS["underserved_boost"])
        total += boost
        boost_reason = (
            f"Underserved supplier boost of {boost:g} points applied. "
            "This is a priority adjustment, not a measured sales figure."
        )
    score = round(min(100.0, max(0.0, total)), 4)
    reasons = [fits[name]["detail"] for name in weights]
    reasons.extend(performance["reasons"])
    reasons = _dedupe(reasons)
    if boost_reason:
        reasons.append(boost_reason)
        components["underserved_boost"] = {"points": boost, "estimated": False, "detail": boost_reason}
    return {
        "excluded": False,
        "creator_id": creator_id or None,
        "handle": handle,
        "creator": creator,
        "score": score,
        "components": components,
        "estimates": performance["estimates"],
        "reasons": reasons,
    }


def _exclusions(product: dict, creator: dict, creator_id: str, blocked: set[str]) -> list[str]:
    reasons = []
    if creator_id and creator_id in blocked:
        reasons.append("Shop blocked this creator.")
    categories = {_norm(product.get("category")), _norm(product.get("subcategory"))} - {""}
    excluded_categories = {_norm(item) for item in creator.get("excluded_categories") or []}
    hit = categories & excluded_categories
    if hit:
        reasons.append("Excluded category: " + ", ".join(sorted(hit)) + ".")
    claim_text = " ".join(_claim_texts(product))
    for item in creator.get("excluded_claims") or []:
        needle = _norm(item)
        if needle and needle in claim_text:
            reasons.append(f"Excluded claim: {item}.")
    product_tags = {_norm(tag) for tag in _product_tags(product)}
    excluded_tags = {_norm(item) for item in (creator.get("excluded_tags") or [])}
    excluded_tags.update(_norm(item) for item in (creator.get("restrictions") or []))
    tag_hit = product_tags & excluded_tags
    if tag_hit:
        reasons.append("Excluded tag: " + ", ".join(sorted(tag_hit)) + ".")
    return reasons


def _category_fit(product: dict, creator: dict) -> dict:
    category = _norm(product.get("category"))
    subcategory = _norm(product.get("subcategory"))
    targets = {category, subcategory} - {""}
    if not targets:
        return {"fit": 0.0, "detail": "Category missing, so category fit is 0."}
    active = {_norm(item) for item in creator.get("active_categories") or []}
    desired = {_norm(item) for item in creator.get("desired_categories") or []}
    if targets & active:
        return {"fit": 1.0, "detail": "Active category match."}
    if targets & desired:
        return {"fit": 0.5, "detail": "Desired category match (partial). The creator wants this category but does not work in it yet."}
    return {"fit": 0.0, "detail": "No active or desired category match."}


def _tag_fit(product: dict, creator: dict) -> dict:
    tokens = _product_tags(product)
    interests = {_norm(item) for item in creator.get("areas_of_interest") or []} - {""}
    if not tokens or not interests:
        return {"fit": 0.0, "detail": "Not enough tags or interests to score overlap."}
    overlap = [token for token in tokens if token in interests]
    fit = len(set(overlap)) / len(set(tokens))
    if not overlap:
        return {"fit": 0.0, "detail": "No tag or attribute overlap with the creator's interests."}
    return {"fit": fit, "detail": "Tag or attribute overlap: " + ", ".join(sorted(set(overlap))) + "."}


def _price_fit(product: dict, creator: dict) -> dict:
    price = _number(product.get("price"))
    band = creator.get("typical_price_band") or {}
    if not isinstance(band, dict):
        band = {}
    low = _number(band.get("min"))
    high = _number(band.get("max"))
    if price is None or (low is None and high is None):
        return {"fit": 0.0, "detail": "Price band unknown, so price fit is 0."}
    if low is not None and price < low:
        return {"fit": 0.0, "detail": "Price is below the creator's typical band."}
    if high is not None and price > high:
        return {"fit": 0.0, "detail": "Price is above the creator's typical band."}
    return {"fit": 1.0, "detail": "Price is inside the creator's typical band."}


def _platform_fit(product: dict, creator: dict) -> dict:
    product_platforms = {_norm(item) for item in _as_list(product.get("platforms"))}
    attributes = product.get("attributes") or {}
    if isinstance(attributes, dict):
        product_platforms.update(_norm(item) for item in _as_list(attributes.get("platforms")))
    product_platforms.discard("")
    creator_platforms = {_norm(item) for item in _as_list(creator.get("platforms"))} - {""}
    if not product_platforms or not creator_platforms:
        return {"fit": 0.0, "detail": "Platform unknown, so platform fit is 0."}
    overlap = product_platforms & creator_platforms
    if not overlap:
        return {"fit": 0.0, "detail": "No shared platform."}
    return {"fit": 1.0, "detail": "Platform overlap: " + ", ".join(sorted(overlap)) + "."}


def _audience_fit(product: dict, creator: dict, shop: Optional[dict]) -> dict:
    attributes = product.get("attributes") if isinstance(product.get("attributes"), dict) else {}
    target_geo = _norm(attributes.get("target_geo") or (shop or {}).get("target_geo"))
    target_band = _norm(attributes.get("target_follower_band") or (shop or {}).get("target_follower_band"))
    demographics = creator.get("audience_demographics") or {}
    if not isinstance(demographics, dict):
        demographics = {}
    creator_geo = _norm(demographics.get("location_focus"))
    creator_band = _norm(creator.get("follower_band"))
    checks = []
    if target_geo and creator_geo:
        checks.append(target_geo == creator_geo)
    if target_band and creator_band:
        checks.append(target_band == creator_band)
    if not checks:
        return {"fit": 0.0, "detail": "Audience target unknown, so audience fit is 0."}
    fit = sum(1 for item in checks if item) / len(checks)
    if fit == 0:
        return {"fit": 0.0, "detail": "Audience does not match the seller's target."}
    if fit == 1:
        return {"fit": 1.0, "detail": "Audience matches the seller's target."}
    return {"fit": fit, "detail": "Audience is a partial match to the seller's target."}


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    unique = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _plain_number(value: float) -> str:
    rounded = round(float(value), 4)
    nearest = round(rounded)
    if abs(rounded - nearest) < 1e-9:
        return str(int(nearest))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _performance(product: dict, creator: dict, shop: Optional[dict] = None) -> dict:
    reach, reach_estimated, reach_reason = _reach(creator)
    engagement, engagement_estimated = _rate(
        creator.get("engagement_rate"),
        creator.get("average_engagement_rate"),
        DEFAULTS["engagement_rate"],
    )
    conversion, conversion_estimated = _rate(creator.get("est_conversion_rate"), None, DEFAULTS["conversion_rate"])
    est_sales = reach * engagement * conversion
    price = _number(product.get("price"))
    commission_rate = _number(product.get("commission_rate"))
    est_revenue = None if price is None else est_sales * price
    payout = None
    if est_revenue is not None and commission_rate is not None:
        payout = est_revenue * (commission_rate / 100.0)
    reach_fit = min(1.0, reach / DEFAULTS["reference_reach"]) if DEFAULTS["reference_reach"] else 0.0
    sales_fit = min(1.0, est_sales / DEFAULTS["reference_sales"]) if DEFAULTS["reference_sales"] else 0.0
    revenue_fit = 0.0 if est_revenue is None else min(1.0, est_revenue / DEFAULTS["reference_revenue"])
    reasons = [reach_reason]
    estimate_names = []
    if reach_estimated:
        estimate_names.append("reach")
    if engagement_estimated:
        estimate_names.append("engagement_rate")
        reasons.append(
            f"Engagement rate is an estimate. Default {DEFAULTS['engagement_rate']} was used because the creator did not provide one."
        )
    else:
        reasons.append("Engagement rate uses the creator's figure.")
    if conversion_estimated:
        estimate_names.append("conversion_rate")
        reasons.append(
            f"Conversion rate is an estimate. Default {DEFAULTS['conversion_rate']} was used because the creator did not provide one."
        )
    else:
        reasons.append("Conversion rate uses the creator's figure.")
    reasons.append(
        "Estimated sales = reach × engagement × conversion"
        + (" (estimate)." if estimate_names else " (uses the creator's figures).")
    )
    if est_revenue is None:
        reasons.append("Estimated revenue was not calculated because price is missing.")
    else:
        reasons.append("Estimated revenue = estimated sales × price.")
    if payout is None:
        reasons.append("Estimated seller commission payout was not calculated because price or commission rate is missing.")
    else:
        reasons.append("Estimated seller commission payout = estimated revenue × commission rate.")
    source = "estimated inputs" if estimate_names else "creator figures"
    currency = product.get("currency") or (shop or {}).get("currency")
    sales_detail = f"Estimated sales {_plain_number(est_sales)} from {source}."
    if est_revenue is None:
        revenue_detail = "Revenue not estimated; price is missing."
        revenue_estimated = False
    else:
        amount = _plain_number(est_revenue)
        code = str(currency).strip().upper() if currency else ""
        money = f"{amount} {code}-equivalent" if code else amount
        revenue_detail = f"Estimated revenue {money} from {source}."
        revenue_estimated = True
    return {
        "reach_fit": {"fit": reach_fit, "detail": reach_reason, "estimated": reach_estimated},
        "sales_fit": {
            "fit": sales_fit,
            "detail": sales_detail,
            "estimated": True,
        },
        "revenue_fit": {
            "fit": revenue_fit,
            "detail": revenue_detail,
            "estimated": revenue_estimated,
        },
        "reasons": reasons,
        "estimates": {
            "reach": reach,
            "reach_estimated": reach_estimated,
            "engagement_rate": engagement,
            "engagement_estimated": engagement_estimated,
            "conversion_rate": conversion,
            "conversion_estimated": conversion_estimated,
            "est_sales": est_sales,
            "est_revenue": est_revenue,
            "est_seller_commission_payout": payout,
            "estimated_inputs": estimate_names,
        },
    }


def _reach(creator: dict) -> tuple[float, bool, str]:
    views = _number(creator.get("avg_views"))
    if views is not None and views >= 0:
        return views, False, "Reach uses the creator's average views."
    midpoint = _follower_midpoint(creator.get("follower_band"))
    if midpoint is not None:
        estimated = midpoint * DEFAULTS["view_rate_of_followers"]
        return (
            estimated,
            True,
            "Reach is an estimate: follower-band midpoint × "
            f"{DEFAULTS['view_rate_of_followers']} because average views were missing.",
        )
    return (
        float(DEFAULTS["reach_views"]),
        True,
        f"Reach is an estimate. Default {DEFAULTS['reach_views']:g} views was used because views and follower band were missing.",
    )


def _rate(primary: Any, secondary: Any, default: float) -> tuple[float, bool]:
    for value in (primary, secondary):
        number = _number(value)
        if number is None:
            continue
        if number > 1:
            number = number / 100.0
        return max(0.0, number), False
    return default, True


def _follower_midpoint(band: Any) -> Optional[float]:
    if not isinstance(band, str) or not band.strip():
        return None
    text = band.strip().lower().replace("-", "_").replace(" ", "")
    if text in {"100k_plus", "100k+"}:
        return 150000.0
    if text.startswith("under_"):
        return _scale_token(text.split("_", 1)[1]) / 2.0
    parts = [part for part in text.split("_") if part]
    numbers = [_scale_token(part) for part in parts]
    numbers = [number for number in numbers if number is not None]
    if len(numbers) >= 2:
        return (numbers[0] + numbers[1]) / 2.0
    if len(numbers) == 1:
        return numbers[0]
    return None


def _scale_token(token: str) -> Optional[float]:
    token = token.replace("+", "")
    multiplier = 1.0
    if token.endswith("k"):
        multiplier = 1000.0
        token = token[:-1]
    elif token.endswith("m"):
        multiplier = 1_000_000.0
        token = token[:-1]
    try:
        return float(token) * multiplier
    except ValueError:
        return None


def _product_tags(product: dict) -> list[str]:
    tags = [_norm(item) for item in _as_list(product.get("tags"))]
    attributes = product.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            if key in {"platforms", "target_geo", "target_follower_band"}:
                continue
            tags.append(_norm(key))
            if isinstance(value, str):
                tags.append(_norm(value))
    return [tag for tag in tags if tag]


def _claim_texts(product: dict) -> list[str]:
    texts = []
    for claim in product.get("claims") or []:
        if isinstance(claim, dict):
            texts.append(_norm(claim.get("claim")))
        else:
            texts.append(_norm(claim))
    return [text for text in texts if text]


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _number(value: Any) -> Optional[float]:
    if value is None or value is False or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
