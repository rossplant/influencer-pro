import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import db
import services
from grok_helper import generate_with_grok

# Process-local creator profiles. This is db.memory_creators, used when
# DATABASE_URL is unset. Postgres is the system of record once that variable is set.
INFLUENCERS: Dict[str, Dict[str, Any]] = db.memory_creators

_rate_buckets: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

ROOT = Path(__file__).resolve().parent
HTML_PAGES = (
    "plantr_onboarding.html",
    "onboarding_form.html",
    "plantr_dashboard.html",
    "plantr_admin.html",
)
APP_VERSION = "0.10"

PromotionStyle = Literal[
    "educational",
    "unboxing",
    "before-after",
    "lifestyle",
    "gift guide",
    "review",
    "LIVE demo",
    "storytelling",
]
ExpertiseLevel = Literal["beginner", "intermediate", "expert"]
Availability = Literal["open", "selective", "paused"]
CommissionPreference = Literal["standard", "higher_for_exclusives"]
class AudienceDemographics(BaseModel):
    age_range: Optional[str] = None
    gender_skew: Optional[str] = None
    location_focus: Optional[str] = None


class InfluencerIn(BaseModel):
    """Request body checked against influencer_schema.json.

    influencer_id is required on the stored profile and is generated here.
    """

    handle: str = Field(min_length=1)
    name: Optional[str] = None
    bio: Optional[str] = None
    areas_of_interest: List[str] = Field(min_length=1)
    expertise_level: Dict[str, ExpertiseLevel] = Field(default_factory=dict)
    preferred_promotion_styles: List[PromotionStyle] = Field(min_length=1)
    audience_demographics: Optional[AudienceDemographics] = None
    content_formats: List[str] = Field(default_factory=list)
    average_engagement_rate: Optional[float] = None
    past_categories: List[str] = Field(default_factory=list)
    restrictions: List[str] = Field(default_factory=list)
    excluded_categories: List[str] = Field(default_factory=list)
    excluded_claims: List[str] = Field(default_factory=list)
    active_categories: List[str] = Field(default_factory=list)
    desired_categories: List[str] = Field(default_factory=list)
    avg_views: Optional[int] = None
    engagement_rate: Optional[float] = None
    metrics_source: Literal["self_reported", "platform_api", "manual_verified"] = "self_reported"
    metrics_as_of: Optional[str] = None
    est_conversion_rate: Optional[float] = None
    avg_order_value: Optional[float] = None
    typical_price_band: Optional[Dict[str, Any]] = None
    availability: Availability = "open"
    commission_preference: Optional[CommissionPreference] = None
    platforms: List[str] = Field(default_factory=list)
    follower_band: Optional[str] = None


class ClaimIn(BaseModel):
    claim: str = Field(min_length=1)
    source: str = Field(min_length=1)
    evidence: Optional[str] = None
    confidence: Optional[str] = None


class ProductIn(BaseModel):
    title: str
    description_short: str = ""
    description_long: str = ""
    category: str = ""
    subcategory: Optional[str] = None
    images: List[str] = []
    shop_id: Optional[str] = None
    product_id: Optional[str] = None
    creator_id: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    claims: List[ClaimIn] = Field(default_factory=list)
    commission_rate: Optional[float] = Field(default=None, ge=0, le=100)


class EnrichResponse(BaseModel):
    signal_score: int
    creator_talking_points: List[str]
    common_questions: List[Dict[str, str]] = []
    enriched: Dict[str, Any]
    source: Literal["grok", "fallback"]
    product_id: Optional[str] = None
    brief_id: Optional[str] = None


class BriefSentIn(BaseModel):
    actor: Literal["system", "operator", "api"] = "api"


class MatchIn(BaseModel):
    influencer: Dict[str, Any]
    products: List[Dict[str, Any]]
    shop_id: Optional[str] = None
    product_id: Optional[str] = None


class ShopIn(BaseModel):
    name: str = Field(min_length=1)
    shopify_domain: Optional[str] = None
    plan_tier: Optional[str] = None
    plan_status: Optional[str] = None
    is_pilot: bool = False
    is_underserved: bool = False
    underserved_reason: Optional[str] = None
    country: Optional[str] = None
    market: Optional[str] = None
    currency: Optional[str] = None
    primary_category: Optional[str] = None


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def cors_origins() -> List[str]:
    if "CORS_ORIGINS" not in os.environ:
        return ["*"]
    raw = os.environ["CORS_ORIGINS"].strip()
    if raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def max_body_bytes() -> int:
    return max(_int_env("MAX_BODY_BYTES", 1_048_576), 1)


def configured_api_keys() -> List[str]:
    return [part.strip() for part in os.getenv("API_KEYS", "").split(",") if part.strip()]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _key_matches(provided: str, keys: List[str]) -> bool:
    return any(hmac.compare_digest(provided, key) for key in keys)


