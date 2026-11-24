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
import emailer
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


def _invite_status_html(states: dict) -> str:
    lines = []
    for role, label in (("seller", "Supplier"), ("creator", "Creatr")):
        row = states.get(role) or {}
        if row.get("status") == "sent" and row.get("provider_id"):
            lines.append(f"<p>{label} invite: sent at {html.escape(str(row.get('sent_at') or ''))}. Resend id {html.escape(str(row.get('provider_id')))}.</p>")
        elif row.get("status") == "failed":
            reason = row.get("last_error") or "the mail service did not accept it"
            lines.append(f"<p>{label} invite: failed. {html.escape(str(reason))}.</p>")
        else:
            lines.append(f"<p>{label} invite: not sent yet.</p>")
    return "".join(lines)


def _retry_forms(current: dict, email_value: str) -> str:
    if not current:
        return ""
    states = uat_mode.invite_states(current["id"])
    forms = []
    for role, label in (("seller", "supplier"), ("creator", "Creatr")):
        row = states.get(role) or {}
        if row.get("status") != "failed":
            continue
        forms.append(
            f"""
        <form method="post" action="/admin/uat/send">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <input type="hidden" name="email" value="{email_value}">
          <input type="hidden" name="role" value="{role}">
          <input type="hidden" name="press_id" value="{html.escape(str(__import__('uuid').uuid4()))}">
          <button type="submit">Retry the {label} invite</button>
        </form>"""
        )
    return "".join(forms)


def _invite_flash(result: dict) -> tuple[str, bool]:
    if result.get("duplicate"):
        return "This press already sent the invites. Nothing else was emailed.", False
    roles = result.get("roles") or {}
    if not roles and result.get("reason"):
        return f"Invite failed: {result['reason']}, try again", True
    bits = []
    failed = False
    for key, label in (("seller", "Supplier"), ("creator", "Creatr")):
        row = roles.get(key)
        if not row:
            continue
        if row.get("sent"):
            bits.append(f"{label} invite sent at {row.get('sent_at') or ''}. Resend id {row.get('provider_id') or ''}.")
        else:
            failed = True
            reason = row.get("reason") or "the mail service did not accept it"
            bits.append(f"{label} invite failed: {reason}. Retry that invite alone.")
    if not failed and result.get("already"):
        return "Both invites were already sent. Nothing else was emailed.", False
    return " ".join(bits), failed


