"""Public pilot pages, billing checkout, and tracking collection."""

from __future__ import annotations

import html
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import auth
import billing_config
import pilots
import portal
import services
import site_copy
import stripe_billing
import touch
import tracking


def _draft_reader(request: Request) -> bool:
    import db
    import uat_mode

    if auth.session_email(request):
        return True
    if not uat_mode.enabled() or not portal.portal_enabled():
        return False
    ids = getattr(db, "memory_uat_ids", {}) or {}
    seller = portal.seller_context(request)
    creator = portal.creator_context(request)
    if seller and seller.get("subject_id") == ids.get("shop_id"):
        return True
    if creator and creator.get("subject_id") == ids.get("creator_id"):
        return True
    return False


def register(app) -> None:
    app.add_middleware(TouchMiddleware)

    @app.get("/founding-curators")
    def founding_curators(request: Request):
        return _landing(request, "creator")

    @app.get("/pilot-sellers")
    def pilot_sellers(request: Request):
        return _landing(request, "seller")

    @app.post("/founding-curators/apply")
    async def founding_apply(request: Request):
        return await _apply(request, "founding_curators")

    @app.post("/pilot-sellers/apply")
    async def seller_apply(request: Request):
        return await _apply(request, "seller_pilot")

    @app.get("/join/{code}")
    def join_page(code: str, request: Request):
        import legal

        opened = pilots.open_invite(code)
        if not billing_config.operator_legal_name():
            return HTMLResponse(_shell(f"<h1>You're invited.</h1><p>{legal.OPENING_SOON}</p>"))
        token = auth.issue_form_token("join")
        dob = "<label>Date of birth <input name='date_of_birth' type='date' required autocomplete='bday'></label>"
        if opened["role"] == "creator":
            age = dob + legal.checkbox("age_confirmed", legal.CREATOR_AGE, required=True)
            boxes = legal.checkbox("terms_accepted", legal.CREATOR_JOIN_TERMS, required=True) + legal.checkbox(
                "pilot_terms_accepted", legal.CREATOR_JOIN_PILOT, required=True
            )
            panel = legal.CREATOR_PANEL
        else:
            age = dob + legal.checkbox("age_confirmed", legal.SELLER_AUTHORITY, required=True)
            boxes = legal.checkbox("terms_accepted", legal.SELLER_JOIN_TERMS, required=True) + legal.checkbox(
                "pilot_terms_accepted", legal.SELLER_JOIN_PILOT, required=True
            )
            panel = legal.SELLER_PANEL
        boxes += legal.checkbox("email_consent", legal.EMAIL_CONSENT, required=False)
        operator = billing_config.operator_legal_name()
        number = ""
        if opened.get("founding_number"):
            number = f"<p>Foundr #{opened['founding_number']}</p>"
        body = f"""
        <h1>You've been selected.</h1>
        {number}
        <p>This invite is for a {opened['role']}. It is not an open sign-up.</p>
        <form method="post" action="/join/{code}">
          <input type="hidden" name="csrf_token" value="{token}">
          <label>Name <input name="name" required></label>
          <label>Email <input name="email" type="email" required></label>
          <label>Password <input name="password" type="password" minlength="12" required></label>
          <label>Categories <input name="categories"></label>
          {age}
          <p>{panel}</p>
          {boxes}
          <button type="submit">Continue</button>
        </form>
        <p>Plantr Collab is operated by {operator}. <a href="/privacy">Privacy</a></p>
        <p>Draft documents, pending legal review:
          <a href="/terms/platform">Terms</a>,
          <a href="/terms/creatr-foundr">Creatr and Foundr terms</a>,
          <a href="/terms/seller">Seller terms</a>,
          <a href="/terms/fees">Fees and plans</a>,
          <a href="/privacy">Privacy</a>,
          <a href="/terms/acceptable-use">Acceptable use</a>,
          <a href="/terms/integration-data">Integration data</a>.
        </p>
        """
        return HTMLResponse(_shell(body))

    @app.post("/join/{code}")
    async def join_submit(code: str, request: Request):
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "join"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        if not billing_config.operator_legal_name():
            import legal

            raise HTTPException(status_code=503, detail=legal.OPENING_SOON)
        import creatr_age

        born, date_error = creatr_age.parse_born(str(form.get("date_of_birth") or ""))
        if date_error:
            raise HTTPException(status_code=422, detail=date_error)
        result = pilots.join_with_invite(
            code,
            name=str(form.get("name") or ""),
            email=str(form.get("email") or ""),
            password=str(form.get("password") or ""),
            categories=str(form.get("categories") or ""),
            age_confirmed=str(form.get("age_confirmed") or "") == "yes",
            date_of_birth=born,
            touch_state=request.scope.get("plantr_touch"),
            consent=tracking.consent_from_scope(request.scope),
            terms_accepted=str(form.get("terms_accepted") or "") == "yes",
            pilot_terms_accepted=str(form.get("pilot_terms_accepted") or "") == "yes",
            email_consent=str(form.get("email_consent") or "") == "yes",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        target = "/seller" if result["role"] == "seller" else "/creator"
        return RedirectResponse(target, status_code=303)

    @app.get("/privacy")
    def privacy_page(request: Request):
        import legal

        privileged = _draft_reader(request)
        if not privileged:
            return HTMLResponse(legal.legal_page("privacy"))
        import site_copy as copy

        body = f"<p class='banner'>DRAFT — NEEDS LEGAL REVIEW (NOT BINDING)</p><h1>Privacy</h1><p>{html.escape(copy.PRIVACY['body'])}</p><p>[PRIVACY POLICY PENDING LEGAL REVIEW]</p>"
        return HTMLResponse(__import__("brand_ui").document("Privacy", body))

    @app.get("/terms/{slug}")
    def terms_page(slug: str, request: Request):
        import legal

        if slug not in legal.LEGAL_PAGES or slug == "privacy":
            raise HTTPException(status_code=404, detail="That page is not available")
        return HTMLResponse(legal.legal_page(slug, privileged=_draft_reader(request)))

    @app.get("/robots.txt")
    def robots_txt():
        import tracking
        from fastapi.responses import PlainTextResponse

        if not tracking.staging():
            raise HTTPException(status_code=404, detail="Not available")
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.post("/track/consent")
    async def update_consent(request: Request):
        body = await request.json()
        allowed = bool(body.get("consent"))
        updated = _set_signed_in_consent(request, allowed)
        if not allowed and updated:
            tracking.track(
                "consent_revoked",
                event_id=f"consent-revoke:{updated}:{int(__import__('time').time())}",
                role=updated.split(":", 1)[0],
                entity_type=updated.split(":", 1)[0],
                entity_id=updated.split(":", 1)[1],
                shop_id=updated.split(":", 1)[1] if updated.startswith("seller:") else None,
                actor="api",
                consent=False,
            )
        response = JSONResponse({"marketing_consent": allowed, "account": updated})
        response.set_cookie(
            "plantr_consent",
            "marketing" if allowed else "denied",
            max_age=90 * 24 * 60 * 60,
            httponly=False,
            samesite="lax",
            path="/",
        )
        if allowed:
            # The attribution cookie is written only now, with the UTMs from the page the visitor accepted on.
            query = str(body.get("query") or "")[:2000].encode()
            observed = touch.observe({**request.scope, "query_string": query})
            cookie = touch.consented_cookie(observed, secure=request.url.scheme == "https")
            if cookie:
                response.headers.append("set-cookie", cookie)
        else:
            response.delete_cookie(touch.COOKIE, path="/")
        return response

    @app.post("/track/apply-start")
    async def apply_start(request: Request):
        body = await request.json()
        role = str(body.get("role") or "")
        event_id = str(body.get("event_id") or uuid.uuid4())
        tracking.track(
            "apply_start",
            event_id=f"start:{event_id}",
            role=role,
            actor="api",
            consent=tracking.consent_from_scope(request.scope),
            touch=request.scope.get("plantr_touch"),
        )
        return {"ok": True}

    @app.get("/seller/campaigns")
    def seller_campaigns(request: Request):
        session = portal.seller_context(request)
        rows = []
        if session:
            import db

            rows = [
                {"name": row.name}
                for row in db.memory_campaigns
                if row.shop_id == session["subject_id"]
            ]
            if db.configured():
                from sqlalchemy import select

                from models import Campaign

                with db.session_scope() as db_session:
                    stored = db_session.scalars(select(Campaign).where(Campaign.shop_id == session["subject_id"])).all()
                    rows = [{"name": row.name} for row in stored]
        return portal.render_seller(session, "campaigns", campaigns=rows)

    @app.post("/seller/campaigns")
    async def seller_create_campaign(request: Request):
        session = portal.require_seller(request)
        form = await request.form()
        portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
        if not stripe_billing.commerce_open("seller", session["subject_id"]):
            return RedirectResponse("/seller/billing?subscribe=1", status_code=303)
        services.create_campaign(session["subject_id"], str(form.get("name") or ""))
        return RedirectResponse("/seller/campaigns", status_code=303)

    @app.post("/seller/billing/checkout")
    async def seller_checkout(request: Request):
        return await _checkout(request, "seller")

    @app.post("/creator/billing/checkout")
    async def creator_checkout(request: Request):
        return await _checkout(request, "creator")

    @app.post("/seller/billing/portal")
    async def seller_portal(request: Request):
        return await _portal(request, "seller")

    @app.post("/creator/billing/portal")
    async def creator_portal(request: Request):
        return await _portal(request, "creator")

    @app.get("/creator/billing")
    def creator_billing(request: Request):
        session = portal.creator_context(request)
        billing = stripe_billing.creator_status(session["subject_id"]) if session else {}
        return portal.render_creator(session, "billing", billing=billing)

    @app.post("/creator/offers/{item_id}/accept")
    async def creator_accept(item_id: str, request: Request):
        session = portal.require_creator(request)
        form = await request.form()
        portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
        if not stripe_billing.commerce_open("creator", session["subject_id"]):
            return RedirectResponse("/creator/billing?subscribe=1", status_code=303)
        if not _offer_for_creator(item_id, session["subject_id"]):
            raise HTTPException(status_code=404, detail="Offer not found")
        services.decide_shortlist_item(item_id, "match_accepted")
        return RedirectResponse("/creator/offers", status_code=303)

    @app.get("/admin/pilots")
    def admin_pilots(request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        board = pilots.dashboard()
        return HTMLResponse(_shell(f"<h1>Pilots</h1><pre>{_dump(board)}</pre><p><a href='/admin/pilots.csv'>CSV</a></p>"))

    @app.get("/admin/pilots.csv")
    def admin_pilots_csv(request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        return Response(pilots.funnel_csv(), media_type="text/csv")

    @app.post("/admin/api/applications/{application_id}/approve")
    def approve_application(application_id: str, request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        return pilots.approve(application_id)

    @app.post("/admin/api/applications/{application_id}/reject")
    def reject_application(application_id: str, request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        return pilots.reject(application_id)

    @app.post("/admin/api/creators/{creator_id}/sale-waiver")
    def sale_waiver(creator_id: str, request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        return stripe_billing.apply_sale_waiver(creator_id)

    @app.post("/admin/api/reminders/run")
    def run_reminders(request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        import reminders

        return reminders.run()

    @app.get("/admin/api/reminders")
    def list_reminders(request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        import reminders

        return {"reminders": reminders.preview()}

    @app.post("/admin/api/creators/{creator_id}/grant-elite")
    def grant_elite(creator_id: str, request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        return stripe_billing.grant_elite(creator_id)

    @app.post("/admin/api/orders/{order_id}/adjustment")
    async def adjust_order(order_id: str, request: Request):
        if not auth.session_email(request):
            raise HTTPException(status_code=401, detail="Admin session required")
        import attribution

        body = await request.json()
        return attribution.adjust_order(
            order_id,
            refunded_amount=body.get("refunded_amount"),
            cancelled_amount=body.get("cancelled_amount"),
        )


class TouchMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        observed = touch.observe(scope)
        scope["plantr_touch"] = observed
        # No attribution cookie until the visitor allows marketing cookies.
        cookie = observed.get("cookie") if tracking.consent_from_scope(scope) else None

        async def send_wrapper(message):
            if cookie and message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"set-cookie", cookie.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            tracking.flush_pending()


def _landing(request: Request, kind: str):
    spec = site_copy.page(kind)
    pilots.ensure_cohorts()
    pilots.record_visit(spec["path"], request.scope.get("plantr_touch"), spec["cohort"])
    event_id = str(uuid.uuid4())
    consent = tracking.consent_from_scope(request.scope)
    tracking.track(
        "page_view",
        event_id=f"pv:{event_id}",
        role=spec["role"],
        actor="system",
        consent=consent,
        touch=request.scope.get("plantr_touch"),
        path=spec["path"],
    )
    content_name = spec["path"].strip("/")
    tracking.track(
        "view_content",
        event_id=f"vc:{event_id}",
        role=spec["role"],
        actor="system",
        consent=consent,
        touch=request.scope.get("plantr_touch"),
        content_name=content_name,
    )
    html = site_copy.landing_html(kind, token=auth.issue_form_token("apply"), event_id=event_id)
    pixels = tracking.pixel_snippet(
        consent,
        [
            {"key": "page_view", "event_id": f"pv:{event_id}"},
            {"key": "view_content", "event_id": f"vc:{event_id}", "params": {"content_name": content_name, "role": spec["role"]}},
        ],
    )
    html = html.replace("</main>", site_copy.consent_banner() + pixels + "</main>")
    return HTMLResponse(html)


async def _apply(request: Request, slug: str):
    form = await request.form()
    if not auth.form_token_ok(str(form.get("csrf_token") or ""), "apply"):
        raise HTTPException(status_code=403, detail="CSRF check failed")
    kind = "creator" if slug == "founding_curators" else "seller"
    if not billing_config.operator_legal_name():
        import legal

        return HTMLResponse(site_copy.landing_html(kind, thanks=legal.OPENING_SOON), status_code=503)
    import creatr_age

    born, date_error = creatr_age.parse_born(str(form.get("date_of_birth") or ""))
    if date_error:
        raise HTTPException(status_code=422, detail=date_error)
    age_yes = str(form.get("age_confirmed") or "") == "yes"
    result = pilots.apply(
        slug,
        name=str(form.get("name") or ""),
        email=str(form.get("email") or ""),
        role=str(form.get("role") or ""),
        categories=str(form.get("categories") or ""),
        contact=str(form.get("contact") or ""),
        age_confirmed=age_yes,
        date_of_birth=born,
        touch_state=request.scope.get("plantr_touch"),
        consent=tracking.consent_from_scope(request.scope),
        event_id=str(form.get("event_id") or ""),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        paid_service_confirmed=str(form.get("paid_service_confirmed") or "") == "yes",
        authority_confirmed=str(form.get("authority_confirmed") or "") == "yes" or (kind == "seller" and age_yes),
        email_consent=str(form.get("email_consent") or "") == "yes",
        business_name=str(form.get("business_name") or ""),
        abn=str(form.get("abn") or ""),
    )
    message = "We have your application. It is reviewed by our team. This is not an open sign-up."
    if result["status"] == "waitlisted":
        message = "This cohort is full. You are on the waitlist."
    tracked_id = result.get("event_id") or ""
    html = site_copy.landing_html(kind, thanks=message, event_id=tracked_id)
    spec = site_copy.page(kind)
    pixels = ""
    if tracked_id and not result.get("duplicate"):
        pixels = tracking.pixel_snippet(
            tracking.consent_from_scope(request.scope),
            [{"key": "apply_submit", "event_id": tracked_id, "params": {"role": spec["role"]}}],
        )
    html = html.replace("</main>", pixels + "</main>")
    return HTMLResponse(html)


async def _checkout(request: Request, role: str):
    session = portal.require_seller(request) if role == "seller" else portal.require_creator(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    path = "/seller/billing" if role == "seller" else "/creator/billing"
    context = stripe_billing.account_context(role, session["subject_id"])
    base = billing_config.public_base_url(request)
    tier = str(form.get("tier") or "")
    interval = str(form.get("interval") or "month")
    if str(form.get("authorise_recurring") or "") != "yes":
        raise HTTPException(status_code=422, detail="Please tick the box to authorise recurring payments")
    trial_end = stripe_billing.pilot_trial_end(role, session["subject_id"])
    try:
        url = stripe_billing.checkout(
            role=role,
            subject_id=session["subject_id"],
            tier=tier,
            interval=interval,
            success_url=f"{base}{path}",
            cancel_url=f"{base}{path}",
            promo_code=str(form.get("promo_code") or ""),
            invite_code=context["signup_code"],
            campaign=context["signup_campaign"],
            trial_end=trial_end,
        )
        stripe_billing.record_checkout_acceptance(
            role,
            session["subject_id"],
            tier=tier,
            interval=interval,
            trial_end=trial_end,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        try:
            url = stripe_billing.portal_session(role=role, subject_id=session["subject_id"], return_url=f"{base}{path}")
        except HTTPException:
            raise exc
    return RedirectResponse(url, status_code=303)


async def _portal(request: Request, role: str):
    session = portal.require_seller(request) if role == "seller" else portal.require_creator(request)
    form = await request.form()
    portal.check_session_csrf(session, str(form.get("csrf_token") or ""))
    base = billing_config.public_base_url(request)
    path = "/seller/billing" if role == "seller" else "/creator/billing"
    url = stripe_billing.portal_session(role=role, subject_id=session["subject_id"], return_url=f"{base}{path}")
    return RedirectResponse(url, status_code=303)


def _set_signed_in_consent(request: Request, allowed: bool) -> str:
    seller = portal.seller_context(request)
    if seller:
        stripe_billing.set_marketing_consent("seller", seller["subject_id"], allowed)
        return f"seller:{seller['subject_id']}"
    creator = portal.creator_context(request)
    if creator:
        stripe_billing.set_marketing_consent("creator", creator["subject_id"], allowed)
        return f"creator:{creator['subject_id']}"
    return ""


def _offer_for_creator(item_id: str, creator_id: str) -> bool:
    import db
    from models import ShortlistItem

    if db.configured():
        with db.session_scope() as session:
            item = session.get(ShortlistItem, item_id)
            return item is not None and item.creator_id == creator_id
    item = next((row for row in db.memory_shortlist_items if row.id == item_id), None)
    return item is not None and item.creator_id == creator_id


def _shell(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:24px auto;padding:0 16px}</style>"
        f"</head><body>{body}</body></html>"
    )


def _dump(value) -> str:
    import html
    import json

    return html.escape(json.dumps(value, default=str, indent=2))
