"""Every account holder must be 18 or over, with a date of birth and a confirmation."""

from datetime import date

import pytest
from fastapi import HTTPException

import creatr_age
import db
import pilots
import uat_mode


def test_rule_requires_both_and_blocks_under_18():
    assert "All users must be 18 or over" in creatr_age.RULE
    missing = creatr_age.check_signup(born=None, attested=True)
    assert missing.startswith("All users must be 18 or over")
    young = creatr_age.check_signup(born=date(2015, 1, 1), attested=True, on=date(2026, 11, 23))
    assert young == "All users must be 18 or over."
    assert creatr_age.check_signup(born=date(1990, 5, 2), attested=True, on=date(2026, 11, 23)) == ""


def test_supplier_application_blocks_under_18():
    with pytest.raises(HTTPException) as caught:
        pilots.apply(
            "seller_pilot",
            name="Sam",
            email="young-seller@example.com",
            role="seller",
            categories="home",
            contact="https://example.com",
            age_confirmed=True,
            date_of_birth=date(2010, 6, 1),
            touch_state=None,
            consent=False,
            event_id="age",
            paid_service_confirmed=True,
            authority_confirmed=True,
            business_name="Young Co",
        )
    assert caught.value.status_code == 422
    assert caught.value.detail == "All users must be 18 or over."


def test_uat_invite_blocks_under_18(monkeypatch):
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    uat_mode.create_set("partner@example.com")
    assert db.memory_emails == [] or all(row["template_key"] != "uat_invite" for row in db.memory_emails)
    token = __import__("auth").issue_form_token("uat-set:seller:partner@example.com")
    with pytest.raises(ValueError) as caught:
        uat_mode.activate_invite(token, born=date(2012, 1, 1), attested=True, password="a-long-password-1")
    assert "All users must be 18 or over" in str(caught.value)
    uat_mode.activate_invite(token, born=date(1992, 4, 4), attested=True, password="a-long-password-1")
    roles = {row.role for row in db.memory_portal_accounts if row.email == "partner@example.com"}
    assert "seller" in roles


def test_logged_out_terms_are_a_placeholder_and_staging_is_not_indexed(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    page = client.get("/terms/seller")
    assert page.status_code == 200
    assert "Draft — pending legal review" in page.text
    assert "NOT BINDING" not in page.text
    assert "A$29" not in page.text
    monkeypatch.setenv("ENV", "staging")
    staging = client.get("/privacy")
    assert staging.headers.get("x-robots-tag") == "noindex"
    robots = client.get("/robots.txt")
    assert "Disallow: /" in robots.text
    monkeypatch.setenv("ENV", "production")
    assert "x-robots-tag" not in client.get("/privacy").headers
    assert client.get("/robots.txt").status_code == 404


def test_story_frame_does_not_change_the_product():
    import listing_studio

    frame = listing_studio.story_frame({"id": "scene-1"})
    assert frame["width"] == 1080 and frame["height"] == 1920
    assert frame["product_changed"] is False
    assert frame["method"] == "crop_or_extend_background"