def register(app, require_admin) -> None:
    @app.get("/invite/opt-out/{token}")
    def invite_opt_out(token: str):
        address = emailer.read_opt_out_token(token)
        if not address:
            body = "<h1>Opt out</h1><p>That opt-out link is not valid.</p>"
            return HTMLResponse(brand_ui.document("Opt out", body), status_code=404)
        emailer.record_invite_opt_out(address)
        body = "<h1>Opt out</h1><p>You've opted out of Plantr Collab invites.</p>"
        return HTMLResponse(brand_ui.document("Opt out", body))

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
    def admin_uat(request: Request):
        if auth.session_email(request) is None:
            return RedirectResponse("/admin", status_code=303)
        require_admin(request)
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        rows = "".join(
            f"<li>{html.escape(row['step'])}: {html.escape(row['result'])} {html.escape(row['comment'])}</li>"
            for row in uat_mode.feedback_rows()
        )
        return HTMLResponse(brand_ui.document("UAT feedback", _uat_admin_body(rows, notice="", error="", email=str(request.query_params.get("email") or ""))))

    def _uat_admin_body(rows: str, *, notice: str, error: str, email: str = "") -> str:
        selected = (email or "").strip().lower()
        current = uat_mode.current_set(selected) if selected else (uat_mode.current_set() or {})
        current = current or {}
        if current:
            states = uat_mode.invite_states(current["id"])
            status = _invite_status_html(states)
        else:
            status = "<p>Invite status: no UAT set yet.</p>"
        banner = f"<p class='banner'>{html.escape(notice)}</p>" if notice else ""
        problem = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        email_value = html.escape(str(current.get("email") or ""))
        creatr_email_value = str(current.get("creatr_email") or "")
        if creatr_email_value == str(current.get("email") or ""):
            creatr_email_value = ""
        creatr_email_value = html.escape(creatr_email_value)
        send_disabled = "" if current else " disabled"
        links = ""
        if current:
            paths = uat_mode._issue_invites(current)
            links = (
                f"<p>Showing the set for {html.escape(str(current.get('email') or ''))}.</p>"
                f"<p><a href=\"{html.escape(paths['seller'])}\">Open the supplier invite</a> <code>{html.escape(paths['seller'])}</code></p>"
                f"<p><a href=\"{html.escape(paths['creator'])}\">Open the Creatr invite</a> <code>{html.escape(paths['creator'])}</code></p>"
            )
        return f"""
        <h1>UAT</h1>
        {banner}{problem}
        {status}
        <p>One email can own both the supplier and the Creatr. Add a Creatr email only when the test uses two addresses. Audience 45,000. The catalogue is uploaded in the supplier journey, not in the invite.</p>
        <p>Invites are sent from {html.escape(emailer.invite_from_name())}. Replies go to {html.escape(emailer.invite_reply_to())}.</p>
        {links}
        <form method="get" action="/admin/uat">
          <label>View set by email <input name="email" type="email" value="{email_value}"></label>
          <button type="submit">Show this set</button>
        </form>
        <form method="post" action="/admin/uat/create-set">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <label>Email <input name="email" type="email" value="{email_value}" required></label>
          <label>First name <input name="first_name" value="{html.escape(str(current.get('first_name') or ''))}"></label>
          <label>Brand / shop name <input name="brand_name" value="{html.escape(str(current.get('brand_name') or ''))}"></label>
          <label>Creatr name <input name="creatr_name" value="{html.escape(str(current.get('creatr_name') or ''))}"></label>
          <label>Creatr email (if different) <input name="creatr_email" type="email" value="{creatr_email_value}"></label>
          <button type="submit">Create set (no email)</button>
          <button type="submit" formaction="/admin/uat/reset-set">Reset UAT set</button>
        </form>
        <form method="post" action="/admin/uat/send">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <input type="hidden" name="email" value="{email_value}">
          <input type="hidden" name="press_id" value="{html.escape(str(__import__('uuid').uuid4()))}">
          <button type="submit"{send_disabled}>Send invite</button>
        </form>
        {_retry_forms(current, email_value)}
        <p><a href="/uat/homeware.csv">Sample homeware CSV</a></p>
        <form method="post" action="/admin/uat/reset">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('admin-uat'))}">
          <button type="submit">Reset UAT data</button>
        </form>
        <p><a href="/admin/uat.csv">Download CSV</a></p>
        <ul>{rows or '<li>No feedback yet.</li>'}</ul>
        """

    @app.post("/admin/uat/create-set")
    async def admin_uat_create_set(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            return _uat_message("UAT mode is off.")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            return _uat_message("The form expired. Open the UAT screen and try again.")
        try:
            result = uat_mode.create_set(
                str(form.get("email") or ""),
                first_name=str(form.get("first_name") or ""),
                brand_name=str(form.get("brand_name") or ""),
                creatr_email=str(form.get("creatr_email") or ""),
                creatr_name=str(form.get("creatr_name") or ""),
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            text = "The supplier shop could not be created." if str(detail) == "Shop not found" else str(detail)
            return _uat_message(text, email=str(form.get("email") or ""))
        note = "UAT set ready. No email was sent."
        if result.get("reused"):
            note = "That email already has a UAT set. No email was sent."
        return _uat_message(note, error=False, email=result.get("email") or "")

    @app.post("/admin/uat/create")
    async def admin_uat_create(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            return _uat_message("UAT mode is off.")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            return _uat_message("The form expired. Open the UAT screen and try again.")
        try:
            result = uat_mode.create_set(
                str(form.get("email") or ""),
                first_name=str(form.get("first_name") or ""),
                brand_name=str(form.get("brand_name") or ""),
                creatr_email=str(form.get("creatr_email") or ""),
                creatr_name=str(form.get("creatr_name") or ""),
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            text = str(detail)
            if text == "Shop not found":
                text = "The supplier shop could not be created. Nothing was emailed."
            return _uat_message(text, email=str(form.get("email") or ""))
        return _uat_message("UAT set ready. No email was sent.", error=False, email=result.get("email") or "")

    def _uat_message(text: str, *, error: bool = True, email: str = ""):
        rows = "".join(
            f"<li>{html.escape(row['step'])}: {html.escape(row['result'])} {html.escape(row['comment'])}</li>"
            for row in uat_mode.feedback_rows()
        )
        notice = "" if error else text
        problem = text if error else ""
        return HTMLResponse(brand_ui.document("UAT", _uat_admin_body(rows, notice=notice, error=problem, email=email)))

    @app.post("/admin/uat/send")
    async def admin_uat_send(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            return _uat_message("UAT mode is off.")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            return _uat_message("The form expired. Open the UAT screen and try again.")
        target = str(form.get("email") or "")
        if (uat_mode.current_set(target) if target else uat_mode.current_set()) is None:
            return _uat_message("Create a UAT set before sending the invite. Nothing was emailed.", email=target)
        try:
            result = uat_mode.send_invite(
                str(form.get("press_id") or ""),
                billing_config.public_base_url(request),
                email=target,
                role=str(form.get("role") or ""),
            )
        except Exception as exc:
            return _uat_message(str(getattr(exc, "detail", None) or exc), email=target)
        text, failed = _invite_flash(result)
        return _uat_message(text, error=failed, email=target or result.get("email") or "")

    def _invite_problem(text: str) -> HTMLResponse:
        body = f"<h1>Invite link</h1><p>{html.escape(text)}</p>"
        return HTMLResponse(brand_ui.document("Invite link", body), status_code=404)

    def _invite_form(action: str, label: str) -> HTMLResponse:
        body = f"""
        <h1>{label} invite</h1>
        <p>These are test accounts. All users must be 18 or over. Enter a date of birth and confirm you are 18 or over, then set a password.</p>
        <form method="post" action="{action}">
          <input type="hidden" name="csrf_token" value="{html.escape(auth.issue_form_token('uat-invite'))}">
          <label>Date of birth <input name="date_of_birth" type="date" required></label>
          <label class="check"><input name="age_confirmed" type="checkbox" value="yes" required> I confirm I am 18 or over. All users must be 18 or over.</label>
          <label>Password <input name="password" type="password" minlength="12" required></label>
          <button type="submit">Set password</button>
        </form>
        """
        return HTMLResponse(brand_ui.document(f"{label} invite", body))

    @app.get("/uat/invite")
    def uat_invite_help():
        body = "<h1>Invite link</h1><p>Open the supplier or Creatr link from your test invite. All users must be 18 or over.</p>"
        return HTMLResponse(brand_ui.document("Invite link", body))

    @app.get("/uat/invite/s/{code}")
    def uat_supplier_invite(code: str):
        if uat_mode.invite_by_code(code, "seller") is None:
            return _invite_problem("That invite link is not valid or has expired.")
        return _invite_form(f"/uat/invite/s/{html.escape(code)}", "Supplier")

    @app.get("/uat/invite/c/{code}")
    def uat_creatr_invite(code: str):
        if uat_mode.invite_by_code(code, "creator") is None:
            return _invite_problem("That invite link is not valid or has expired.")
        return _invite_form(f"/uat/invite/c/{html.escape(code)}", "Creatr")

    @app.post("/uat/invite/s/{code}")
    async def uat_supplier_invite_submit(code: str, request: Request):
        return await _submit_short_invite(code, "seller", request)

    @app.post("/uat/invite/c/{code}")
    async def uat_creatr_invite_submit(code: str, request: Request):
        return await _submit_short_invite(code, "creator", request)

    async def _submit_short_invite(code: str, role: str, request: Request):
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "uat-invite"):
            return _invite_problem("The form expired. Open the invite link again.")
        import creatr_age

        born, date_error = creatr_age.parse_born(str(form.get("date_of_birth") or ""))
        if date_error:
            return HTMLResponse(brand_ui.document("Invite", f"<h1>Invite</h1><p>{html.escape(date_error)}</p>"), status_code=422)
        try:
            opened = uat_mode.activate_invite(
                code,
                born=born,
                attested=str(form.get("age_confirmed") or "") == "yes",
                password=str(form.get("password") or ""),
                role=role,
            )
        except ValueError as exc:
            return HTMLResponse(brand_ui.document("Invite", f"<h1>Invite</h1><p>{html.escape(str(exc))}</p>"), status_code=422)
        target = "/seller" if opened["role"] == "seller" else "/creator"
        response = RedirectResponse(target, status_code=303)
        portal.set_role_cookie(response, opened["role"], opened["email"], opened["subject_id"])
        return response

    @app.get("/uat/invite/{token}")
    def uat_invite_page(token: str):
        try:
            opened = uat_mode.open_invite(token)
        except ValueError:
            return _invite_problem("That invite link is not valid or has expired.")
        label = "Supplier" if opened["role"] == "seller" else "Creatr"
        return _invite_form(f"/uat/invite/{html.escape(token)}", label)

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
            return HTMLResponse(brand_ui.document("Invite", f"<h1>Invite</h1><p>{html.escape(date_error)}</p>"), status_code=422)
        try:
            opened = uat_mode.activate_invite(
                token,
                born=born,
                attested=str(form.get("age_confirmed") or "") == "yes",
                password=str(form.get("password") or ""),
            )
        except ValueError as exc:
            return HTMLResponse(brand_ui.document("Invite", f"<h1>Invite</h1><p>{html.escape(str(exc))}</p>"), status_code=422)
        target = "/seller" if opened["role"] == "seller" else "/creator"
        response = RedirectResponse(target, status_code=303)
        portal.set_role_cookie(response, opened["role"], opened["email"], opened["subject_id"])
        return response

    @app.get("/uat/homeware.csv")
    def uat_homeware_csv():
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        import catalogue

        return Response(catalogue.TEMPLATE_CSV, media_type="text/csv")

    @app.post("/admin/uat/reset")
    async def admin_uat_reset(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            raise HTTPException(status_code=404, detail="Not available")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        uat_mode.reset()
        return RedirectResponse("/admin/uat", status_code=303)

    @app.post("/admin/uat/reset-set")
    async def admin_uat_reset_set(request: Request, _: str = Depends(require_admin)):
        if not uat_mode.enabled():
            return _uat_message("UAT mode is off.")
        form = await request.form()
        if not auth.form_token_ok(str(form.get("csrf_token") or ""), "admin-uat"):
            return _uat_message("The form expired. Open the UAT screen and try again.")
        email = str(form.get("email") or "")
        try:
            result = uat_mode.reset_set(
                email,
                first_name=str(form.get("first_name") or ""),
                brand_name=str(form.get("brand_name") or ""),
                creatr_email=str(form.get("creatr_email") or ""),
                creatr_name=str(form.get("creatr_name") or ""),
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            return _uat_message(str(detail), email=email)
        return _uat_message("UAT set reset. No email was sent.", error=False, email=result.get("email") or email)

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
