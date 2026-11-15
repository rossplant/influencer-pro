import hmac
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from grok_helper import generate_with_grok

# TODO: replace INFLUENCERS with Postgres. This process-local dict is lost on
# restart and is not shared across Railway replicas.
INFLUENCERS: Dict[str, Dict[str, Any]] = {}

_rate_buckets: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

ROOT = Path(__file__).resolve().parent
HTML_PAGES = (
    "plantr_onboarding.html",
    "onboarding_form.html",
    "plantr_dashboard.html",
    "plantr_admin.html",
)

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
    availability: Availability = "open"
    commission_preference: Optional[CommissionPreference] = None


class ProductIn(BaseModel):
    title: str
    description_short: str = ""
    description_long: str = ""
    category: str = ""
    images: List[str] = []


class EnrichResponse(BaseModel):
    signal_score: int
    creator_talking_points: List[str]
    common_questions: List[Dict[str, str]] = []
    enriched: Dict[str, Any]
    source: Literal["grok", "fallback"]


class MatchIn(BaseModel):
    influencer: Dict[str, Any]
    products: List[Dict[str, Any]]


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


def reset_runtime_state() -> None:
    """Clear process-local influencers and rate-limit buckets. Used by tests."""
    INFLUENCERS.clear()
    with _rate_lock:
        _rate_buckets.clear()


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


app = FastAPI(title="plantr-collab API", version="0.9")
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _html(name: str) -> FileResponse:
    path = ROOT / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(path)


@app.get("/")
def root():
    return {
        "status": "plantr-collab API running",
        "version": "0.9",
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


@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn, _: None = Depends(guard_request)):
    data = product.model_dump()
    grok = generate_with_grok(data)
    source = grok.get("source", "fallback")
    if source not in ("grok", "fallback"):
        source = "fallback"
    return {
        "signal_score": 70,
        "creator_talking_points": grok.get("talking_points", []),
        "common_questions": grok.get("faqs", []),
        "enriched": data,
        "source": source,
    }


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
    return {"matches": ranked[:10]}


@app.post("/onboard-influencer")
def onboard_influencer(influencer: InfluencerIn, _: None = Depends(guard_request)):
    influencer_id = str(uuid.uuid4())
    record = influencer.model_dump()
    record["influencer_id"] = influencer_id
    INFLUENCERS[influencer_id] = record
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
