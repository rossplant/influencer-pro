import hashlib
import html
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

import admin_data
import attribution
import auth
import billing_config
import portal
import db
import events
import services
from grok_helper import generate_with_grok
import scoring
import stripe_billing

# Process-local creator profiles. This is db.memory_creators, used when
# DATABASE_URL is unset. Postgres is the system of record once that variable is set.
INFLUENCERS: Dict[str, Dict[str, Any]] = db.memory_creators

_rate_buckets: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

ROOT = Path(__file__).resolve().parent
HTML_PAGES = (
    "plantr_onboarding.html",
    "onboarding_form.html",
)
APP_VERSION = "0.16.1"

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
    email: Optional[str] = None
    password: Optional[str] = None
    invite_code: Optional[str] = None
    age_confirmed: bool = False


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


class DeclineIn(BaseModel):
    reason: Optional[str] = None


class MatchIn(BaseModel):
    product: Optional[Dict[str, Any]] = None
    product_id: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    all_onboarded: bool = False
    shop_id: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=50)
    optimise_for: Literal["balanced", "reach", "sales", "revenue"] = "balanced"
    blocked_creator_ids: List[str] = Field(default_factory=list)


class LoginIn(BaseModel):
    email: str
    password: str


class SetupIn(BaseModel):
    token: str = ""
    password: str = ""
    password_confirm: str = ""


class LinkIn(BaseModel):
    creator_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    destination_url: Optional[str] = None
    shortlist_item_id: Optional[str] = None
    platform: str = "plantr"


class ShopFlagsIn(BaseModel):
    is_pilot: bool
    is_underserved: bool
    underserved_reason: Optional[str] = None


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


def cors_policy(raw: Optional[str] = None) -> tuple[List[str], bool]:
    """Origins from CORS_ORIGINS. Credentials are never combined with *."""
    if raw is None:
        raw = os.environ.get("CORS_ORIGINS", "")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    explicit = [part for part in parts if part != "*"]
    if explicit:
        return explicit, True
    if "*" in parts:
        return ["*"], False
    return [], False


def cors_origins() -> List[str]:
    origins, _credentials = cors_policy()
    return origins


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
    """Rate-limit a protected call. Accept an API key or an admin session.

    Local processes with API_KEYS unset stay open so development can boot.
    Production and staging fail closed: no key and no session is 401.
    """
    keys = configured_api_keys()
    provided = request.headers.get("x-api-key", "")
    key_ok = bool(keys) and bool(provided) and _key_matches(provided, keys)
    session_email = auth.session_email(request)
    if key_ok or session_email:
        identity = f"session:{session_email}" if session_email else f"key:{provided}"
        enforce_rate_limit(identity)
        return
    enforce_rate_limit(f"ip:{client_ip(request)}")
    if keys or auth.locked_environment():
        raise HTTPException(status_code=401, detail="Authentication required")


def rate_limit_public(request: Request) -> None:
    enforce_rate_limit(f"ip:{client_ip(request)}")


def rate_limit_portal(request: Request) -> None:
    enforce_rate_limit(f"portal:{client_ip(request)}")


def require_admin(request: Request) -> str:
    if not _admin_available():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    email = auth.session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Admin session required")
    enforce_rate_limit(f"session:{email}")
    return email


def _api_key_status(request: Request) -> str:
    """Return valid, invalid, or absent. A header is ignored when API_KEYS is unset."""
    provided = request.headers.get("x-api-key", "")
    if not provided:
        return "absent"
    keys = configured_api_keys()
    if not keys:
        return "absent"
    if _key_matches(provided, keys):
        return "valid"
    return "invalid"


