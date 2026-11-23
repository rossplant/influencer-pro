"""Quality months, warnings, and admin exceptions.

A failed month is stored as an event. An admin exception stores a later event
that excuses that month. Nothing here changes match ranking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

import db
import emailer
import events
import recognition
from models import Event


def _props(row) -> dict:
    if isinstance(row, dict):
        return row
    return dict(getattr(row, "properties", None) or {})


def _rows(name: str) -> list:
    if not db.configured():
        return [row for row in db.memory_events if getattr(row, "event_name", None) == name]
    with db.session_scope() as session:
        found = session.scalars(select(Event).where(Event.event_name == name)).all()
        return [
            {
                "occurred_at": row.occurred_at,
                "properties": dict(row.properties or {}),
            }
            for row in found
        ]


def note_month(subject_type: str, subject_id: str, month: str, *, passed: bool, email: str = "") -> dict:
    """Record one calendar month and warn after the first failure in a streak."""
    if subject_type not in {"creator", "supplier"}:
        raise HTTPException(status_code=422, detail="subject_type must be creator or supplier")
    events.record_admin(
        "quality_month",
        subject_type=subject_type,
        subject_id=subject_id,
        month=month,
        passed=bool(passed),
    )
    standing = standing_for(subject_type, subject_id)
    if standing["warning"] and email:
        if subject_type == "supplier":
            import supplier_copy

            emailer.send(
                "supplier_badge_notice",
                email,
                notice=supplier_copy.badge_notice("Pays on time", False),
            )
        else:
            emailer.send(
                "quality_warning",
                email,
                month=month,
                standard=standing["quality_standard"],
            )
    return standing


def months_for(subject_type: str, subject_id: str) -> list[dict]:
    by_month = {}
    for row in _rows("quality_month"):
        props = _props(row)
        if props.get("subject_type") == subject_type and props.get("subject_id") == subject_id:
            by_month[props.get("month")] = {
                "month": props.get("month"),
                "passed": bool(props.get("passed")),
                "excused": False,
            }
    recorded = list(by_month.values())
    excused = {
        props.get("month")
        for row in _rows("quality_exception")
        for props in [_props(row)]
        if props.get("subject_type") == subject_type and props.get("subject_id") == subject_id
    }
    for row in recorded:
        if row["month"] in excused:
            row["excused"] = True
    return recorded


def standing_for(subject_type: str, subject_id: str, *, sales: int = 0, pilot_invite: bool = False, on=None) -> dict:
    role = "creator" if subject_type == "creator" else "supplier"
    return recognition.apply_quality(
        sales,
        months_for(subject_type, subject_id),
        pilot_invite=pilot_invite,
        on=on,
        role=role,
    )


def request_review(subject_type: str, subject_id: str, *, month: str, note: str = "") -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "month": month,
        "note": (note or "").strip(),
        "status": "open",
    }
    events.record_admin("quality_review_requested", **item)
    return item


def queue() -> list[dict]:
    open_items = []
    closed = set()
    for row in _rows("quality_exception"):
        props = _props(row)
        if props.get("review_id"):
            closed.add(props["review_id"])
    for row in _rows("quality_review_requested"):
        props = _props(row)
        if props.get("id") and props["id"] not in closed and props.get("status", "open") == "open":
            open_items.append(
                {
                    "id": props.get("id"),
                    "subject_type": props.get("subject_type"),
                    "subject_id": props.get("subject_id"),
                    "month": props.get("month"),
                    "note": props.get("note") or "",
                    "status": "open",
                }
            )
    return open_items


def excuse(
    *,
    admin_email: str,
    subject_type: str,
    subject_id: str,
    month: str,
    reason: str,
    note: str = "",
    restore: bool = False,
    review_id: str = "",
) -> dict:
    """Mark a failure as not their fault. Other requires a note. Writes an audit row."""
    if reason not in recognition.EXCUSE_REASONS:
        raise HTTPException(status_code=422, detail="reason is not one of the allowed reasons")
    cleaned = (note or "").strip()
    if reason == "other" and not cleaned:
        raise HTTPException(status_code=422, detail="A note is required when the reason is other")
    when = datetime.now(timezone.utc).isoformat()
    audit = {
        "who": admin_email,
        "when": when,
        "reason": reason,
        "note": cleaned,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "month": month,
        "restore": bool(restore),
        "review_id": review_id,
    }
    events.record_admin("quality_exception", **audit)
    if restore:
        for row in months_for(subject_type, subject_id):
            if row["passed"] or row["excused"] or row["month"] == month:
                continue
            events.record_admin(
                "quality_exception",
                who=admin_email,
                when=when,
                reason=reason,
                note=cleaned,
                subject_type=subject_type,
                subject_id=subject_id,
                month=row["month"],
                restore=True,
                review_id=review_id,
            )
    return audit
