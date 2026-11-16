"""Pilot cohorts, applications, invites, and the admin funnel."""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select

import billing_config
import db
import emailer
import services
import touch
import tracking
from models import (
    Creator,
    PilotApplication,
    PilotCohort,
    PilotInvite,
    PilotMembership,
    PilotSurvey,
    PilotVisit,
    Shop,
    utcnow,
)


def ensure_cohorts() -> None:
    """Both cohorts run 20 Nov 2026 to 20 Jan 2027. An existing row is moved onto it."""
    start = billing_config.pilot_start()
    end = billing_config.pilot_end()
    for slug, spec in billing_config.PILOT_COHORTS.items():
        fields = dict(
            name=spec["name"],
            role=spec["role"],
            seat_cap=billing_config.seat_cap(slug),
            starts_at=start,
            ends_at=end,
            pilot_tier=spec["pilot_tier"],
            post_coupon_key=spec["post_coupon"],
            active=True,
        )
        if db.configured():
            with db.session_scope() as session:
                row = session.scalar(select(PilotCohort).where(PilotCohort.slug == slug))
                if row is None:
                    session.add(PilotCohort(id=str(uuid.uuid4()), slug=slug, **fields))
                else:
                    row.name = fields["name"]
                    row.starts_at = start
                    row.ends_at = end
                    row.post_coupon_key = fields["post_coupon_key"]
            continue
        existing = db.memory_cohorts.get(slug)
        if existing is None:
            db.memory_cohorts[slug] = SimpleNamespace(id=str(uuid.uuid4()), slug=slug, **fields)
        else:
            existing.name = fields["name"]
            existing.starts_at = start
            existing.ends_at = end
            existing.post_coupon_key = fields["post_coupon_key"]


def apply(
    slug: str,
    *,
    name: str,
    email: str,
    role: str,
    categories: str,
    contact: str,
    age_confirmed: bool,
    touch_state: Optional[dict],
    consent: bool,
    event_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    paid_service_confirmed: bool = False,
    authority_confirmed: bool = False,
    email_consent: bool = False,
    business_name: str = "",
    abn: str = "",
) -> dict:
    """consent is the cookie choice for ad platforms. email_consent is the separate email opt-in."""
    import legal

    if not billing_config.operator_legal_name():
        raise HTTPException(status_code=503, detail=legal.OPENING_SOON)
    ensure_cohorts()
    spec = billing_config.PILOT_COHORTS.get(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown cohort")
    if spec["role"] == "creator" and not age_confirmed:
        raise HTTPException(status_code=422, detail="Creators must confirm they are 18 or older")
    if spec["role"] == "seller" and not authority_confirmed:
        raise HTTPException(status_code=422, detail="Sellers must confirm they are 18 or older and authorised to act for the business")
    if not paid_service_confirmed:
        raise HTTPException(status_code=422, detail="Please confirm you understand Plantr Collab is a paid service after the pilot")
    clean_abn = None
    if spec["role"] == "seller":
        try:
            clean_abn = legal.clean_abn(abn)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if not business_name.strip():
            raise HTTPException(status_code=422, detail="Business name is required")
    cleaned = (email or "").strip().lower()
    if not name.strip() or "@" not in cleaned:
        raise HTTPException(status_code=422, detail="Name and email are required")
    existing = _application_by_email(slug, cleaned)
    if existing is not None:
        return {"id": _get(existing, "id"), "status": _get(existing, "status"), "duplicate": True}
    status = "waitlisted" if seats_used(slug) >= billing_config.seat_cap(slug) else "pending"
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        cohort_slug=slug,
        name=name.strip(),
        email=cleaned,
        role=spec["role"],
        categories=categories.strip(),
        contact=contact.strip(),
        status=status,
        founding_number=None,
        attribution={"first": touch.first_of(touch_state), "last": touch.last_of(touch_state)},
        age_confirmed=bool(age_confirmed),
        marketing_consent=bool(consent),
        email_marketing_consent=bool(email_consent),
        business_name=business_name.strip() or None,
        abn=clean_abn,
        authority_confirmed=bool(authority_confirmed),
        created_at=utcnow(),
        reviewed_at=None,
    )
    _save_application(row)
    if spec["role"] == "seller":
        texts = [legal.SELLER_PANEL, legal.SELLER_AUTHORITY, legal.SELLER_APPLY]
    else:
        texts = [legal.CREATOR_PANEL, legal.CREATOR_AGE, legal.CREATOR_APPLY]
    if email_consent:
        texts.append(legal.EMAIL_CONSENT)
    legal.record(
        subject_type="pilot_application",
        subject_id=row.id,
        context=f"apply:{slug}",
        documents=legal.APPLY_DOCUMENTS,
        texts=texts,
        price_shown=texts[0],
        confirmations={
            "paid_service": True,
            "age_18": bool(age_confirmed) or bool(authority_confirmed),
            "authorised": bool(authority_confirmed),
            "email_marketing": bool(email_consent),
            "operator": billing_config.operator_legal_name(),
        },
        ip=ip,
        user_agent=user_agent,
    )
    tracked_id = event_id or f"apply:{row.id}"
    invite_code = touch.last_of(touch_state).get("invite_code") or touch.first_of(touch_state).get("invite_code")
    tracked = tracking.track(
        "apply_submit",
        event_id=tracked_id,
        role=spec["role"],
        entity_type="pilot_application",
        entity_id=row.id,
        actor="api",
        consent=consent,
        email=cleaned,
        ip=ip,
        user_agent=user_agent,
        touch=touch_state,
        invite_code=invite_code,
        cohort=slug,
    )
    return {"id": row.id, "status": status, "event_id": tracked["event_id"]}


