"""Sample requests, address visibility, and the post due date."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import samples


def _product():
    return {"id": "prod-1", "title": "Stoneware bowl", "attributes": {"samples_enabled": True}}


def _request():
    return samples.request_sample(
        shop_id="shop-1",
        creator_id="cre-1",
        product=_product(),
        note="Unboxing, then an honest review.",
        address={"address_line": "1 Example St", "suburb": "Sydney", "region": "NSW", "postcode": "2000"},
    )


def test_address_is_hidden_until_approval_and_removed_30_days_after_delivery():
    row = _request()
    assert samples.address_for(row, viewer="supplier") == ""
    assert "1 Example St" in samples.address_for(row, viewer="creator")
    approved = samples.decide(row["id"], "shop-1", approved=True)
    assert "2000" in samples.address_for(approved, viewer="supplier")
    shipped = samples.ship(row["id"], "shop-1", tracking="TRACK1")
    delivered = samples.mark_delivered(row["id"], actor_creator="cre-1")
    assert delivered["post_due_on"] == delivered["delivered_at"].date() + timedelta(days=14)
    later = delivered["delivered_at"] + timedelta(days=31)
    refreshed = samples.refresh(delivered, now=later)
    assert refreshed["address_redacted"] is True
    assert samples.address_for(refreshed, viewer="supplier", now=later) == ""


def test_due_window_reminders_overdue_and_disclosure():
    row = _request()
    samples.decide(row["id"], "shop-1", approved=True)
    samples.ship(row["id"], "shop-1")
    samples.save_settings("shop-1", max_per_month=5, post_due_days=3)
    delivered = samples.mark_delivered(row["id"], actor_shop="shop-1")
    assert (delivered["post_due_on"] - delivered["delivered_at"].date()).days == 7
    samples.save_settings("shop-1", max_per_month=5, post_due_days=40)
    assert samples.due_days(40) == 30
    week = delivered["delivered_at"] + timedelta(days=7)
    # due is delivered+7, so 7 days before due is the delivery day. Use a 14 day window.
    samples.save_settings("shop-1", max_per_month=5, post_due_days=14)
    delivered["post_due_on"] = samples.post_due(delivered["delivered_at"], 14)
    seven = datetime.combine(delivered["post_due_on"] - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
    reminded = samples.refresh(delivered, now=seven, email="ada@example.com")
    assert samples.reminder_kind(reminded, now=seven) == "7"
    assert reminded["reminder_7_sent"] is True
    two = datetime.combine(delivered["post_due_on"] - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    reminded = samples.refresh(reminded, now=two, email="ada@example.com")
    assert samples.reminder_kind(reminded, now=two) == "2"
    late = datetime.combine(delivered["post_due_on"] + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    overdue = samples.refresh(reminded, now=late)
    assert samples.effective_status(overdue, now=late) == "overdue"
    with pytest.raises(HTTPException):
        samples.submit_post(row["id"], "cre-1", links="https://example.com/post", disclosure=False)
    posted = samples.submit_post(row["id"], "cre-1", links="https://example.com/post", disclosure=True)
    assert posted["status"] == "posted"
    with pytest.raises(HTTPException):
        samples.decide(row["id"], "other-shop", approved=True)


def test_supplier_cannot_read_another_creators_request():
    row = _request()
    assert samples.list_for_creator("someone-else") == []
    assert samples.address_for(row, viewer="supplier") == ""
