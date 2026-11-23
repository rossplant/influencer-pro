"""Portal pages added in v0.26.0: statements, content kit, and the staging UAT walkthrough."""

from __future__ import annotations

import html
import os

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import auth
import billing_config
import brand_ui
import db
import portal
import uat_mode


def _statements(role: str, subject_id: str) -> list:
    if not db.configured():
        return []
    import commission

    if role == "seller":
        return commission.list_statements(shop_id=subject_id, viewer="seller")
    return commission.list_statements(creator_id=subject_id, viewer="creator")


def _detail(role: str, subject_id: str, statement_id: str) -> dict:
    import commission

    if role == "seller":
        return commission.statement_detail(statement_id, viewer="seller", shop_id=subject_id)
    return commission.statement_detail(statement_id, viewer="creator", creator_id=subject_id)


def register(app, require_admin) -> None:
    @app.get("/seller/statements")
    def seller_statements(request: Request):
        session = portal.seller_context(request)
        rows = _statements("seller", session["subject_id"]) if session else []
        return portal.render_seller(session, "statements", statements=rows)

    @app.get("/seller/statements/{statement_id}")
    def seller_statement(statement_id: str, request: Request):
        session = portal.require_seller(request)
        detail = _detail("seller", session["subject_id"], statement_id)
        return portal.render_seller(session, "statements", statements=_statements("seller", session["subject_id"]), detail=detail)

    @app.get("/seller/statements/{statement_id}.csv")
    def seller_statement_csv(statement_id: str, request: Request):
        session = portal.require_seller(request)
        import commission

        if not db.configured():
            raise HTTPException(status_code=503, detail="Commission tracking needs the database")
        body = commission.statement_csv(statement_id, shop_id=session["subject_id"])
        return Response(body, media_type="text/csv")

    @app.get("/creator/statements")
    def creator_statements(request: Request):
        session = portal.creator_context(request)
        rows = _statements("creator", session["subject_id"]) if session else []
        return portal.render_creator(session, "statements", statements=rows)

    @app.get("/creator/statements/{statement_id}")
    def creator_statement(statement_id: str, request: Request):
        session = portal.require_creator(request)
        detail = _detail("creator", session["subject_id"], statement_id)
        return portal.render_creator(session, "statements", statements=_statements("creator", session["subject_id"]), detail=detail)

    @app.get("/creator/statements/{statement_id}.csv")
    def creator_statement_csv(statement_id: str, request: Request):
        session = portal.require_creator(request)
        if not db.configured():
            raise HTTPException(status_code=503, detail="Commission tracking needs the database")
        detail = _detail("creator", session["subject_id"], statement_id)
        lines = ["period,status,amount"]
        lines.append(f"{detail.get('period')},{detail.get('status')},{detail.get('amount_owed')}")
        return Response("\n".join(lines) + "\n", media_type="text/csv")

    @app.get("/creator/samples/{sample_id}/kit")
    def content_kit(sample_id: str, request: Request):
        session = portal.creator_context(request)
        if session is None:
            return portal.render_creator(None, "home")
        import listing_studio
        import samples as sample_flow

        row = next((item for item in sample_flow.list_for_creator(session["subject_id"]) if item["id"] == sample_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Sample request not found")
        kit = listing_studio.approved_for_creatr(row["product_id"])
        offer = next((item for item in portal.creator_offers(session["subject_id"]) if item.get("product_id") == row["product_id"]), {})
        page = portal.content_kit_page(
            {**row, "scenes": kit.get("scenes") or [], "angles": kit.get("angles") or [], "link_code": offer.get("discount_code") or offer.get("tracking_path") or ""},
            base_url=billing_config.public_base_url(request),
        )
        return portal.render_creator(session, "kit", kit_html=page)

    @app.get("/creator/samples/{sample_id}/kit.txt")
    def content_kit_download(sample_id: str, request: Request):
        session = portal.require_creator(request)
        import samples as sample_flow

        row = next((item for item in sample_flow.list_for_creator(session["subject_id"]) if item["id"] == sample_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Sample request not found")
        text = f"{(row.get('product') or {}).get('title') or 'This product'}\n#Ad\n"
        return Response(text, media_type="text/plain")

    @app.get("/uat")
    def uat_checklist(request: Request):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        steps = "".join(
            f"<li><form method='post' action='/uat/{html.escape(step)}'>"
            f"<strong>{html.escape(step.replace('_', ' '))}</strong> "
            f"<input type='hidden' name='csrf_token' value='{html.escape(auth.issue_form_token('uat'))}'>"
            f"<label>Comment <input name='comment'></label> "
            f"<button name='result' value='pass'>Pass</button> "
            f"<button name='result' value='fail'>Fail</button></form></li>"
            for step in uat_mode.STEPS
        )
        body = f"""
        <h1>UAT checklist</h1>
        <p>Test environment. Walk the supplier join, listing and Listing Studio, Creatr join with the age check,
        the match, the sample, shipping, delivery, the content kit, a post with #Ad, a sale, the statement,
        the emails, and sandbox billing.</p>
        <form method="post" action="/uat/sale">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('uat'))}">
          <label>Code or link <input name="code" value="UAT"></label>
          <button type="submit">Simulate a sale</button>
        </form>
        <ol>{steps}</ol>
        """
        return HTMLResponse(brand_ui.document("UAT checklist", body))

    @app.post("/uat/sale")
    async def uat_sale(request: Request):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        uat_mode.simulate_sale(str(form.get("code") or ""))
        return RedirectResponse("/uat", status_code=303)

    @app.post("/uat/{step}")
    async def uat_step(step: str, request: Request):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        try:
            uat_mode.record(step, str(form.get("result") or ""), str(form.get("comment") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return RedirectResponse("/uat", status_code=303)

    @app.get("/admin/uat")
    def admin_uat(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        rows = "".join(
            f"<li>{html.escape(row['step'])}: {html.escape(row['result'])} {html.escape(row['comment'])}</li>"
            for row in uat_mode.feedback_rows()
        )
        body = f"""
        <h1>UAT feedback</h1>
        <form method="post" action="/admin/uat/create">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <label>Email for both UAT accounts <input name="email" type="email" required></label>
          <button type="submit">Create a fresh UAT set</button>
        </form>
        <form method="post" action="/admin/uat/send">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <button type="submit">Send invite</button>
        </form>
        <p>One email owns the supplier account and the Creatr account. The invite has two labelled links. Nothing is emailed until Send invite.</p>
        <p><a href="/uat/homeware.csv">Sample homeware CSV</a></p>
        <form method="post" action="/admin/uat/reset">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <button type="submit">Reset UAT data</button>
        </form>
        <p><a href="/admin/uat.csv">Download CSV</a></p>
        <ul>{rows or '<li>No feedback yet.</li>'}</ul>
        """
        return HTMLResponse(brand_ui.document("UAT feedback", body))

    @app.post("/admin/uat/create")
    async def admin_uat_create(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        try:
            uat_mode.create_set(str(form.get("email") or ""))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return RedirectResponse("/admin/uat", status_code=303)

    @app.post("/admin/uat/send")
    async def admin_uat_send(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        try:
            uat_mode.send_invite()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return RedirectResponse("/admin/uat", status_code=303)

    @app.get("/uat/invite/{token}")
    def uat_invite_page(token: str):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        try:
            opened = uat_mode.open_invite(token)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        label = "Supplier" if opened["role"] == "seller" else "Creatr"
        body = f"""
        <h1>{label} invite</h1>
        <p>All users must be 18 or over. Enter a date of birth and confirm you are 18 or over, then set a password.</p>
        <form method="post" action="/uat/invite/{html.escape(token)}">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('uat-invite'))}">
          <label>Date of birth <input name="date_of_birth" type="date" required></label>
          <label class="check"><input name="age_confirmed" type="checkbox" value="yes" required> I confirm I am 18 or over. All users must be 18 or over.</label>
          <label>Password <input name="password" type="password" minlength="12" required></label>
          <button type="submit">Set password</button>
        </form>
        """
        return HTMLResponse(brand_ui.document(f"{label} invite", body))

    @app.post("/uat/invite/{token}")
    async def uat_invite_submit(token: str, request: Request):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "uat-invite"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        import creatr_age

        born, date_error = creatr_age.parse_born(str(form.get("date_of_birth") or ""))
        if date_error:
            raise HTTPException(status_code=422, detail=date_error)
        try:
            opened = uat_mode.activate_invite(
                token,
                born=born,
                attested=str(form.get("age_confirmed") or "") == "yes",
                password=str(form.get("password") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        target = "/seller" if opened["role"] == "seller" else "/creator"
        return RedirectResponse(target, status_code=303)

    @app.get("/uat/homeware.csv")
    def uat_homeware_csv():
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        body = (
            "title,url,price,category,image_url\n"
            "Stoneware bowl,https://example.com/bowl,42,home,https://example.com/bowl.jpg\n"
            "Linen napkin,https://example.com/napkin,18,home,https://example.com/napkin.jpg\n"
            "Oak board,https://example.com/board,64,home,https://example.com/board.jpg\n"
        )
        return Response(body, media_type="text/csv")

    @app.post("/admin/uat/reset")
    async def admin_uat_reset(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        uat_mode.reset()
        return RedirectResponse("/admin/uat", status_code=303)

    @app.get("/admin/uat.csv")
    def admin_uat_csv(_: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        return Response(uat_mode.feedback_csv(), media_type="text/csv")

    @app.post("/admin/api/commission/statements/run")
    def admin_statement_run(request: Request, _: str = Depends(require_admin)):
        import statements_mail

        period = request.query_params.get("period") or ""
        if not period:
            from datetime import datetime, timezone

            period = datetime.now(timezone.utc).strftime("%Y-%m")
        if not db.configured():
            raise HTTPException(status_code=503, detail="Commission tracking needs the database")
        return statements_mail.run_month(period, actor="admin")