def require_login_operator(request: Request) -> str:
    """Admin session for the browser, or a valid API key for operator automation.

    The key path is only for creating shop and creator logins. It does not
    open the rest of the admin area.
    """
    status = _api_key_status(request)
    if status == "valid":
        enforce_rate_limit(f"key:{api_key_label(request.headers.get('x-api-key'))}")
        return "api-key"
    email = auth.session_email(request) if _admin_available() else None
    if status == "invalid" and not email:
        enforce_rate_limit(f"ip:{client_ip(request)}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not _admin_available():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    if not email:
        enforce_rate_limit(f"ip:{client_ip(request)}")
        raise HTTPException(status_code=401, detail="Admin session required")
    enforce_rate_limit(f"session:{email}")
    return email


def _admin_available() -> bool:
    return auth.admin_enabled(user_exists=admin_data.admin_exists(auth.admin_email()))


def _reject_setup_closed() -> None:
    if not auth.secrets_configured() or not auth.setup_token_configured():
        raise HTTPException(status_code=503, detail="Admin setup is disabled")
    if admin_data.admin_exists(auth.admin_email()):
        raise HTTPException(status_code=410, detail="Admin setup is no longer available")


def _session_cookie(response: JSONResponse, email: str) -> JSONResponse:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=auth.issue_session(email),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


def init_storage() -> None:
    db.init_storage()


def reset_runtime_state() -> None:
    """Clear process-local data and rate-limit buckets, then re-read DATABASE_URL."""
    db.reset_memory()
    auth.reset_login_limits()
    portal.reset_limits()
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
    record.pop("password", None)
    record.pop("email", None)
    record.pop("invite_code", None)
    record.pop("age_confirmed", None)
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


_cors_origins, _cors_credentials = cors_policy()
app = FastAPI(title="Plantr Collab API", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
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
    return RedirectResponse(url="/founding-curators", status_code=302)


@app.get("/onboarding_form.html")
def onboarding_form_page():
    return RedirectResponse(url="/founding-curators", status_code=302)


@app.get("/plantr_dashboard.html")
def plantr_dashboard_page():
    return RedirectResponse(url="/creator", status_code=302)


@app.get("/plantr_admin.html")
def plantr_admin_page():
    return RedirectResponse(url="/admin", status_code=302)


@app.get("/admin")
def admin_page():
    if not _admin_available():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    page = (ROOT / "admin.html").read_text(encoding="utf-8")
    token = html.escape(auth.issue_form_token("admin"), quote=True)
    return HTMLResponse(page.replace("__CSRF__", token))


@app.get("/admin/setup")
def admin_setup_page():
    _reject_setup_closed()
    page = (ROOT / "admin_setup.html").read_text(encoding="utf-8")
    email = html.escape(auth.admin_email(), quote=True)
    page = page.replace('value=""', f'value="{email}"', 1)
    return HTMLResponse(page)


@app.post("/admin/setup")
async def admin_setup(request: Request):
    _reject_setup_closed()
    if not auth.setup_attempt_allowed(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many setup attempts", headers={"Retry-After": "900"})
    payload = await _json_or_form(request)
    if not auth.token_matches(str(payload.get("token") or "")):
        raise HTTPException(status_code=401, detail="Invalid setup token")
    password = str(payload.get("password") or "")
    confirmation = str(payload.get("password_confirm") or "")
    if len(password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters")
    if not auth.passwords_match(password, confirmation):
        raise HTTPException(status_code=422, detail="Password confirmation does not match")
    if admin_data.admin_exists(auth.admin_email()):
        raise HTTPException(status_code=410, detail="Admin setup is no longer available")
    admin_data.create_admin(auth.admin_email(), auth.hash_password(password))
    return {"ok": True}


@app.post("/admin/login")
def admin_login(body: LoginIn, request: Request):
    if not auth.secrets_configured():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    user = admin_data.find_admin(auth.admin_email())
    password_hash = user.password_hash if user is not None else auth.fallback_password_hash()
    if not auth.admin_enabled(user_exists=user is not None):
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    if not auth.login_attempt_allowed(client_ip(request)):
        events.record_admin("admin_login_failed", email=_login_email(body.email), reason="rate_limited")
        raise HTTPException(status_code=429, detail="Too many login attempts", headers={"Retry-After": "900"})
    if not password_hash:
        auth.verify_password(body.password, "invalid")
        events.record_admin("admin_login_failed", email=_login_email(body.email), reason="invalid")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not auth.credentials_match(body.email, body.password, password_hash):
        events.record_admin("admin_login_failed", email=_login_email(body.email), reason="invalid")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user is not None:
        admin_data.mark_admin_login(user.email)
    events.record_admin("admin_login_succeeded", email=auth.admin_email())
    return _session_cookie(JSONResponse({"ok": True}), auth.admin_email())


@app.post("/admin/logout")
def admin_logout():
    if not auth.secrets_configured():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return response


@app.get("/admin/api/summary")
def admin_summary(_: str = Depends(require_admin)):
    return admin_data.summary()


@app.get("/admin/api/shops")
def admin_shops(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: str = Depends(require_admin)):
    return admin_data.list_shops(q, page, page_size)


@app.get("/admin/api/shops/{shop_id}")
def admin_shop(shop_id: str, _: str = Depends(require_admin)):
    row = admin_data.shop_detail(shop_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return row


@app.patch("/admin/api/shops/{shop_id}")
def admin_shop_flags(shop_id: str, body: ShopFlagsIn, _: str = Depends(require_admin)):
    row = admin_data.update_shop_flags(
        shop_id,
        is_pilot=body.is_pilot,
        is_underserved=body.is_underserved,
        underserved_reason=body.underserved_reason,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return row


@app.get("/admin/api/creators")
def admin_creators(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: str = Depends(require_admin)):
    return admin_data.list_creators(q, page, page_size)


@app.get("/admin/api/creators/{creator_id}")
def admin_creator(creator_id: str, _: str = Depends(require_admin)):
    row = admin_data.creator_detail(creator_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    return row


@app.get("/admin/api/products")
def admin_products(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: str = Depends(require_admin)):
    return admin_data.list_products(q, page, page_size)


@app.get("/admin/api/products/{product_id}")
def admin_product(product_id: str, _: str = Depends(require_admin)):
    row = admin_data.product_detail(product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return row


@app.get("/admin/api/shortlists")
def admin_shortlists(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: str = Depends(require_admin)):
    return admin_data.list_shortlists(q, page, page_size)


@app.get("/admin/api/shortlists/{shortlist_id}")
def admin_shortlist(shortlist_id: str, _: str = Depends(require_admin)):
    row = admin_data.shortlist_detail(shortlist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Shortlist not found")
    return row


@app.get("/admin/api/events")
def admin_events(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: str = Depends(require_admin)):
    return admin_data.list_events(q, page, page_size)


@app.get("/admin/api/events/{event_id}")
def admin_event(event_id: str, _: str = Depends(require_admin)):
    row = admin_data.event_detail(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return row


@app.post("/creator-product-links")
def create_creator_product_link(body: LinkIn, request: Request, _: str = Depends(require_login_operator)):
    row, created = attribution.issue_link(
        creator_id=body.creator_id,
        product_id=body.product_id,
        destination_url=body.destination_url,
        shortlist_item_id=body.shortlist_item_id,
        platform=body.platform,
    )
    row["tracking_url"] = billing_config.public_base_url(request) + row["tracking_path"]
    row["created"] = created
    return row


@app.get("/creator-product-links")
def get_creator_product_links(
    shop_id: Optional[str] = None,
    creator_id: Optional[str] = None,
    _: str = Depends(require_login_operator),
):
    return attribution.list_links(shop_id=shop_id, creator_id=creator_id)


@app.get("/r/{code}")
def redirect_tracking_code(code: str, _: None = Depends(rate_limit_public)):
    link = attribution.resolve_redirect(code)
    destination = attribution.safe_destination((link or {}).get("link_url"))
    if link is None or destination is None:
        raise HTTPException(status_code=404, detail="Tracking code not found")
    events.record_link_click(link)
    return RedirectResponse(url=destination, status_code=302)


@app.post("/attributed-orders/import")
async def import_attributed_orders(request: Request, dry_run: bool = False, _: str = Depends(require_admin)):
    csv_text, flag = await _csv_payload(request, dry_run)
    return attribution.import_orders(csv_text, dry_run=flag)


@app.get("/admin/api/links")
def admin_links(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    shop_id: Optional[str] = None,
    creator_id: Optional[str] = None,
    _: str = Depends(require_admin),
):
    return _paged(attribution.list_links(shop_id=shop_id, creator_id=creator_id), q, page, page_size)


@app.get("/admin/api/links/{link_id}")
def admin_link(link_id: str, _: str = Depends(require_admin)):
    row = attribution.link_detail(link_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return row


@app.get("/admin/api/orders")
def admin_orders(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    shop_id: Optional[str] = None,
    _: str = Depends(require_admin),
):
    page = _paged(attribution.list_orders(shop_id=shop_id), q, page, page_size)
    page["gmv"] = attribution.attributed_gmv(shop_id=shop_id)
    return page


@app.get("/admin/api/orders/{order_id}")
def admin_order(order_id: str, _: str = Depends(require_admin)):
    row = attribution.order_detail(order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


@app.post("/admin/api/shops/{shop_id}/login")
async def admin_create_seller_login(shop_id: str, request: Request, actor: str = Depends(require_login_operator)):
    fields = await _request_fields(request)
    if actor != "api-key":
        portal.check_login_csrf(str(fields.get("csrf_token") or ""), "admin")
    result = portal.create_login(
        role="seller",
        email=str(fields.get("email") or ""),
        password=str(fields.get("password") or ""),
        password_confirm=str(fields.get("password_confirm") or ""),
        shop_id=shop_id,
    )
    return _portal_result(request, result, "/admin")


@app.post("/admin/api/creators/{creator_id}/login")
async def admin_create_creator_login(creator_id: str, request: Request, actor: str = Depends(require_login_operator)):
    fields = await _request_fields(request)
    if actor != "api-key":
        portal.check_login_csrf(str(fields.get("csrf_token") or ""), "admin")
    result = portal.create_login(
        role="creator",
        email=str(fields.get("email") or ""),
        password=str(fields.get("password") or ""),
        password_confirm=str(fields.get("password_confirm") or ""),
        creator_id=creator_id,
    )
    return _portal_result(request, result, "/admin")


@app.post("/admin/api/shops/{shop_id}/view-as")
async def admin_view_shop(shop_id: str, request: Request, admin: str = Depends(require_admin)):
    fields = await _request_fields(request)
    portal.check_login_csrf(str(fields.get("csrf_token") or ""), "admin")
    if services.fetch_shop(shop_id) is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    response = _portal_result(request, {"ok": True, "redirect": "/seller"}, "/seller")
    portal.set_role_cookie(response, "seller", admin, shop_id, impersonator=admin)
    return response


@app.post("/admin/api/creators/{creator_id}/view-as")
async def admin_view_creator(creator_id: str, request: Request, admin: str = Depends(require_admin)):
    fields = await _request_fields(request)
    portal.check_login_csrf(str(fields.get("csrf_token") or ""), "admin")
    if services.fetch_creator(creator_id) is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    response = _portal_result(request, {"ok": True, "redirect": "/creator"}, "/creator")
    portal.set_role_cookie(response, "creator", admin, creator_id, impersonator=admin)
    return response


def _commerce_block(role: str, subject_id: str):
    if stripe_billing.commerce_open(role, subject_id):
        return None
    path = "/seller/billing" if role == "seller" else "/creator/billing"
    return RedirectResponse(f"{path}?subscribe=1", status_code=303)


@app.get("/seller")
@app.get("/seller/profile")
def seller_profile(request: Request, _: None = Depends(rate_limit_portal)):
    return portal.render_seller(portal.seller_context(request), "profile", base_url=billing_config.public_base_url(request))


@app.get("/seller/products")
def seller_products(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.seller_context(request)
    products = portal.seller_products(session["subject_id"]) if session else []
    return portal.render_seller(session, "products", base_url=billing_config.public_base_url(request), products=products)


@app.post("/seller/products")
async def seller_save_product(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_seller(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    blocked = _commerce_block("seller", session["subject_id"])
    if blocked is not None:
        return blocked
    portal.save_product(
        session["subject_id"],
        product_id=str(form.get("product_id") or ""),
        title=str(form.get("title") or ""),
        category=str(form.get("category") or ""),
        price=str(form.get("price") or ""),
        url=str(form.get("url") or ""),
        description=str(form.get("description") or ""),
        commission=str(form.get("commission") or ""),
    )
    return RedirectResponse("/seller/products", status_code=303)


@app.get("/seller/find")
def seller_find(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.seller_context(request)
    products = portal.seller_products(session["subject_id"]) if session else []
    return portal.render_seller(session, "find", base_url=billing_config.public_base_url(request), products=products, matches=[])


@app.post("/seller/find")
async def seller_run_match(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_seller(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    blocked = _commerce_block("seller", session["subject_id"])
    if blocked is not None:
        return blocked
    outcome = portal.run_match(session["subject_id"], str(form.get("product_id") or ""))
    products = portal.seller_products(session["subject_id"])
    return portal.render_seller(session, "find", base_url=billing_config.public_base_url(request), products=products, matches=outcome["matches"])


@app.post("/seller/shortlist-items/{item_id}/accept")
async def seller_accept(item_id: str, request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_seller(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    blocked = _commerce_block("seller", session["subject_id"])
    if blocked is not None:
        return blocked
    portal.decide(session["subject_id"], item_id, "match_accepted")
    return RedirectResponse("/seller/accepted", status_code=303)


@app.post("/seller/shortlist-items/{item_id}/decline")
async def seller_decline(item_id: str, request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_seller(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    portal.decide(session["subject_id"], item_id, "match_declined")
    return RedirectResponse("/seller/find", status_code=303)


@app.get("/seller/accepted")
def seller_accepted(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.seller_context(request)
    accepted = portal.accepted_for_shop(session["subject_id"]) if session else []
    return portal.render_seller(session, "accepted", base_url=billing_config.public_base_url(request), accepted=accepted)


@app.post("/seller/login")
async def seller_login(request: Request, _: None = Depends(rate_limit_portal)):
    form = await request.form()
    portal.check_login_csrf(str(form.get("csrf_token") or ""), "seller-login")
    account = portal.authenticate("seller", str(form.get("email") or ""), str(form.get("password") or ""), client_ip(request))
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    response = RedirectResponse("/seller/profile", status_code=303)
    portal.set_role_cookie(response, "seller", account.email, account.shop_id)
    return response


@app.post("/seller/logout")
async def seller_logout(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_seller(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    response = RedirectResponse("/seller", status_code=303)
    portal.clear_role_cookie(response, "seller")
    return response


@app.get("/creator")
@app.get("/creator/profile")
def creator_profile_page(request: Request, _: None = Depends(rate_limit_portal)):
    return portal.render_creator(portal.creator_context(request), "profile", base_url=billing_config.public_base_url(request))


@app.post("/creator/profile")
async def creator_save_profile(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_creator(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    active = [part.strip().lower() for part in str(form.get("active_categories") or "").split(",") if part.strip()]
    desired = [part.strip().lower() for part in str(form.get("desired_categories") or "").split(",") if part.strip()]
    portal.update_creator_categories(session["subject_id"], active, desired)
    return RedirectResponse("/creator/profile", status_code=303)


@app.get("/creator/offers")
def creator_offers_page(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.creator_context(request)
    offers = portal.creator_offers(session["subject_id"]) if session else []
    return portal.render_creator(session, "offers", base_url=billing_config.public_base_url(request), offers=offers)


@app.get("/seller/billing")
def seller_billing_page(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.seller_context(request)
    billing = stripe_billing.shop_status(session["subject_id"]) if session else {}
    return portal.render_seller(session, "billing", base_url=billing_config.public_base_url(request), billing=billing)


@app.get("/creator/payouts")
def creator_payouts_redirect():
    return RedirectResponse("/creator/billing", status_code=302)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, _: None = Depends(rate_limit_public)):
    payload = await request.body()
    try:
        event = stripe_billing.construct_event(payload, request.headers.get("stripe-signature", ""))
    except HTTPException:
        raise
    except Exception:
        logging.getLogger("plantr.webhook").exception("stripe webhook could not be read")
        return {"received": True}
    try:
        if stripe_billing.record_event(event):
            stripe_billing.apply_event(event)
        # Fan-out runs after the Stripe event is stored. A platform failure must not
        # turn this into a 500, or Stripe would retry and skip apply_event.
        import tracking

        tracking.flush_pending()
    except HTTPException:
        raise
    except Exception:
        logging.getLogger("plantr.webhook").exception("stripe webhook failed after the event was stored")
    return {"received": True}


@app.get("/creator/links")
def creator_links_page(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.creator_context(request)
    links = portal.creator_links(session["subject_id"]) if session else []
    return portal.render_creator(session, "links", base_url=billing_config.public_base_url(request), links=links)


@app.post("/creator/login")
async def creator_login(request: Request, _: None = Depends(rate_limit_portal)):
    form = await request.form()
    portal.check_login_csrf(str(form.get("csrf_token") or ""), "creator-login")
    account = portal.authenticate("creator", str(form.get("email") or ""), str(form.get("password") or ""), client_ip(request))
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    response = RedirectResponse("/creator/profile", status_code=303)
    portal.set_role_cookie(response, "creator", account.email, account.creator_id)
    return response


@app.post("/creator/logout")
async def creator_logout(request: Request, _: None = Depends(rate_limit_portal)):
    session = portal.require_creator(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    response = RedirectResponse("/creator", status_code=303)
    portal.clear_role_cookie(response, "creator")
    return response


async def _request_fields(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    form = await request.form()
    return {key: form.get(key) for key in form.keys()}


def _portal_result(request: Request, payload: dict, redirect_to: str):
    if "application/json" in request.headers.get("content-type", ""):
        return JSONResponse(payload)
    return RedirectResponse(redirect_to, status_code=303)


def _paged(rows: list, q: str, page: int, page_size: int) -> dict:
    needle = q.strip().lower()
    if needle:
        rows = [row for row in rows if needle in json.dumps(row, default=str).lower()]
    start = (page - 1) * page_size
    return {"items": rows[start : start + page_size], "page": page, "page_size": page_size, "total": len(rows)}


async def _csv_payload(request: Request, dry_run: bool) -> tuple[str, bool]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        return str(body.get("csv") or ""), _truthy(body.get("dry_run", dry_run))
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        upload = form.get("file")
        if hasattr(upload, "read"):
            text = (await upload.read()).decode("utf-8-sig")
        else:
            text = str(form.get("csv") or "")
        return text, _truthy(form.get("dry_run", dry_run))
    return (await request.body()).decode("utf-8-sig"), dry_run


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def _json_or_form(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    form = await request.form()
    return {key: form.get(key) for key in ("token", "password", "password_confirm")}


def _login_email(email: str) -> str:
    return email.strip().lower()[:255]


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


@app.post("/shortlist-items/{item_id}/accept")
def accept_shortlist_item(item_id: str, _: None = Depends(guard_request)):
    return services.decide_shortlist_item(item_id, "match_accepted")


@app.post("/shortlist-items/{item_id}/decline")
def decline_shortlist_item(item_id: str, body: Optional[DeclineIn] = None, _: None = Depends(guard_request)):
    return services.decide_shortlist_item(item_id, "match_declined", reason=None if body is None else body.reason)


@app.post("/match")
def match(body: MatchIn, _: None = Depends(guard_request)):
    if not body.product and not body.product_id:
        raise HTTPException(status_code=422, detail="Provide a product or a product_id")
    if not body.creators and not body.all_onboarded:
        raise HTTPException(status_code=422, detail="Provide creators or set all_onboarded")
    loaded = services.load_match_inputs(
        product=body.product,
        product_id=body.product_id,
        creators=body.creators,
        all_onboarded=body.all_onboarded,
        shop_id=body.shop_id,
    )
    outcome = scoring.rank_creators(
        loaded["product"],
        loaded["creators"],
        shop=loaded["shop"],
        optimise_for=body.optimise_for,
        blocked_creator_ids=body.blocked_creator_ids,
        limit=body.limit,
    )
    shortlist_id, item_ids = services.persist_rules_match(
        shop_id=loaded["shop_id"],
        product_id=loaded["product_id"],
        optimise_for=body.optimise_for,
        excluded=outcome["excluded"],
        matches=[
            {
                "creator_id": (row["creator"].get("influencer_id") or row["creator"].get("id")),
                "score": row["score"],
                "components": row["components"],
                "estimates": row["estimates"],
                "reasons": row["reasons"],
            }
            for row in outcome["matches"]
        ],
    )
    for row, item_id in zip(outcome["matches"], item_ids):
        row["shortlist_item_id"] = item_id
    if shortlist_id:
        outcome["shortlist_id"] = shortlist_id
    return outcome


@app.post("/onboard-influencer")
def onboard_influencer(influencer: InfluencerIn, _: None = Depends(rate_limit_public)):
    if influencer.email or influencer.password:
        if not influencer.email or not influencer.password or len(influencer.password) < portal.MIN_PASSWORD:
            raise HTTPException(
                status_code=422,
                detail="Creator login needs an email and a password of at least 12 characters",
            )
    influencer_id = str(uuid.uuid4())
    record = public_profile(influencer, influencer_id)
    services.persist_creator(record)
    if influencer.email and influencer.password:
        if not influencer.age_confirmed:
            raise HTTPException(status_code=422, detail="Creators must confirm they are 18 or older")
        if not influencer.invite_code or not _invite_ok(influencer.invite_code):
            raise HTTPException(status_code=422, detail="Creator membership is invite-only")
        portal.create_login(
            role="creator",
            email=influencer.email,
            password=influencer.password,
            password_confirm=influencer.password,
            creator_id=influencer_id,
        )
    return {"id": influencer_id, "influencer": record}


def _invite_ok(code: str) -> bool:
    import pilots

    try:
        pilots.open_invite(code)
    except HTTPException:
        return False
    return True


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


import web_routes
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

web_routes.register(app)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