def approve(application_id: str) -> dict:
    ensure_cohorts()
    application = _application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if _get(application, "status") == "approved":
        return {"id": application_id, "status": "approved", "duplicate": True}
    slug = _get(application, "cohort_slug")
    if seats_used(slug) >= billing_config.seat_cap(slug):
        _set(application, "status", "waitlisted")
        _set(application, "reviewed_at", utcnow())
        return {"id": application_id, "status": "waitlisted"}
    cohort = _cohort(slug)
    number = None
    if slug == "founding_curators":
        number = _next_founding_number()
        _set(application, "founding_number", number)
    invite = _new_invite(cohort, _get(application, "email"))
    membership = SimpleNamespace(
        id=str(uuid.uuid4()),
        cohort_id=_get(cohort, "id"),
        application_id=application_id,
        invite_id=_get(invite, "id"),
        email=_get(application, "email"),
        role=_get(application, "role"),
        shop_id=None,
        creator_id=None,
        stage="invited",
        founding_number=number,
        invited_at=utcnow(),
        opened_at=None,
        signed_up_at=None,
        onboarded_at=None,
        active_at=None,
        converted_at=None,
        created_at=utcnow(),
    )
    _save_membership(membership)
    _set(application, "status", "approved")
    _set(application, "reviewed_at", utcnow())
    emailer.send(
        "approval",
        _get(application, "email"),
        code=_get(invite, "code"),
        url=f"/join/{_get(invite, 'code')}",
    )
    stored = _get(application, "attribution") or {}
    if isinstance(stored, dict) and ("first" in stored or "last" in stored):
        stored_touch = stored
    else:
        stored_touch = {"first": stored or {}, "last": stored or {}}
    tracked = tracking.track(
        "application_approved",
        event_id=f"approve:{application_id}",
        role=_get(application, "role"),
        entity_type="pilot_application",
        entity_id=application_id,
        actor="operator",
        consent=bool(_get(application, "marketing_consent")),
        email=_get(application, "email"),
        touch=stored_touch,
        cohort=slug,
        invite_code=_get(invite, "code"),
    )
    return {
        "id": application_id,
        "status": "approved",
        "invite_code": _get(invite, "code"),
        "founding_number": number,
        "event_id": tracked["event_id"],
        "event_name": tracked["event_name"],
    }


def reject(application_id: str) -> dict:
    application = _application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    _set(application, "status", "rejected")
    _set(application, "reviewed_at", utcnow())
    tracking.track(
        "application_rejected",
        event_id=f"reject:{application_id}",
        role=_get(application, "role"),
        entity_type="pilot_application",
        entity_id=application_id,
        actor="operator",
        cohort=_get(application, "cohort_slug"),
    )
    return {"id": application_id, "status": "rejected"}