def api_key_label(raw: Optional[str]) -> Optional[str]:
    """Stable label for a key. The raw key is never returned or stored."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def enforce_rate_limit(identity: str) -> None:
    limit = max(_int_env("RATE_LIMIT_REQUESTS", 30), 1)
    window = max(_int_env("RATE_LIMIT_WINDOW_SECONDS", 60), 1)
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[identity]
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(window)},
            )
        bucket.append(now)


def guard_request(request: Request) -> None:
    """Rate-limit every guarded call. Require X-API-Key only when API_KEYS is set."""
    keys = configured_api_keys()
    provided = request.headers.get("x-api-key", "")
    valid = bool(keys) and bool(provided) and _key_matches(provided, keys)
    if keys and not valid:
        enforce_rate_limit(f"ip:{client_ip(request)}")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    identity = f"key:{provided}" if valid else f"ip:{client_ip(request)}"
    enforce_rate_limit(identity)


def init_storage() -> None:
    db.init_storage()


def reset_runtime_state() -> None:
    """Clear process-local data and rate-limit buckets, then re-read DATABASE_URL."""
    db.reset_memory()
    with _rate_lock:
        _rate_buckets.clear()
    init_storage()


def score_match(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    tags = set()
    for key in ("target_audience", "occasions"):
        tags.update(str(item).lower() for item in product.get(key) or [])
    category = product.get("category")
    if category:
        tags.add(str(category).lower())
    interests = {str(item).lower() for item in influencer.get("areas_of_interest") or []}
    score = float(len(tags & interests) * 10)
    styles = {str(item).lower() for item in influencer.get("preferred_promotion_styles") or []}
    if "educational" in styles and product.get("care_instructions"):
        score += 15
    if "lifestyle" in styles and product.get("occasions"):
        score += 10
    return max(score, 0)


def public_profile(influencer: InfluencerIn, influencer_id: str) -> Dict[str, Any]:
    """Profile dict returned to clients. Empty new fields stay off the payload."""
    record = influencer.model_dump()
    record["influencer_id"] = influencer_id
    if record.get("engagement_rate") is None and record.get("average_engagement_rate") is not None:
        record["engagement_rate"] = record["average_engagement_rate"]
    record.pop("average_engagement_rate", None)
    if not record.get("platforms"):
        record.pop("platforms", None)
    if not record.get("follower_band"):
        record.pop("follower_band", None)
    if not record.get("excluded_categories"):
        record.pop("excluded_categories", None)
    if not record.get("excluded_claims"):
        record.pop("excluded_claims", None)
    return record


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = max_body_bytes()
        content_length = _content_length(scope)
        if content_length is not None and content_length > limit:
            await _reject_too_large(scope, send)
            return

        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                break
            body = message.get("body") or b""
            total += len(body)
            if total > limit:
                await _reject_too_large(scope, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        pending = list(messages)

        async def replay_receive():
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


class RequestLogMiddleware:
    """JSON line per request on stdout, plus an api_requests row when a database is configured.

    The line carries ts, route, method, status, latency_ms, and the key label.
    The raw API key and the request/response bodies are not logged or stored.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        request_id = str(uuid.uuid4())
        token = db.request_id_var.set(request_id)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id
        else:
            setattr(state, "request_id", request_id)

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            route = scope.get("path") or "/"
            method = scope.get("method") or ""
            label = api_key_label(_header(scope, "x-api-key"))
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "request",
                "request_id": request_id,
                "route": route,
                "method": method,
                "status": status_code,
                "latency_ms": latency_ms,
                "key": label,
            }
            print(json.dumps(record), flush=True)
            db.record_api_request(
                ts=datetime.now(timezone.utc),
                route=route,
                method=method,
                status=status_code,
                latency_ms=latency_ms,
                api_key_id=label,
                request_id=request_id,
                grok_tokens_prompt=_state_value(scope, "grok_tokens_prompt"),
                grok_tokens_completion=_state_value(scope, "grok_tokens_completion"),
                grok_fallback=_state_value(scope, "grok_fallback"),
                grok_latency_ms=_state_value(scope, "grok_latency_ms"),
                grok_model=_state_value(scope, "grok_model"),
            )
            db.request_id_var.reset(token)


def _header(scope, name: str) -> str:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            return value.decode("latin-1")
    return ""


def _state_value(scope, name: str):
    state = scope.get("state")
    if state is None:
        return None
    if isinstance(state, dict):
        return state.get(name)
    return getattr(state, name, None)


def _content_length(scope) -> Optional[int]:
    for name, value in scope.get("headers") or []:
        if name.lower() == b"content-length":
            try:
                return int(value.decode())
            except ValueError:
                return None
    return None


async def _reject_too_large(scope, send) -> None:
    response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
    await response(scope, _closed_receive, send)


