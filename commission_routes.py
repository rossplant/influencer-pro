"""HTTP routes for the Shopify order sync and light commission tracking (JSON).

Seller and creator routes use the portal session cookie and the session CSRF token in
the X-CSRF-Token header for writes. Admin routes use the admin session. Shopify
webhooks are verified by HMAC. Everything here needs the database.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

import attribution
import billing_config
import commission
import portal
import shopify_sync
import spark_ads


class StoreWebhooksIn(BaseModel):
    shop_domain: str
    signing_secret: str


class StoreCodeIn(BaseModel):
    code: Optional[str] = None


class PlatformCodeIn(BaseModel):
    code: str
    source_platform: str


class SparkAdIn(BaseModel):
    post_url: str
    spark_code: str
    authorised_until: str
    usage_note: Optional[str] = None
    consent: bool = False


class MarkPaidIn(BaseModel):
    paid_on: str
    reference: Optional[str] = None


class DisputeIn(BaseModel):
    reason: str
    amount: Any = None


class ResolveIn(BaseModel):
    reason: str
    outcome: str = "clear_block"


class ReasonIn(BaseModel):
    reason: str


class GenerateIn(BaseModel):
    period: str
    shop_id: Optional[str] = None


class ResponseIn(BaseModel):
    text: Optional[str] = None


class AbnIn(BaseModel):
    abn: Optional[str] = None


class AbnAckIn(BaseModel):
    creator_id: str
    context: str
    reference_id: Optional[str] = None


class OfferIn(BaseModel):
    creator_id: str
    product_id: Optional[str] = None
    shortlist_item_id: Optional[str] = None
    commission_rate: float = Field(ge=0, le=100)
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=60)
    paid_via: str = "plantr_tracked"
    abn_warning_acknowledged: bool = False


class CorrectionIn(BaseModel):
    reason: str
    proposed_paid_on: str
    evidence_text: Optional[str] = None
    evidence_url: Optional[str] = None


class CorrectionAnswerIn(BaseModel):
    reason: Optional[str] = None


class CorrectionDecisionIn(BaseModel):
    outcome: str
    reason: str


class AcceptIn(BaseModel):
    terms_version: str
    terms_sha256: str


def register(app) -> None:
    import main

    def _seller(request: Request, write: bool = False) -> dict:
        session = portal.require_seller(request)
        if write:
            portal.check_session_csrf(session, request.headers.get("x-csrf-token", ""))
        return session

    def _creator(request: Request, write: bool = False) -> dict:
        session = portal.require_creator(request)
        if write:
            portal.check_session_csrf(session, request.headers.get("x-csrf-token", ""))
        return session

    portal_limit = Depends(main.rate_limit_portal)
    public_limit = Depends(main.rate_limit_public)
    admin = Depends(main.require_admin)

    # ------------------------------------------------------------ Shopify: seller
    @app.get("/seller/api/shopify")
    def seller_shopify_status(request: Request, _: None = portal_limit):
        session = _seller(request)
        status = shopify_sync.connection_status(session["subject_id"])
        if status.get("webhook_path"):
            status["webhook_url"] = billing_config.public_base_url(request) + status["webhook_path"]
        return status

    @app.post("/seller/api/shopify/store-webhooks")
    def seller_shopify_store_webhooks(body: StoreWebhooksIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return shopify_sync.connect_store_webhooks(
            session["subject_id"], body.shop_domain, body.signing_secret, actor=session["email"], base_url=billing_config.public_base_url(request)
        )

    @app.get("/seller/shopify/install")
    def seller_shopify_install(request: Request, shop: str = ""):
        session = portal.seller_context(request)
        if session is None:
            return portal.login_page("seller")
        if not (shop or "").strip():
            import brand_ui

            body = (
                "<h1>Connect Shopify</h1>"
                "<p>Add the Shopify domain in Settings before connecting the store. "
                "Use a domain such as the-store.myshopify.com.</p>"
                "<p><a href=\"/seller/profile\">Open Settings</a></p>"
            )
            return HTMLResponse(brand_ui.document("Connect Shopify", body))
        redirect_uri = billing_config.public_base_url(request) + "/shopify/oauth/callback"
        return RedirectResponse(shopify_sync.begin_oauth(session["subject_id"], shop, redirect_uri=redirect_uri), status_code=303)

    @app.post("/seller/api/shopify/disconnect")
    def seller_shopify_disconnect(request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return shopify_sync.disconnect(session["subject_id"], actor=session["email"])

    @app.put("/seller/api/links/{link_id}/store-code")
    def seller_store_code(link_id: str, body: StoreCodeIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return shopify_sync.set_store_code(session["subject_id"], link_id, body.code, actor=session["email"])

    @app.post("/seller/api/links/{link_id}/platform-codes")
    def seller_platform_code(link_id: str, body: PlatformCodeIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return attribution.register_platform_code(
            session["subject_id"], link_id, body.code, body.source_platform, actor=session["email"]
        )

    @app.delete("/seller/api/links/{link_id}/platform-codes/{code_id}")
    def seller_delete_platform_code(link_id: str, code_id: str, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return attribution.delete_platform_code(session["subject_id"], link_id, code_id)

    # ------------------------------------------------------------ Shopify: public
    @app.get("/shopify/oauth/callback")
    def shopify_oauth_callback(request: Request, _: None = public_limit):
        shopify_sync.complete_oauth(dict(request.query_params))
        return RedirectResponse("/seller/profile?shopify=connected", status_code=303)

    @app.post("/shopify/webhooks")
    async def shopify_app_webhook(request: Request):
        status, payload = shopify_sync.handle_webhook(await request.body(), dict(request.headers))
        return JSONResponse(payload, status_code=status)

    @app.post("/shopify/webhooks/store/{path_token}")
    async def shopify_store_webhook(path_token: str, request: Request):
        status, payload = shopify_sync.handle_webhook(await request.body(), dict(request.headers), path_token=path_token)
        return JSONResponse(payload, status_code=status)

    # ------------------------------------------------------------ Shopify: admin
    @app.get("/admin/api/shopify/connections")
    def admin_shopify_connections(_: str = admin):
        return shopify_sync.list_connections()

    @app.post("/admin/api/shopify/{shop_id}/backfill")
    def admin_shopify_backfill(shop_id: str, days: Optional[int] = None, actor: str = admin):
        return shopify_sync.backfill(shop_id, days=days, actor=actor)

    # ------------------------------------------------------------ commission: seller
    @app.get("/seller/api/commission/statements")
    def seller_statements(request: Request, _: None = portal_limit):
        session = _seller(request)
        return {"label": commission.ESTIMATE_LABEL, "statements": commission.list_statements(shop_id=session["subject_id"], viewer="seller")}

    @app.get("/seller/api/commission/statements/{statement_id}")
    def seller_statement(statement_id: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.statement_detail(statement_id, viewer="seller", shop_id=session["subject_id"])

    @app.get("/seller/api/commission/statements/{statement_id}/csv")
    def seller_statement_csv(statement_id: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        body = commission.statement_csv(statement_id, shop_id=session["subject_id"])
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="statement-{statement_id}.csv"'},
        )

    @app.get("/seller/api/commission/source-report")
    def seller_source_report(period: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.source_report(session["subject_id"], period)

    @app.post("/seller/api/commission/statements/{statement_id}/mark-paid")
    def seller_mark_paid(statement_id: str, body: MarkPaidIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return commission.mark_paid(session["subject_id"], statement_id, paid_on=body.paid_on, reference=body.reference, actor=session["email"])

    @app.post("/seller/api/commission/statements/{statement_id}/corrections")
    def seller_request_correction(statement_id: str, body: CorrectionIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return commission.request_correction(
            session["subject_id"], statement_id, reason=body.reason, proposed_paid_on=body.proposed_paid_on,
            evidence_text=body.evidence_text, evidence_url=body.evidence_url, actor=session["email"],
        )

    @app.get("/seller/api/commission/corrections")
    def seller_corrections(request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.list_corrections(shop_id=session["subject_id"])

    @app.get("/seller/api/commission/block")
    def seller_block(request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.collab_block(session["subject_id"])

    @app.post("/seller/api/commission/abn-ack")
    def seller_abn_ack(body: AbnAckIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return commission.acknowledge_abn_warning(
            session["subject_id"], body.creator_id, context=body.context, reference_id=body.reference_id,
            actor=session["email"], ip=main.client_ip(request),
        )

    @app.get("/seller/api/pays-on-time")
    def seller_pays_on_time(request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.pays_on_time(session["subject_id"])

    @app.put("/seller/api/pays-on-time/response")
    def seller_pays_on_time_response(body: ResponseIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return commission.set_response(session["subject_id"], body.text, actor=session["email"])

    @app.get("/seller/api/collabs")
    def seller_collabs(request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.list_agreements(shop_id=session["subject_id"], viewer="seller")

    @app.get("/seller/api/collabs/{agreement_id}")
    def seller_collab(agreement_id: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        return commission.agreement_detail(agreement_id, viewer="seller", shop_id=session["subject_id"])

    @app.get("/seller/api/collabs/{collab_id}/spark-ads")
    def seller_spark_ads(collab_id: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        return spark_ads.list_codes(collab_id=collab_id, viewer="seller", shop_id=session["subject_id"])

    @app.get("/seller/api/collabs/{collab_id}/spark-ads/{spark_id}")
    def seller_spark_ad(collab_id: str, spark_id: str, request: Request, _: None = portal_limit):
        session = _seller(request)
        return spark_ads.get_code(collab_id=collab_id, spark_id=spark_id, viewer="seller", shop_id=session["subject_id"])

    @app.post("/seller/api/collabs")
    def seller_offer(body: OfferIn, request: Request, _: None = portal_limit):
        session = _seller(request, write=True)
        return commission.create_offer(
            session["subject_id"], actor=session["email"], ip=main.client_ip(request), creator_id=body.creator_id,
            product_id=body.product_id, shortlist_item_id=body.shortlist_item_id, commission_rate=body.commission_rate,
            payment_terms_days=body.payment_terms_days, paid_via=body.paid_via, abn_warning_acknowledged=body.abn_warning_acknowledged,
        )

    # ------------------------------------------------------------ commission: creator
    @app.get("/creator/api/commission/statements")
    def creator_statements(request: Request, _: None = portal_limit):
        session = _creator(request)
        return {"label": commission.ESTIMATE_LABEL, "statements": commission.list_statements(creator_id=session["subject_id"], viewer="creator")}

    @app.get("/creator/api/commission/statements/{statement_id}")
    def creator_statement(statement_id: str, request: Request, _: None = portal_limit):
        session = _creator(request)
        return commission.statement_detail(statement_id, viewer="creator", creator_id=session["subject_id"])

    @app.post("/creator/api/commission/statements/{statement_id}/confirm")
    def creator_confirm(statement_id: str, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.confirm(session["subject_id"], statement_id, actor=session["email"])

    @app.post("/creator/api/commission/statements/{statement_id}/dispute")
    def creator_dispute(statement_id: str, body: DisputeIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.dispute(session["subject_id"], statement_id, reason=body.reason, amount=body.amount, actor=session["email"])

    @app.post("/creator/api/commission/statements/{statement_id}/withdraw-dispute")
    def creator_withdraw(statement_id: str, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.withdraw_dispute(session["subject_id"], statement_id, actor=session["email"])

    @app.get("/creator/api/commission/corrections")
    def creator_corrections(request: Request, _: None = portal_limit):
        session = _creator(request)
        return commission.list_corrections(creator_id=session["subject_id"])

    @app.post("/creator/api/commission/corrections/{correction_id}/agree")
    def creator_agree_correction(correction_id: str, body: CorrectionAnswerIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.respond_to_correction(session["subject_id"], correction_id, agree=True, reason=body.reason, actor=session["email"])

    @app.post("/creator/api/commission/corrections/{correction_id}/disagree")
    def creator_disagree_correction(correction_id: str, body: CorrectionAnswerIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.respond_to_correction(session["subject_id"], correction_id, agree=False, reason=body.reason, actor=session["email"])

    @app.get("/creator/api/links")
    def creator_links_api(request: Request, _: None = portal_limit):
        session = _creator(request)
        return {"links": attribution.list_links(shop_id=None, creator_id=session["subject_id"])}

    @app.get("/creator/api/collabs")
    def creator_collabs(request: Request, _: None = portal_limit):
        session = _creator(request)
        return commission.list_agreements(creator_id=session["subject_id"], viewer="creator")

    @app.get("/creator/api/collabs/{collab_id}/spark-ads")
    def creator_spark_ads(collab_id: str, request: Request, _: None = portal_limit):
        session = _creator(request)
        return spark_ads.list_codes(collab_id=collab_id, viewer="creator", creator_id=session["subject_id"])

    @app.post("/creator/api/collabs/{collab_id}/spark-ads")
    def creator_add_spark_ad(collab_id: str, body: SparkAdIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return spark_ads.add_code(
            session["subject_id"], collab_id, post_url=body.post_url, spark_code=body.spark_code,
            authorised_until=body.authorised_until, usage_note=body.usage_note, consent=body.consent, actor=session["email"],
        )

    @app.get("/creator/api/collabs/{collab_id}/spark-ads/{spark_id}")
    def creator_spark_ad(collab_id: str, spark_id: str, request: Request, _: None = portal_limit):
        session = _creator(request)
        return spark_ads.get_code(collab_id=collab_id, spark_id=spark_id, viewer="creator", creator_id=session["subject_id"])

    @app.post("/creator/api/collabs/{collab_id}/spark-ads/{spark_id}/revoke")
    def creator_revoke_spark_ad(collab_id: str, spark_id: str, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return spark_ads.revoke(session["subject_id"], collab_id, spark_id, actor=session["email"])

    @app.post("/creator/api/collabs/{agreement_id}/accept")
    def creator_accept(agreement_id: str, body: AcceptIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.accept_offer(
            session["subject_id"], agreement_id, terms_version_shown=body.terms_version, terms_sha256_shown=body.terms_sha256,
            actor=session["email"], ip=main.client_ip(request),
        )

    @app.get("/creator/api/shops/{shop_id}/pays-on-time")
    def creator_pays_on_time(shop_id: str, request: Request, _: None = portal_limit):
        session = _creator(request)
        commission.require_db()
        if not commission.creator_can_view_record(session["subject_id"], shop_id):
            return JSONResponse({"detail": "Not available"}, status_code=404)
        return commission.pays_on_time(shop_id)

    @app.get("/creator/api/abn")
    def creator_abn(request: Request, _: None = portal_limit):
        session = _creator(request)
        commission.require_db()
        return commission.creator_abn_status(session["subject_id"])

    @app.put("/creator/api/abn")
    def creator_set_abn(body: AbnIn, request: Request, _: None = portal_limit):
        session = _creator(request, write=True)
        return commission.set_creator_abn(session["subject_id"], body.abn)

    # ------------------------------------------------------------ commission: admin
    @app.post("/admin/api/commission/statements/generate")
    def admin_generate(body: GenerateIn, actor: str = admin):
        return commission.generate_statements(body.period, shop_id=body.shop_id, actor=actor)

    @app.post("/admin/api/commission/overdue-sweep")
    def admin_overdue_sweep(_: str = admin):
        return commission.overdue_sweep()

    @app.get("/admin/api/commission/statements")
    def admin_statements(shop_id: Optional[str] = None, creator_id: Optional[str] = None, _: str = admin):
        return commission.list_statements(shop_id=shop_id, creator_id=creator_id, viewer="admin")

    @app.get("/admin/api/commission/statements/{statement_id}")
    def admin_statement(statement_id: str, _: str = admin):
        return commission.statement_detail(statement_id, viewer="admin")

    @app.post("/admin/api/commission/statements/{statement_id}/resolve-dispute")
    def admin_resolve(statement_id: str, body: ResolveIn, actor: str = admin):
        return commission.resolve_dispute(statement_id, reason=body.reason, outcome=body.outcome, admin=actor)

    @app.get("/admin/api/commission/corrections")
    def admin_corrections(status: Optional[str] = None, shop_id: Optional[str] = None, _: str = admin):
        return commission.list_corrections(shop_id=shop_id, status=status)

    @app.post("/admin/api/commission/corrections/{correction_id}/decide")
    def admin_decide_correction(correction_id: str, body: CorrectionDecisionIn, actor: str = admin):
        return commission.decide_correction(correction_id, outcome=body.outcome, reason=body.reason, admin=actor)

    @app.post("/admin/api/commission/corrections/settle")
    def admin_settle_corrections(_: str = admin):
        return commission.settle_expired_corrections()

    @app.get("/admin/api/shops/{shop_id}/collab-block")
    def admin_block(shop_id: str, _: str = admin):
        commission.require_db()
        return commission.collab_block(shop_id)

    @app.post("/admin/api/shops/{shop_id}/collab-block/lift")
    def admin_lift(shop_id: str, body: ReasonIn, actor: str = admin):
        return commission.lift_block(shop_id, reason=body.reason, admin=actor)

    @app.get("/admin/api/commission/audit")
    def admin_audit(shop_id: Optional[str] = None, statement_id: Optional[str] = None, _: str = admin):
        return commission.audit_log(shop_id=shop_id, statement_id=statement_id)
