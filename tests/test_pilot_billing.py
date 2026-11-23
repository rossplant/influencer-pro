"""Pilot codes, the Curatr waiver, checkout disclosure, reminders, consent and sign-up records (v0.17.0)."""

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import billing_config
import db
import legal
import mailer
import main
import pilots
import reminders
import site_copy
import stripe_billing
from tests.test_stripe import FakeStripe, _Obj

client = TestClient(main.app)
UTC = timezone.utc
PILOT_END = datetime(2027, 1, 20, 1, 0, tzinfo=UTC)  # 20 Jan 2027 12:00 AEDT


# ---------------------------------------------------------------- codes


@pytest.mark.parametrize(
    "role,account,tier,interval,expected",
    [
        ("creator", {"signup_campaign": "founding_curators"}, "curator", "month", "FOUNDR9"),
        ("creator", {"founding_number": 7}, "curator", "month", "FOUNDR9"),
        ("creator", {"signup_campaign": "founding_curators"}, "curator", "year", ""),
        ("creator", {"signup_campaign": "founding_curators"}, "member", "month", ""),
        ("creator", {"signup_campaign": "founding_curators"}, "elite", "month", ""),
        ("creator", {}, "curator", "month", ""),
        ("seller", {"signup_campaign": "seller_pilot"}, "growth", "month", "PILOTGROWTH"),
        ("seller", {"is_pilot": True}, "growth", "month", "PILOTGROWTH"),
        ("seller", {"signup_campaign": "seller_pilot"}, "growth", "year", ""),
        ("seller", {"signup_campaign": "seller_pilot"}, "scale", "month", ""),
        ("seller", {}, "growth", "month", ""),
        ("seller", {"signup_campaign": "founding_curators"}, "growth", "month", ""),
    ],
)
def test_post_pilot_code_is_automatic_only_for_the_right_plan(role, account, tier, interval, expected):
    assert billing_config.pilot_offer_code(role, account, tier, interval) == expected


def test_foundr9_is_a10_off_monthly_curatr_for_12_months_and_founding50_is_gone():
    coupon = billing_config.COUPONS[billing_config.PROMOTION_CODES["FOUNDR9"]["coupon"]]
    curatr = billing_config.CREATOR_TIERS["curator"]["amount_month_cents"]
    assert curatr - coupon["amount_off"] == 900
    assert coupon["duration"] == "repeating" and coupon["duration_in_months"] == 12
    assert coupon["applies_to"] == [("creator", "curator")]
    pilot = billing_config.COUPONS[billing_config.PROMOTION_CODES["PILOTGROWTH"]["coupon"]]
    growth = billing_config.SELLER_TIERS["growth"]["amount_month_cents"]
    assert growth * (100 - pilot["percent_off"]) // 100 == 3950
    assert "FOUNDING50" not in billing_config.PROMOTION_CODES
    assert "FOUNDING50" in billing_config.RETIRED_PROMOTION_CODES


def test_checkout_disclosure_matches_the_acceptance_wording():
    end = int(PILOT_END.timestamp())
    seller = stripe_billing.checkout_disclosure("seller", "growth", "month", offer="PILOTGROWTH", trial_end=end)
    assert seller == (
        "No charge today. Your first charge of A$39.50 (ex GST) will be on 20 January 2027. "
        "You will pay A$39.50 a month for 3 months, then A$79 a month from 20 April 2027, "
        "renewing automatically until you cancel. Cancel before 20 January 2027 and you will not be charged. "
        "The pilot is at no charge until 20 January 2027 unless cancelled."
    )
    foundr = stripe_billing.checkout_disclosure("creator", "curator", "month", offer="FOUNDR9", trial_end=end)
    assert foundr == (
        "No charge today. Code FOUNDR9 applied. Your first charge of A$9 (ex GST) will be on 20 January 2027. "
        "You will pay A$9 a month for 12 months, then A$19 a month from 20 January 2028, "
        "renewing automatically until you cancel. Cancel before 20 January 2027 and you will not be charged. "
        "The pilot is at no charge until 20 January 2027 unless cancelled."
    )
    other = stripe_billing.checkout_disclosure("creator", "elite", "year", trial_end=end)
    assert other.startswith("No charge today. Your first charge of A$490 (ex GST) will be on 20 January 2027.")
    assert stripe_billing.checkout_disclosure("seller", "scale", "year").startswith(
        "You are subscribing to Scale at A$2,290 per year, excluding GST."
    )