def open_invite(code: str) -> dict:
    invite = _invite_by_code(code)
    if invite is None or not _invite_usable(invite):
        raise HTTPException(status_code=404, detail="Invite not found")
    membership = _membership_for_invite(_get(invite, "id"))
    if membership is not None and _get(membership, "opened_at") is None:
        _set(membership, "opened_at", utcnow())
        if _get(membership, "stage") == "invited":
            _set(membership, "stage", "opened")
    cohort = _cohort_by_id(_get(invite, "cohort_id"))
    return {
        "code": _get(invite, "code"),
        "role": _get(cohort, "role"),
        "cohort": _get(cohort, "slug"),
        "campaign": _get(invite, "campaign"),
        "channel": _get(invite, "channel"),
        "founding_number": None if membership is None else _get(membership, "founding_number"),
    }


def join_with_invite(
    code: str,
    *,
    name: str,
    email: str,
    password: str,
    categories: str,
    age_confirmed: bool,
    touch_state: Optional[dict],
    consent: bool,
    terms_accepted: bool = False,
    pilot_terms_accepted: bool = False,
    email_consent: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """consent is the cookie choice for ad platforms. email_consent is the separate email opt-in."""
    import legal

    opened = open_invite(code)
    if opened["role"] == "creator" and not age_confirmed:
        raise HTTPException(status_code=422, detail="Creators must confirm they are 18 or older")
    if not terms_accepted or not pilot_terms_accepted:
        raise HTTPException(status_code=422, detail="Please tick both required boxes to continue")
    invite = _invite_by_code(code)
    cleaned = email.strip().lower()
    if opened["role"] == "seller":
        shop = services.create_shop(
            name=name or cleaned,
            shopify_domain=None,
            plan_tier=billing_config.PILOT_COHORTS[opened["cohort"]]["pilot_tier"],
            plan_status="trial",
            is_pilot=True,
        )
        subject_id = shop["id"]
        import portal

        portal.create_login(
            role="seller",
            email=cleaned,
            password=password,
            password_confirm=password,
            shop_id=subject_id,
        )
        _stamp_account("seller", subject_id, touch_state, code, opened, age_confirmed=False, marketing_consent=consent, email_consent=email_consent)
    else:
        creator_id = str(uuid.uuid4())
        active = [part.strip().lower() for part in categories.split(",") if part.strip()] or ["other"]
        services.persist_creator(
            {
                "influencer_id": creator_id,
                "handle": name.strip() or cleaned.split("@", 1)[0],
                "areas_of_interest": active,
                "preferred_promotion_styles": ["educational"],
                "active_categories": active,
                "platforms": ["other"],
                "availability": "open",
            }
        )
        import portal

        portal.create_login(
            role="creator",
            email=cleaned,
            password=password,
            password_confirm=password,
            creator_id=creator_id,
        )
        subject_id = creator_id
        _stamp_account("creator", subject_id, touch_state, code, opened, age_confirmed=True, marketing_consent=consent, email_consent=email_consent)
    _consume_invite(invite)
    if opened["role"] == "seller":
        texts = [legal.SELLER_JOIN_TERMS, legal.SELLER_JOIN_PILOT]
        documents = legal.SELLER_JOIN_DOCUMENTS
    else:
        texts = [legal.CREATOR_JOIN_TERMS, legal.CREATOR_JOIN_PILOT]
        documents = legal.CREATOR_JOIN_DOCUMENTS
    if email_consent:
        texts.append(legal.EMAIL_CONSENT)
    legal.record(
        subject_type="shop" if opened["role"] == "seller" else "creator",
        subject_id=subject_id,
        context=f"join:{opened['cohort']}",
        documents=documents,
        texts=texts,
        price_shown=texts[1],
        confirmations={"terms": True, "pilot_terms": True, "age_18": True, "email_marketing": bool(email_consent), "invite": code},
        ip=ip,
        user_agent=user_agent,
    )
    membership = _membership_for_invite(_get(invite, "id"))
    if membership is not None:
        _set(membership, "stage", "signed_up")
        _set(membership, "signed_up_at", utcnow())
        if opened["role"] == "seller":
            _set(membership, "shop_id", subject_id)
        else:
            _set(membership, "creator_id", subject_id)
    tracked = tracking.track(
        "complete_registration",
        event_id=f"reg:{subject_id}",
        role=opened["role"],
        entity_type="shop" if opened["role"] == "seller" else "creator",
        entity_id=subject_id,
        shop_id=subject_id if opened["role"] == "seller" else None,
        actor="api",
        consent=consent,
        email=cleaned,
        touch=touch_state,
        invite_code=code,
        subject_key=f"reg:{subject_id}",
    )
    return {
        "role": opened["role"],
        "id": subject_id,
        "cohort": opened["cohort"],
        "event_id": tracked["event_id"],
        "founding_number": opened["founding_number"],
    }


def record_visit(path: str, touch_state: Optional[dict], cohort: Optional[str]) -> None:
    last = touch.last_of(touch_state)
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        path=path,
        cohort_slug=cohort,
        campaign=last.get("utm_campaign") or last.get("campaign"),
        channel=last.get("utm_source") or last.get("channel"),
        code=last.get("invite_code") or last.get("promo_code"),
        occurred_at=utcnow(),
    )
    if db.configured():
        with db.session_scope() as session:
            session.add(
                PilotVisit(
                    id=row.id,
                    path=row.path,
                    cohort_slug=row.cohort_slug,
                    campaign=row.campaign,
                    channel=row.channel,
                    code=row.code,
                    occurred_at=row.occurred_at,
                )
            )
    else:
        db.memory_visits.append(row)


