"""Scheduled pilot-ending and renewal reminders.

Stripe's trial_will_end fires about 3 days ahead, which is later than the terms
promise. This job works out every reminder from the app's own records:

- pilot_end: every signed-up pilot member, sent by 6 Jan 2027 (PILOT_REMINDER_AT).
- first_charge: 7 days before the first charge after any free period (trial_end).
- annual_renewal: 14 days before an annual plan renews (current_period_end).
- discount_end: 14 days before PILOTGROWTH, FOUNDR9 or another discount ends.

Each reminder has a stable reminder_key. reminder_sends stores one row per key,
so a reminder is sent once even when the job runs on several replicas or many
times a day. A reminder that could not be sent (no provider yet, or an error) is
retried on later runs until the date it warns about has passed.

Run it with POST /admin/api/reminders/run, `python -m reminders`, or the hourly
loop that starts with the app when REMINDERS_LOOP is not "off".
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import billing_config
import db
import mailer
from models import BillingSubscription, ReminderSend, utcnow

logger = logging.getLogger("plantr.reminders")

FIRST_CHARGE_LEAD = timedelta(days=7)
RENEWAL_LEAD = timedelta(days=14)
DISCOUNT_LEAD = timedelta(days=14)
LIVE = {"active", "trialing", "past_due"}
RETRY = {mailer.NO_PROVIDER, mailer.FAILED, "claimed"}
LOOP_SECONDS = 3600


@dataclass
class Reminder:
    key: str
    kind: str
    template: str
    to_email: str
    due_at: datetime
    event_at: datetime
    values: dict = field(default_factory=dict)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _day(moment: datetime) -> str:
    return billing_config.sydney_date_label(moment)


def pilot_reminders(memberships: Iterable) -> list[Reminder]:
    ends = billing_config.pilot_end()
    due = billing_config.pilot_reminder_at()
    found = []
    for member in memberships:
        email = _get(member, "email")
        if not email or _get(member, "signed_up_at") is None:
            continue
        role = _get(member, "role")
        if role == "seller":
            offer = "If you continue on Growth you pay A$39.50 a month ex GST for 3 months, then A$79 a month ex GST. "
        else:
            offer = "If you continue on Curatr you pay A$9 a month ex GST for 12 months with code FOUNDR9, then A$19 a month ex GST. "
        found.append(
            Reminder(
                key=f"pilot_end:{_get(member, 'id')}:{ends.date().isoformat()}",
                kind="pilot_end",
                template="pilot_ending",
                to_email=email,
                due_at=due,
                event_at=ends,
                values={"ends": _day(ends), "offer": offer},
            )
        )
    return found


def subscription_reminders(row: dict, email: str) -> list[Reminder]:
    """Reminders for one billing row. Keys include the date, so a new period gets a new reminder."""
    if not email:
        return []
    status = str(row.get("status") or row.get("subscription_status") or "").lower()
    if status not in LIVE:
        return []
    role = row.get("role") or "seller"
    tier = row.get("plan_tier") or ""
    interval = row.get("interval") or row.get("billing_interval") or "month"
    spec = billing_config.tier_spec(role, tier)
    if not spec:
        return []
    unit = "year" if interval == "year" else "month"
    full_cents = spec["amount_year_cents"] if unit == "year" else spec["amount_month_cents"]
    sub_id = row.get("stripe_subscription_id") or row.get("id") or ""
    code = str(row.get("promo_code") or "")
    code_spec = billing_config.promo_spec(code)
    found = []
    trial_end = _aware(row.get("trial_end"))
    if status == "trialing" and trial_end is not None:
        first_cents = code_spec.get("discounted_month_cents") if code_spec and unit == "month" else full_cents
        charge_values = {"charge_date": _day(trial_end), "amount": billing_config.amount_label(first_cents), "plan": spec["label"], "unit": unit}
        found.append(
            Reminder(
                key=f"first_charge:{sub_id}:{trial_end.date().isoformat()}",
                kind="first_charge",
                template="first_charge_7_days",
                to_email=email,
                due_at=trial_end - FIRST_CHARGE_LEAD,
                event_at=trial_end,
                values=charge_values,
            )
        )
        found.append(
            Reminder(
                key=f"first_charge_2d:{sub_id}:{trial_end.date().isoformat()}",
                kind="first_charge_2d",
                template="first_charge_2_days",
                to_email=email,
                due_at=trial_end - timedelta(days=2),
                event_at=trial_end,
                values=charge_values,
            )
        )
    period_end = _aware(row.get("current_period_end"))
    if unit == "year" and status in {"active", "past_due"} and period_end is not None:
        found.append(
            Reminder(
                key=f"annual_renewal:{sub_id}:{period_end.date().isoformat()}",
                kind="annual_renewal",
                template="annual_renewal_14_days",
                to_email=email,
                due_at=period_end - RENEWAL_LEAD,
                event_at=period_end,
                values={"renews": _day(period_end), "amount": billing_config.amount_label(full_cents), "plan": spec["label"]},
            )
        )
    discount_end = _aware(row.get("discount_end"))
    if discount_end is not None:
        found.append(
            Reminder(
                key=f"discount_end:{sub_id}:{discount_end.date().isoformat()}",
                kind="discount_end",
                template="discount_ending_14_days",
                to_email=email,
                due_at=discount_end - DISCOUNT_LEAD,
                event_at=discount_end,
                values={
                    "ends": _day(discount_end),
                    "code": code or "your",
                    "plan": spec["label"],
                    "amount": billing_config.amount_label(full_cents),
                    "unit": unit,
                },
            )
        )
        if code == "PILOTGROWTH":
            step_values = {"ends": _day(discount_end), "amount": billing_config.amount_label(full_cents)}
            found.append(
                Reminder(
                    key=f"growth_step_7d:{sub_id}:{discount_end.date().isoformat()}",
                    kind="growth_step_7d",
                    template="growth_step_7_days",
                    to_email=email,
                    due_at=discount_end - timedelta(days=7),
                    event_at=discount_end,
                    values=step_values,
                )
            )
            found.append(
                Reminder(
                    key=f"growth_step_2d:{sub_id}:{discount_end.date().isoformat()}",
                    kind="growth_step_2d",
                    template="growth_step_2_days",
                    to_email=email,
                    due_at=discount_end - timedelta(days=2),
                    event_at=discount_end,
                    values=step_values,
                )
            )
    return found


def all_reminders() -> list[Reminder]:
    import pilots
    import stripe_billing

    found = pilot_reminders(pilots._memberships())
    for row in _billing_rows():
        email = stripe_billing._portal_email(row["role"], row["subject_id"]) or ""
        found.extend(subscription_reminders(row, email))
    return found


def run(now: Optional[datetime] = None, *, sender: Optional[mailer.Mailer] = None, reminders: Optional[list] = None) -> dict:
    """Send every reminder that is due and not yet sent. Safe to call any number of times."""
    now = _aware(now) or utcnow()
    sender = sender or mailer.default()
    summary = {"due": 0, "sent": 0, "no_provider": 0, "failed": 0, "skipped": 0}
    for reminder in reminders if reminders is not None else all_reminders():
        if not (reminder.due_at <= now < reminder.event_at):
            continue
        summary["due"] += 1
        if not _claim(reminder):
            summary["skipped"] += 1
            continue
        import emailer

        result = emailer.send(reminder.template, reminder.to_email, _mailer=sender, **reminder.values)
        status = result.get("status") or mailer.FAILED
        _finish(reminder.key, status, result.get("provider_id"))
        summary[status if status in summary else "failed"] += 1
    if summary["no_provider"]:
        logger.warning("%s reminders are due but RESEND_API_KEY is not set; they will retry", summary["no_provider"])
    if now.day == 1 and db.configured():
        try:
            import statements_mail

            statements_mail.run_month(now.strftime("%Y-%m"))
        except Exception:
            logger.exception("monthly statements")
    return summary


def preview(now: Optional[datetime] = None) -> list[dict]:
    now = _aware(now) or utcnow()
    rows = []
    for reminder in all_reminders():
        if reminder.event_at <= now:
            continue
        rows.append(
            {
                "key": reminder.key,
                "kind": reminder.kind,
                "due_at": reminder.due_at.isoformat(),
                "event_at": reminder.event_at.isoformat(),
                "status": _status(reminder.key),
            }
        )
    return sorted(rows, key=lambda item: item["due_at"])


async def loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run)
        except Exception:
            logger.exception("reminder run failed")
        await asyncio.sleep(LOOP_SECONDS)


def loop_enabled() -> bool:
    return os.getenv("REMINDERS_LOOP", "on").strip().lower() not in {"off", "0", "false", "no"}


def _claim(reminder: Reminder) -> bool:
    now = utcnow()
    if db.configured():
        try:
            with db.session_scope() as session:
                session.add(
                    ReminderSend(
                        id=str(uuid.uuid4()),
                        reminder_key=reminder.key,
                        kind=reminder.kind,
                        to_email=reminder.to_email,
                        due_at=reminder.due_at,
                        event_at=reminder.event_at,
                        status="claimed",
                        attempts=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush()
            return True
        except IntegrityError:
            with db.session_scope() as session:
                row = session.scalar(select(ReminderSend).where(ReminderSend.reminder_key == reminder.key))
                if row is None or row.status not in RETRY:
                    return False
                if row.status == "claimed" and _aware(row.updated_at) > now - timedelta(minutes=15):
                    return False
                row.status = "claimed"
                row.attempts = (row.attempts or 0) + 1
                row.updated_at = now
                return True
    stored = db.memory_reminder_sends.get(reminder.key)
    if stored is not None and stored["status"] not in RETRY:
        return False
    db.memory_reminder_sends[reminder.key] = {
        "kind": reminder.kind,
        "to_email": reminder.to_email,
        "due_at": reminder.due_at,
        "event_at": reminder.event_at,
        "status": "claimed",
        "attempts": (stored or {}).get("attempts", 0) + 1,
        "updated_at": now,
    }
    return True


def _finish(key: str, status: str, provider_id: Optional[str]) -> None:
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(ReminderSend).where(ReminderSend.reminder_key == key))
            if row is not None:
                row.status = status
                row.provider_id = provider_id
                row.updated_at = utcnow()
        return
    stored = db.memory_reminder_sends.get(key)
    if stored is not None:
        stored.update(status=status, provider_id=provider_id, updated_at=utcnow())


def _status(key: str) -> Optional[str]:
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(ReminderSend).where(ReminderSend.reminder_key == key))
            return None if row is None else row.status
    stored = db.memory_reminder_sends.get(key)
    return None if stored is None else stored["status"]


def _billing_rows() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(BillingSubscription)).all()
            return [
                {
                    "role": row.role,
                    "subject_id": row.shop_id if row.role == "seller" else row.creator_id,
                    "stripe_subscription_id": row.stripe_subscription_id,
                    "id": row.id,
                    "status": row.status,
                    "plan_tier": row.plan_tier,
                    "interval": row.interval,
                    "trial_end": row.trial_end,
                    "current_period_end": row.current_period_end,
                    "discount_end": row.discount_end,
                    "promo_code": row.promo_code,
                }
                for row in rows
            ]
    return [dict(row) for row in db.memory_billing]


def _get(row, name):
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