# ---------------------------------------------------------------- schedule


class FakeSchedules:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, payload):
        self.created.append(payload)
        return _Obj(
            id="sub_sched_1",
            phases=[
                _Obj(
                    start_date=1763600000,
                    end_date=int(PILOT_END.timestamp()),
                    items=[_Obj(price=_Obj(id="price_curatr_month"), quantity=1)],
                )
            ],
        )

    def update(self, schedule_id, payload):
        self.updated.append((schedule_id, payload))
        return _Obj(id=schedule_id)


def _trialing_subscription(code="FOUNDR9", **extra):
    metadata = {"role": "creator", "subject_id": "c1", "tier": "curator", "interval": "month", "promo_code": code}
    if code:
        metadata.update(pilot_offer=code, pilot_promotion_code="promo_foundr9")
    return {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "trialing",
        "trial_end": int(PILOT_END.timestamp()),
        "start_date": 1763600000,
        "schedule": None,
        "metadata": metadata,
        **extra,
    }


def test_pilot_schedule_puts_the_code_after_the_free_period(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    fake.subscription_schedules = FakeSchedules()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    result = stripe_billing.ensure_pilot_schedule(_trialing_subscription())
    assert result["scheduled"] is True
    assert fake.subscription_schedules.created == [{"from_subscription": "sub_1"}]
    schedule_id, payload = fake.subscription_schedules.updated[0]
    assert schedule_id == "sub_sched_1"
    assert payload["end_behavior"] == "release"
    free, discounted, full = payload["phases"]
    assert free["end_date"] == free["trial_end"] == int(PILOT_END.timestamp())
    assert "discounts" not in free
    assert discounted["duration"] == {"interval": "month", "interval_count": 12}
    assert discounted["discounts"] == [{"promotion_code": "promo_foundr9"}]
    assert "discounts" not in full
    assert all(phase["items"] == [{"price": "price_curatr_month", "quantity": 1}] for phase in payload["phases"])
    assert result["discount_end"].startswith("2028-01-20")


def test_pilot_schedule_is_idempotent_and_skips_non_pilot_subscriptions(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    fake.subscription_schedules = FakeSchedules()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    assert stripe_billing.ensure_pilot_schedule(_trialing_subscription(schedule="sub_sched_old"))["existing"] is True
    assert stripe_billing.ensure_pilot_schedule(_trialing_subscription(code=""))["scheduled"] is False
    assert stripe_billing.ensure_pilot_schedule(_trialing_subscription(status="active"))["scheduled"] is False
    assert fake.subscription_schedules.created == []


def test_subscription_created_webhook_attaches_the_schedule(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    fake.subscription_schedules = FakeSchedules()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    db.memory_creator_objects["c1"] = SimpleNamespace(id="c1", signup_campaign="founding_curators")
    stripe_billing.apply_event({"id": "evt_1", "type": "customer.subscription.created", "data": {"object": _trialing_subscription()}})
    assert len(fake.subscription_schedules.created) == 1
    row = next(row for row in db.memory_billing if row.get("stripe_subscription_id") == "sub_1")
    assert row["trial_end"] == PILOT_END
    assert row["discount_end"] == datetime(2028, 1, 20, 1, 0, tzinfo=UTC)
    assert row["promo_code"] == "FOUNDR9"


# ---------------------------------------------------------------- waiver


class FakeInvoices:
    def __init__(self, amount):
        self.amount = amount
        self.previews = []

    def create_preview(self, params):
        self.previews.append(params)
        return _Obj(amount_due=self.amount)


class FakeBalance:
    def __init__(self):
        self.created = []

    def create(self, customer_id, params, options=None):
        self.created.append((customer_id, params, options))
        return _Obj(id="cbtxn_1")


def _waiver_creator(monkeypatch, tier, amount=900):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    fake = FakeStripe()
    fake.invoices = FakeInvoices(amount)
    fake.customers.balance_transactions = FakeBalance()
    monkeypatch.setattr(stripe_billing, "client", lambda: fake)
    db.memory_creator_objects["w1"] = SimpleNamespace(
        id="w1",
        plan_tier=tier,
        subscription_status="active",
        stripe_customer_id="cus_w1",
        stripe_subscription_id="sub_w1",
        signup_campaign="founding_curators",
    )
    now = datetime.now(UTC)
    for index in range(5):
        db.memory_attributed_orders.append(SimpleNamespace(creator_id="w1", attributed=True, occurred_at=now - timedelta(minutes=index)))
    return fake


@pytest.mark.parametrize("tier", ["member", "elite"])
def test_waiver_is_curatr_only(monkeypatch, tier):
    fake = _waiver_creator(monkeypatch, tier)
    with pytest.raises(HTTPException) as caught:
        stripe_billing.apply_sale_waiver("w1")
    assert caught.value.status_code == 422
    assert "Curatr only" in caught.value.detail
    assert fake.customers.balance_transactions.created == []


def test_waiver_stacks_with_foundr9_instead_of_replacing_it(monkeypatch):
    fake = _waiver_creator(monkeypatch, "curator", amount=900)
    result = stripe_billing.apply_sale_waiver("w1")
    assert result["applied"] is True and result["credit_cents"] == 900 and result["stacks_with_discount"] is True
    customer, params, options = fake.customers.balance_transactions.created[0]
    assert customer == "cus_w1"
    assert params["amount"] == -900 and params["currency"] == "aud"
    assert options["idempotency_key"].startswith("curatr-waiver-w1-")
    # The subscription's discounts (FOUNDR9) are never touched.
    assert fake.subscriptions.updated == []


def test_waiver_needs_five_sales(monkeypatch):
    _waiver_creator(monkeypatch, "curator")
    db.memory_attributed_orders.pop()
    with pytest.raises(HTTPException):
        stripe_billing.apply_sale_waiver("w1")


# ---------------------------------------------------------------- reminders


class RecordingMailer:
    def __init__(self, status=mailer.SENT):
        self.status = status
        self.sent = []

    def send(self, *, to, subject, text, html=None, idempotency_key=None):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return {"status": self.status, "id": f"re_{len(self.sent)}" if self.status == mailer.SENT else None}


def _member(**extra):
    return SimpleNamespace(id="m1", email="pilot@example.com", role="seller", signed_up_at=datetime(2026, 11, 21, tzinfo=UTC), **extra)


def test_pilot_end_reminder_is_due_by_6_january_2027():
    [reminder] = reminders.pilot_reminders([_member(), SimpleNamespace(id="m2", email="x@example.com", role="creator", signed_up_at=None)])
    assert reminder.due_at == datetime(2027, 1, 4, 23, 0, tzinfo=UTC)  # 5 Jan 2027 10:00 AEDT
    assert reminder.due_at < datetime(2027, 1, 6, tzinfo=UTC)
    assert reminder.event_at == PILOT_END
    assert reminder.values["ends"] == "20 January 2027"


def test_subscription_reminder_dates():
    trialing = {
        "role": "seller",
        "stripe_subscription_id": "sub_p",
        "status": "trialing",
        "plan_tier": "growth",
        "interval": "month",
        "trial_end": PILOT_END,
        "discount_end": datetime(2027, 4, 20, 1, 0, tzinfo=UTC),
        "promo_code": "PILOTGROWTH",
    }
    found = {item.kind: item for item in reminders.subscription_reminders(trialing, "s@example.com")}
    assert found["first_charge"].due_at == PILOT_END - timedelta(days=7)
    assert found["first_charge"].values["amount"] == "A$39.50"
    assert found["discount_end"].due_at == datetime(2027, 4, 6, 1, 0, tzinfo=UTC)
    assert found["discount_end"].values["amount"] == "A$79"
    assert "annual_renewal" not in found
    annual = {
        "role": "creator",
        "stripe_subscription_id": "sub_a",
        "status": "active",
        "plan_tier": "curator",
        "interval": "year",
        "current_period_end": datetime(2027, 12, 1, tzinfo=UTC),
    }
    [renewal] = reminders.subscription_reminders(annual, "a@example.com")
    assert renewal.kind == "annual_renewal"
    assert renewal.due_at == datetime(2027, 11, 17, tzinfo=UTC)
    assert renewal.values["amount"] == "A$190"
    assert reminders.subscription_reminders({**annual, "status": "canceled"}, "a@example.com") == []


def test_reminder_run_is_idempotent_and_retries_without_a_provider():
    [reminder] = reminders.pilot_reminders([_member()])
    before = reminder.due_at - timedelta(hours=1)
    sender = RecordingMailer()
    assert reminders.run(before, sender=sender, reminders=[reminder])["due"] == 0
    quiet = RecordingMailer(status=mailer.NO_PROVIDER)
    first = reminders.run(reminder.due_at, sender=quiet, reminders=[reminder])
    assert first["no_provider"] == 1
    later = reminder.due_at + timedelta(hours=1)
    assert reminders.run(later, sender=sender, reminders=[reminder])["sent"] == 1
    assert reminders.run(later + timedelta(hours=1), sender=sender, reminders=[reminder])["skipped"] == 1
    assert len(sender.sent) == 1
    assert "20 January 2027" in sender.sent[0]["subject"]
    assert "A$39.50" in sender.sent[0]["text"]
    stale = reminders.pilot_reminders([SimpleNamespace(id="m9", email="late@example.com", role="seller", signed_up_at=datetime(2026, 12, 1, tzinfo=UTC))])
    assert reminders.run(PILOT_END + timedelta(minutes=1), sender=sender, reminders=stale)["due"] == 0


def test_resend_adapter_is_a_logged_no_op_without_a_key(monkeypatch, caplog):
    monkeypatch.setattr(mailer, "_warned", False)
    with caplog.at_level(logging.WARNING, logger="plantr.mailer"):
        result = mailer.ResendMailer.from_env().send(to="a@example.com", subject="s", text="t")
    assert result == {"status": mailer.NO_PROVIDER, "id": None}
    assert "RESEND_API_KEY is not set" in caplog.text
    import emailer

    out = emailer.send("first_charge_7_days", "a@example.com", charge_date="20 January 2027", amount="A$9", plan="Curatr", unit="month")
    assert out["sent"] is False and out["status"] == mailer.NO_PROVIDER


# ---------------------------------------------------------------- site


def test_no_attribution_cookie_until_consent_then_set_with_the_utms():
    fresh = TestClient(main.app)
    page = fresh.get("/pilot-sellers?utm_source=tiktok&utm_campaign=pilot")
    assert "plantr_attr" not in page.headers.get("set-cookie", "")
    denied = fresh.post("/track/consent", json={"consent": False, "query": "utm_source=tiktok"})
    assert "plantr_attr=;" in denied.headers.get("set-cookie", "") or "plantr_attr=\"\"" in denied.headers.get("set-cookie", "")
    allowed = fresh.post("/track/consent", json={"consent": True, "query": "utm_source=tiktok&utm_campaign=pilot"})
    cookies = allowed.headers.get_list("set-cookie")
    attr = next(value for value in cookies if value.startswith("plantr_attr="))
    assert "tiktok" in attr and "HttpOnly" in attr


def test_public_copy_is_neutral_curatr_only_and_ex_gst():
    creators = client.get("/founding-curators").text
    sellers = client.get("/pilot-sellers").text
    assert "You've been selected" not in creators and "You&#x27;ve been selected" not in creators
    assert "every sale is tracked" not in sellers.lower()
    assert "every sale you drive" not in creators.lower()
    assert "Curatr only" in creators
    for text in (" ".join(site_copy.CREATOR_PAGE["points"]), " ".join(site_copy.SUPPLIER_PAGE["points"])):
        for match in re.finditer(r"A\$[\d,.]+", text):
            assert text[match.end():match.end() + 7] == " ex GST" or text[match.end():].startswith(" a month ex GST") or text[match.end():].startswith(" a year ex GST"), text[match.start():match.end() + 20]
    for page in (creators, sellers):
        assert "8 weeks" not in page and "90 days" not in page
        assert "20 November 2026" in page and "20 January 2027" in page
        assert "Test Operator Pty Ltd" in page


def test_applications_are_closed_until_the_operator_is_named(monkeypatch):
    monkeypatch.delenv("OPERATOR_LEGAL_NAME")
    page = client.get("/pilot-sellers").text
    assert "opening soon" in page.lower()
    assert "/pilot-sellers/apply" not in page
    response = client.post("/pilot-sellers/apply", data={"csrf_token": "x"})
    assert response.status_code in (403, 503)
    with pytest.raises(HTTPException) as caught:
        pilots.apply("seller_pilot", name="A", email="a@example.com", role="seller", categories="", contact="", age_confirmed=False,
                     touch_state=None, consent=False, event_id="", paid_service_confirmed=True, authority_confirmed=True, business_name="B")
    assert caught.value.status_code == 503


def _seller_form(page, **extra):
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    data = {
        "csrf_token": token,
        "name": "Sam",
        "email": "sam@example.com",
        "role": "seller",
        "business_name": "Sam's Plants",
        "authority_confirmed": "yes",
        "age_confirmed": "yes",
        "date_of_birth": "1990-01-01",
        "paid_service_confirmed": "yes",
    }
    data.update(extra)
    return {key: value for key, value in data.items() if value is not None}


def test_seller_application_needs_authority_and_a_valid_abn_and_records_acceptance(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    page = client.get("/pilot-sellers").text
    assert "All users must be 18 or over" in page
    fee = "Plantr takes no cut of your sales. You pay Creatrs' commission directly."
    assert fee in legal.SELLER_PANEL
    assert html.escape(fee) in page
    assert "name='abn'" in page
    assert "email_consent" in page and "required> Send me" not in page
    assert client.post("/pilot-sellers/apply", data=_seller_form(page, age_confirmed=None)).status_code == 422
    page = client.get("/pilot-sellers").text
    assert client.post("/pilot-sellers/apply", data=_seller_form(page, abn="1234")).status_code == 422
    page = client.get("/pilot-sellers").text
    assert client.post("/pilot-sellers/apply", data=_seller_form(page, abn="51 824 753 557")).status_code == 422
    page = client.get("/pilot-sellers").text
    ok = client.post(
        "/pilot-sellers/apply",
        data=_seller_form(page, abn="51 824 753 556"),
        headers={"Cookie": "plantr_consent=marketing"},
    )
    assert ok.status_code == 200
    row = db.memory_applications[-1]
    assert row.abn == "51824753556" and row.authority_confirmed is True
    # Cookie consent is not email consent.
    assert row.marketing_consent is True and row.email_marketing_consent is False
    [record] = legal.acceptances(row.id)
    assert record["context"] == "apply:seller_pilot"
    assert record["terms_version"] == legal.TERMS_VERSION
    assert legal.SELLER_APPLY in record["text_shown"]
    assert record["accepted_at"].tzinfo is not None
    assert record["confirmations"]["email_marketing"] is False


def test_abn_checksum():
    assert legal.clean_abn("") is None
    assert legal.clean_abn("51 824 753 556") == "51824753556"
    for bad in ("5182475355", "51824753557", "abcdefghijk"):
        with pytest.raises(ValueError):
            legal.clean_abn(bad)


def test_join_needs_the_terms_boxes_and_stores_an_acceptance_record(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    pilots.ensure_cohorts()
    cohort = db.memory_cohorts["seller_pilot"]
    invite = pilots._new_invite(cohort, "join@example.com")
    page = client.get(f"/join/{invite.code}").text
    assert "Seller Pilot Agreement" in page and "terms_accepted" in page
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    base = {
        "csrf_token": token,
        "name": "Jo",
        "email": "join@example.com",
        "password": "a-long-password-1",
        "age_confirmed": "yes",
        "date_of_birth": "1990-01-01",
    }
    refused = client.post(f"/join/{invite.code}", data=base, follow_redirects=False)
    assert refused.status_code == 422
    token = client.get(f"/join/{invite.code}").text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    joined = client.post(
        f"/join/{invite.code}",
        data={**base, "csrf_token": token, "terms_accepted": "yes", "pilot_terms_accepted": "yes", "email_consent": "yes"},
        follow_redirects=False,
    )
    assert joined.status_code == 303
    shop_id = next(iter(db.memory_shops))
    assert db.memory_shops[shop_id].email_marketing_consent is True
    [record] = legal.acceptances(shop_id)
    assert record["context"] == "join:seller_pilot"
    assert "seller_pilot_agreement" in record["documents"]
    assert legal.EMAIL_CONSENT in record["text_shown"]


def test_reminder_log_and_acceptance_records_persist_in_the_database(tmp_path, monkeypatch):
    from tests.test_persistence import use_database

    use_database(tmp_path, monkeypatch)
    try:
        [reminder] = reminders.pilot_reminders([_member()])
        sender = RecordingMailer()
        assert reminders.run(reminder.due_at, sender=sender, reminders=[reminder])["sent"] == 1
        assert reminders.run(reminder.due_at, sender=sender, reminders=[reminder])["skipped"] == 1
        assert len(sender.sent) == 1
        assert reminders._status(reminder.key) == mailer.SENT
        legal.record(subject_type="shop", subject_id="s1", context="test", documents={"a": "1"}, texts=["t"])
        assert legal.acceptances("s1")[0]["context"] == "test"
    finally:
        monkeypatch.delenv("DATABASE_URL")
        main.init_storage()