def save_survey(*, role: str, email: Optional[str], subject_id: Optional[str], cohort_slug: str, feels_right: str, comment: str) -> dict:
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        role=role,
        email=email,
        subject_id=subject_id,
        cohort_slug=cohort_slug,
        week=billing_config.SURVEY_WEEK,
        feels_right=feels_right,
        comment=comment,
        created_at=utcnow(),
    )
    if db.configured():
        with db.session_scope() as session:
            session.add(PilotSurvey(**row.__dict__))
    else:
        db.memory_surveys.append(row)
    return {"id": row.id}


def dashboard() -> dict:
    ensure_cohorts()
    cohorts = []
    for slug in billing_config.PILOT_COHORTS:
        cohort = _cohort(slug)
        cohorts.append(
            {
                "slug": slug,
                "seats_used": seats_used(slug),
                "seat_cap": billing_config.seat_cap(slug),
                "starts_at": _get(cohort, "starts_at"),
                "ends_at": _get(cohort, "ends_at"),
                "pilot_tier": _get(cohort, "pilot_tier"),
            }
        )
    return {
        "cohorts": cohorts,
        "funnel": _funnel(),
        "conversion_target": billing_config.conversion_target(),
        "upgrades_by_gate": _gates(),
        "surveys": _survey_count(),
        "applications": [_public_application(row) for row in _applications()],
        "email_notice": emailer.PROVIDER_REQUIRED,
    }


def funnel_csv() -> str:
    lines = ["channel,campaign,code,visits,signups,first_paid,conversion"]
    for row in _funnel():
        lines.append(
            f"{row['channel']},{row['campaign']},{row['code']},{row['visits']},{row['signups']},{row['first_paid']},{row['conversion']}"
        )
    return "\n".join(lines) + "\n"


def seats_used(slug: str) -> int:
    cohort = _cohort(slug)
    if cohort is None:
        return 0
    cohort_id = _get(cohort, "id")
    if db.configured():
        with db.session_scope() as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(PilotMembership).where(PilotMembership.cohort_id == cohort_id)
                )
                or 0
            )
    return sum(1 for row in db.memory_memberships if row.cohort_id == cohort_id)


def _funnel() -> list:
    groups = {}
    for visit in _visits():
        key = (_get(visit, "channel") or "", _get(visit, "campaign") or "", _get(visit, "code") or "")
        bucket = groups.setdefault(key, {"channel": key[0], "campaign": key[1], "code": key[2], "visits": 0, "signups": 0, "first_paid": 0})
        bucket["visits"] += 1
    for membership in _memberships():
        invite = _invite(_get(membership, "invite_id")) if _get(membership, "invite_id") else None
        key = (
            "" if invite is None else _get(invite, "channel") or "",
            "" if invite is None else _get(invite, "campaign") or "",
            "" if invite is None else _get(invite, "code") or "",
        )
        bucket = groups.setdefault(key, {"channel": key[0], "campaign": key[1], "code": key[2], "visits": 0, "signups": 0, "first_paid": 0})
        if _get(membership, "signed_up_at") is not None:
            bucket["signups"] += 1
        if _get(membership, "converted_at") is not None:
            bucket["first_paid"] += 1
    rows = []
    for bucket in groups.values():
        signups = bucket["signups"]
        bucket["conversion"] = round(bucket["first_paid"] / signups, 4) if signups else 0
        rows.append(bucket)
    return rows