async def _closed_receive():
    return {"type": "http.disconnect"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_storage()
    yield


app = FastAPI(title="Plantr Collab API", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)


def _html(name: str) -> FileResponse:
    path = ROOT / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(path)


def _attach_grok_obs(request: Request, grok: Dict[str, Any]) -> None:
    obs = grok.pop("_obs", None) or {}
    request.state.grok_tokens_prompt = obs.get("prompt_tokens")
    request.state.grok_tokens_completion = obs.get("completion_tokens")
    request.state.grok_fallback = obs.get("fallback")
    request.state.grok_latency_ms = obs.get("latency_ms")
    request.state.grok_model = obs.get("model")


@app.get("/")
def root():
    return {
        "status": "Plantr Collab API running",
        "version": APP_VERSION,
        "grok_enabled": bool(os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")),
    }


@app.get("/plantr_onboarding.html")
def plantr_onboarding_page():
    return _html("plantr_onboarding.html")


@app.get("/onboarding_form.html")
def onboarding_form_page():
    return _html("onboarding_form.html")


@app.get("/plantr_dashboard.html")
def plantr_dashboard_page():
    return _html("plantr_dashboard.html")


@app.get("/plantr_admin.html")
def plantr_admin_page():
    return _html("plantr_admin.html")


@app.post("/shops")
def create_shop(body: ShopIn, _: None = Depends(guard_request)):
    return services.create_shop(
        name=body.name,
        shopify_domain=body.shopify_domain,
        plan_tier=body.plan_tier,
        plan_status=body.plan_status,
        is_pilot=body.is_pilot,
        is_underserved=body.is_underserved,
        underserved_reason=body.underserved_reason,
        country=body.country,
        market=body.market,
        currency=body.currency,
        primary_category=body.primary_category,
    )


@app.get("/shops/{shop_id}")
def get_shop(shop_id: str, _: None = Depends(guard_request)):
    shop = services.fetch_shop(shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@app.get("/creators/{creator_id}")
def get_creator(creator_id: str, _: None = Depends(guard_request)):
    creator = services.fetch_creator(creator_id)
    if creator is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    return creator


@app.post("/enrich", response_model=EnrichResponse, response_model_exclude_none=True)
def enrich(product: ProductIn, request: Request, _: None = Depends(guard_request)):
    data = {
        "title": product.title,
        "description_short": product.description_short,
        "description_long": product.description_long,
        "category": product.category,
        "images": product.images,
    }
    if product.shop_id or product.product_id:
        services.prepare_enrichment(product.shop_id, product.product_id)
    grok = generate_with_grok(data)
    _attach_grok_obs(request, grok)
    source = grok.get("source", "fallback")
    if source not in ("grok", "fallback"):
        source = "fallback"
    saved = None
    if product.shop_id or product.product_id:
        saved = services.save_enrichment(
            shop_id=product.shop_id,
            product_id=product.product_id,
            creator_id=product.creator_id,
            title=product.title,
            description_short=product.description_short,
            description_long=product.description_long,
            category=product.category,
            subcategory=product.subcategory,
            images=product.images,
            price=product.price,
            currency=product.currency,
            tags=product.tags,
            attributes=product.attributes,
            claims=[claim.model_dump() for claim in product.claims],
            commission_rate=product.commission_rate,
            fields_set=set(product.model_fields_set),
            source=source,
            talking_points=grok.get("talking_points", []),
            faqs=grok.get("faqs", []),
        )
    body = {
        "signal_score": 70,
        "creator_talking_points": grok.get("talking_points", []),
        "common_questions": grok.get("faqs", []),
        "enriched": data,
        "source": source,
    }
    if saved:
        body["product_id"] = saved["product_id"]
        body["brief_id"] = saved["brief_id"]
    return body


@app.post("/briefs/{brief_id}/sent")
def mark_brief_sent(brief_id: str, body: BriefSentIn, _: None = Depends(guard_request)):
    return services.mark_brief_sent(brief_id, body.actor)


@app.post("/match")
def match(body: MatchIn, _: None = Depends(guard_request)):
    ranked = []
    for product in body.products:
        ranked.append(
            {
                "product": product,
                "match_score": score_match(product, body.influencer),
            }
        )
    ranked.sort(key=lambda item: item["match_score"], reverse=True)
    ranked = ranked[:10]
    shortlist_id = services.persist_match(
        influencer=body.influencer,
        ranked=ranked,
        shop_id=body.shop_id,
        product_id=body.product_id,
    )
    result: Dict[str, Any] = {"matches": ranked}
    if shortlist_id:
        result["shortlist_id"] = shortlist_id
    return result


@app.post("/onboard-influencer")
def onboard_influencer(influencer: InfluencerIn, _: None = Depends(guard_request)):
    influencer_id = str(uuid.uuid4())
    record = public_profile(influencer, influencer_id)
    services.persist_creator(record)
    return {"id": influencer_id, "influencer": record}


@app.post("/sync-shopify")
def sync_shopify(_: None = Depends(guard_request)):
    store = os.getenv("SHOPIFY_STORE", "").strip()
    token = os.getenv("SHOPIFY_TOKEN", "").strip()
    if not store or not token:
        raise HTTPException(
            status_code=501,
            detail="Shopify sync isn't configured. Set SHOPIFY_STORE and SHOPIFY_TOKEN.",
        )
    # Credentials may be present, but this build does not call Shopify.
    raise HTTPException(
        status_code=501,
        detail="Shopify sync is not implemented yet. No products were synced.",
    )