def _gates() -> dict:
    counts = {}
    source = db.memory_events
    if db.configured():
        with db.session_scope() as session:
            from models import Event

            source = session.scalars(select(Event).where(Event.event_name == "usage_limit_hit")).all()
    else:
        source = [row for row in source if getattr(row, "event_name", None) == "usage_limit_hit"]
    for event in source:
        props = getattr(event, "properties", None) or {}
        limit = props.get("limit") or "unknown"
        counts[limit] = counts.get(limit, 0) + 1
    return counts


def _stamp_account(role, subject_id, touch_state, code, opened, *, age_confirmed: bool, marketing_consent: bool = False, email_consent: bool = False) -> None:
    if db.configured():
        with db.session_scope() as session:
            row = session.get(Shop if role == "seller" else Creator, subject_id)
            if row is None:
                return
            touch.apply_first_touch(row, touch_state)
            if not row.signup_code:
                row.signup_code = code
                row.signup_campaign = opened["campaign"]
                row.signup_channel = opened["channel"]
            row.marketing_consent = bool(marketing_consent)
            row.email_marketing_consent = bool(email_consent)
            if role == "creator":
                row.age_confirmed = age_confirmed
                row.founding_number = opened["founding_number"]
                row.plan_tier = billing_config.PILOT_COHORTS[opened["cohort"]]["pilot_tier"]
                row.plan_status = "trial"
        return
    if role == "seller":
        row = db.memory_shops.get(subject_id)
    else:
        row = db.memory_creator_objects.get(subject_id)
    if row is None:
        return
    if not getattr(row, "attribution_first", None):
        touch.apply_first_touch(row, touch_state)
    if not getattr(row, "signup_code", None):
        row.signup_code = code
        row.signup_campaign = opened["campaign"]
        row.signup_channel = opened["channel"]
    row.marketing_consent = bool(marketing_consent)
    row.email_marketing_consent = bool(email_consent)
    if role == "creator":
        row.age_confirmed = age_confirmed
        row.founding_number = opened["founding_number"]
        row.plan_tier = billing_config.PILOT_COHORTS[opened["cohort"]]["pilot_tier"]
        row.plan_status = "trial"


def _new_invite(cohort, email: str):
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        cohort_id=_get(cohort, "id"),
        code=secrets.token_urlsafe(8),
        max_uses=1,
        use_count=0,
        expires_at=_get(cohort, "ends_at"),
        campaign=_get(cohort, "slug"),
        channel="invite",
        created_at=utcnow(),
    )
    if db.configured():
        with db.session_scope() as session:
            session.add(
                PilotInvite(
                    id=row.id,
                    cohort_id=row.cohort_id,
                    code=row.code,
                    max_uses=1,
                    use_count=0,
                    expires_at=row.expires_at,
                    campaign=row.campaign,
                    channel=row.channel,
                    created_at=row.created_at,
                )
            )
    else:
        db.memory_invites.append(row)
    emailer.send("invite", email, url=f"/join/{row.code}", code=row.code)
    return row


def _consume_invite(invite) -> None:
    _set(invite, "use_count", int(_get(invite, "use_count") or 0) + 1)


def _invite_usable(invite) -> bool:
    if int(_get(invite, "use_count") or 0) >= int(_get(invite, "max_uses") or 1):
        return False
    expires = _get(invite, "expires_at")
    return expires is None or expires >= utcnow()


def _next_founding_number() -> int:
    numbers = [int(_get(row, "founding_number") or 0) for row in _applications() if _get(row, "cohort_slug") == "founding_curators"]
    return max(numbers or [0]) + 1


def _cohort(slug: str):
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(PilotCohort).where(PilotCohort.slug == slug))
            return None if row is None else _detach_cohort(row)
    return db.memory_cohorts.get(slug)


def _cohort_by_id(cohort_id: str):
    if db.configured():
        with db.session_scope() as session:
            row = session.get(PilotCohort, cohort_id)
            return None if row is None else _detach_cohort(row)
    return next((row for row in db.memory_cohorts.values() if row.id == cohort_id), None)


def _detach_cohort(row):
    return SimpleNamespace(
        id=row.id,
        slug=row.slug,
        name=row.name,
        role=row.role,
        seat_cap=row.seat_cap,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        pilot_tier=row.pilot_tier,
        post_coupon_key=row.post_coupon_key,
        active=row.active,
    )


def _save_application(row) -> None:
    if db.configured():
        with db.session_scope() as session:
            session.add(
                PilotApplication(
                    id=row.id,
                    cohort_slug=row.cohort_slug,
                    name=row.name,
                    email=row.email,
                    role=row.role,
                    categories=row.categories,
                    contact=row.contact,
                    status=row.status,
                    founding_number=row.founding_number,
                    attribution=row.attribution,
                    age_confirmed=row.age_confirmed,
                    marketing_consent=row.marketing_consent,
                    email_marketing_consent=row.email_marketing_consent,
                    business_name=row.business_name,
                    abn=row.abn,
                    authority_confirmed=row.authority_confirmed,
                    created_at=row.created_at,
                )
            )
        return
    db.memory_applications.append(row)


def _save_membership(row) -> None:
    if db.configured():
        with db.session_scope() as session:
            session.add(
                PilotMembership(
                    id=row.id,
                    cohort_id=row.cohort_id,
                    application_id=row.application_id,
                    invite_id=row.invite_id,
                    email=row.email,
                    role=row.role,
                    shop_id=row.shop_id,
                    creator_id=row.creator_id,
                    stage=row.stage,
                    founding_number=row.founding_number,
                    invited_at=row.invited_at,
                    created_at=row.created_at,
                )
            )
        return
    db.memory_memberships.append(row)


def _applications():
    if db.configured():
        with db.session_scope() as session:
            return [
                SimpleNamespace(**{column: getattr(row, column) for column in (
                    "id", "cohort_slug", "name", "email", "role", "categories", "contact", "status",
                    "founding_number", "attribution", "age_confirmed", "marketing_consent", "created_at", "reviewed_at",
                )})
                for row in session.scalars(select(PilotApplication).order_by(PilotApplication.created_at.desc())).all()
            ]
    return list(db.memory_applications)


def _application(application_id: str):
    if db.configured():
        with db.session_scope() as session:
            row = session.get(PilotApplication, application_id)
            if row is None:
                return None
            return row
    return next((row for row in db.memory_applications if row.id == application_id), None)


def _application_by_email(slug: str, email: str):
    return next((row for row in _applications() if _get(row, "cohort_slug") == slug and _get(row, "email") == email), None)


def _memberships():
    if db.configured():
        with db.session_scope() as session:
            return list(session.scalars(select(PilotMembership)).all())
    return list(db.memory_memberships)


def _membership_for_invite(invite_id: str):
    if db.configured():
        with db.session_scope() as session:
            return session.scalar(select(PilotMembership).where(PilotMembership.invite_id == invite_id))
    return next((row for row in db.memory_memberships if row.invite_id == invite_id), None)


def _visits():
    if db.configured():
        with db.session_scope() as session:
            return list(session.scalars(select(PilotVisit)).all())
    return list(db.memory_visits)


def _invite(invite_id: Optional[str]):
    if not invite_id:
        return None
    if db.configured():
        with db.session_scope() as session:
            return session.get(PilotInvite, invite_id)
    return next((row for row in db.memory_invites if row.id == invite_id), None)


def _invite_by_code(code: str):
    if db.configured():
        with db.session_scope() as session:
            return session.scalar(select(PilotInvite).where(PilotInvite.code == code))
    return next((row for row in db.memory_invites if row.code == code), None)


def _survey_count() -> int:
    if db.configured():
        with db.session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(PilotSurvey)) or 0)
    return len(db.memory_surveys)


def _public_application(row) -> dict:
    return {
        "id": _get(row, "id"),
        "cohort": _get(row, "cohort_slug"),
        "name": _get(row, "name"),
        "role": _get(row, "role"),
        "status": _get(row, "status"),
        "founding_number": _get(row, "founding_number"),
    }


def _get(row, name):
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name)


def _set(row, name, value) -> None:
    if db.configured() and not isinstance(row, SimpleNamespace):
        _update_db(row, name, value)
        return
    setattr(row, name, value)


def _update_db(row, name, value) -> None:
    model = type(row)
    if model is SimpleNamespace:
        setattr(row, name, value)
        return
    with db.session_scope() as session:
        current = session.get(model, row.id)
        if current is not None:
            setattr(current, name, value)
        setattr(row, name, value)
