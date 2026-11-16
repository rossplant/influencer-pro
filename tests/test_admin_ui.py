"""Operator pages: readable records, working links, and staging-only tools."""

import re
from datetime import datetime, timedelta, timezone
from html import unescape

from fastapi.testclient import TestClient

import db
import main
from test_api import INFLUENCER, PRODUCT, client
from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

import admin_format


def _session(monkeypatch):
    _setup(monkeypatch)
    return _cookie_header(_login())


def _seller_and_creator(headers):
    shop = client.post(
        "/shops",
        json={"name": "Acme Pottery", "primary_category": "home"},
        headers=headers,
    ).json()
    stored = db.memory_shops[shop["id"]]
    stored.business_name = "Acme Pottery"
    creator = client.post(
        "/onboard-influencer",
        json={**INFLUENCER, "handle": "@ada", "name": "Ada Lovelace", "active_categories": ["home"]},
        headers=headers,
    ).json()
    client.post("/enrich", json={**PRODUCT, "shop_id": shop["id"], "title": "Ceramic pourer"}, headers=headers)
    return shop, creator


def _csrf(page: str) -> str:
    return page.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]


def test_dates_money_and_badges():
    moment = datetime(2026, 11, 16, 9, 21, tzinfo=timezone.utc)
    assert admin_format.format_when(moment) == "16 Nov 2026, 8:21 pm AEDT"
    earlier = moment - timedelta(days=3)
    assert admin_format.relative_age(earlier, now=moment) == "3 days ago"
    assert admin_format.money(12.5, "AUD", kind="ex GST") == "A$12.50 ex GST"
    assert "Payment overdue" in admin_format.status_label("past_due")
    assert "past_due" not in admin_format.status_label("past_due")
    assert admin_format.status_label("no_longer_true") == "Claim removed"
    assert admin_format.plan_name("creator", "member") == "Membr"
    assert admin_format.humanize("rules-v1") == "Matching rules, version 1"


def test_pages_are_readable_and_links_resolve(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1000")
    headers = _session(monkeypatch)
    shop, creator = _seller_and_creator(headers)
    db.memory_shops[shop["id"]].subscription_status = "past_due"

    dashboard = client.get("/admin", headers=headers)
    assert dashboard.status_code == 200
    assert "Needs attention" in dashboard.text
    assert "Payment overdue for Acme Pottery" in dashboard.text
    assert "Create login" in dashboard.text
    assert "View as" in dashboard.text
    assert "STAGING" in dashboard.text
    assert "Test accounts" not in dashboard.text
    assert "past_due" not in dashboard.text
    assert "<pre>" not in dashboard.text

    sellers = client.get("/admin/sellers", headers=headers)
    assert "Acme Pottery" in sellers.text
    assert "No sellers yet" not in sellers.text
    assert f"<td>{shop['id']}</td>" not in sellers.text

    seller = client.get(f"/admin/sellers/{shop['id']}", headers=headers)
    assert "<h1>Acme Pottery</h1>" in seller.text
    assert "Payment overdue" in seller.text
    assert "Create login for this seller" in seller.text
    assert "Technical details" in seller.text
    assert "Starter" in seller.text
    assert "past_due" not in seller.text
    assert seller.text.split("<h1>", 1)[1].split("</h1>", 1)[0] != shop["id"]

    creator_page = client.get(f"/admin/creators/{creator['id']}", headers=headers)
    assert "@ada" in creator_page.text
    assert "Membr" in creator_page.text
    assert "Create login for this Creatr" in creator_page.text

    empty = client.get("/admin/sales/disputes", headers=headers)
    assert "Connect the database" in empty.text or "No open disputes" in empty.text

    seen = set()
    queue = ["/admin"]
    while queue and len(seen) < 200:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url, headers=headers)
        assert response.status_code < 400, url
        if "html" not in response.headers.get("content-type", ""):
            continue
        for href in re.findall(r"""href=['"]([^'"]+)['"]""", response.text):
            href = unescape(href)
            if href.startswith("/admin") and not href.startswith("/admin/api"):
                queue.append(href.split("#", 1)[0])
    assert "/admin/sellers" in seen
    assert f"/admin/sellers/{shop['id']}" in seen
    assert f"/admin/creators/{creator['id']}" in seen
    assert "/admin/settings" in seen


def test_account_actions_hide_the_password_and_view_as_is_read_only(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1000")
    headers = _session(monkeypatch)
    shop, creator = _seller_and_creator(headers)
    seller = client.get(f"/admin/sellers/{shop['id']}", headers=headers)
    token = _csrf(seller.text)
    created = client.post(
        f"/admin/sellers/{shop['id']}/login",
        data={
            "csrf_token": token,
            "email": "seller@example.com",
            "password": "correct horse battery",
            "password_confirm": "correct horse battery",
            "confirm_name": "Acme Pottery",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert created.status_code == 303
    created = client.get(created.headers["location"], headers=headers)
    assert created.status_code == 200
    assert "Sign-in created" in created.text
    assert "correct horse battery" not in created.text
    assert "seller@example.com" in created.text

    mismatch = client.post(
        f"/admin/sellers/{shop['id']}/password",
        data={
            "csrf_token": _csrf(created.text),
            "password": "another horse battery",
            "password_confirm": "another horse battery",
            "confirm_name": "Wrong name",
        },
        headers=headers,
    )
    assert mismatch.status_code == 422
    assert "Type the name exactly" in mismatch.text

    link = client.post(
        f"/admin/sellers/{shop['id']}/reset-link",
        data={"csrf_token": _csrf(created.text), "confirm_name": "Acme Pottery"},
        headers=headers,
    )
    assert link.status_code == 200
    assert "Copy this link now" in link.text
    reset_url = link.text.split("id='reset-url' readonly value='", 1)[1].split("'", 1)[0]
    token_value = reset_url.rstrip("/").split("/")[-1]
    form = client.get(reset_url)
    assert form.status_code == 200
    assert "Set a new password" in form.text
    saved = client.post(
        reset_url,
        data={
            "csrf_token": form.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0],
            "password": "a brand new secret",
            "password_confirm": "a brand new secret",
        },
    )
    assert saved.status_code == 200
    assert "Password saved" in saved.text
    assert "a brand new secret" not in saved.text
    assert client.get(reset_url).status_code == 410

    no_reason = client.post(
        f"/admin/creators/{creator['id']}/view-as",
        data={"csrf_token": _csrf(client.get(f"/admin/creators/{creator['id']}", headers=headers).text), "confirm_name": "@ada", "reason": "short"},
        headers=headers,
    )
    assert no_reason.status_code == 422
    viewed = client.post(
        f"/admin/creators/{creator['id']}/view-as",
        data={
            "csrf_token": _csrf(client.get(f"/admin/creators/{creator['id']}", headers=headers).text),
            "confirm_name": "@ada",
            "reason": "Checking the offer page",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert viewed.status_code == 303
    assert any(event.event_name == "view_as_started" and event.properties.get("reason") == "Checking the offer page" for event in db.memory_events)
    creator_cookie = {"Cookie": viewed.headers["set-cookie"].split(";", 1)[0]}
    profile = client.get("/creator/profile", headers=creator_cookie)
    assert "Viewing as @ada, read-only." in profile.text
    blocked = client.post(
        "/creator/profile",
        data={"csrf_token": "x", "active_categories": "home", "desired_categories": "beauty"},
        headers=creator_cookie,
    )
    assert blocked.status_code == 403
    assert "read-only" in blocked.text
    assert "Traceback" not in blocked.text
    exit_token = profile.text.split("name='csrf_token' value='", 1)[1].split("'", 1)[0]
    left = client.post("/creator/logout", data={"csrf_token": exit_token}, headers=creator_cookie, follow_redirects=False)
    assert left.status_code == 303
    assert any(event.event_name == "view_as_ended" for event in db.memory_events)


def test_staging_tools_do_not_render_in_production(monkeypatch):
    headers = _session(monkeypatch)
    shop, creator = _seller_and_creator(headers)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("TEST_SELLER_SHOP_ID", shop["id"])
    monkeypatch.setenv("TEST_CREATOR_ID", creator["id"])
    staging = client.get("/admin", headers=headers)
    assert "STAGING" in staging.text
    assert "Test accounts" in staging.text
    assert "Acme Pottery" in staging.text
    assert f"<strong>{shop['id']}</strong>" not in staging.text

    monkeypatch.setenv("ENV", "production")
    production = client.get("/admin", headers=headers)
    assert "STAGING" not in production.text
    assert "Test accounts" not in production.text
    assert client.post("/admin/test-accounts/seller/view-as", data={"csrf_token": "x", "subject_id": shop["id"]}, headers=headers).status_code == 404

    settings = client.get("/admin/settings", headers=headers)
    assert "Launch readiness" in settings.text
    assert "Not ready" in settings.text
    assert "sk_" not in settings.text
    assert "SESSION_SECRET" not in settings.text
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("EMAIL_FROM", "Plantr <hello@example.com>")
    monkeypatch.delenv("ADMIN_SETUP_TOKEN")
    ready = client.get("/admin/settings", headers=headers)
    assert "Email sending is on." in ready.text
    assert "Admin setup is closed." in ready.text
    assert "re_test" not in ready.text


def test_json_admin_api_still_works(monkeypatch):
    headers = _session(monkeypatch)
    shop, _creator = _seller_and_creator(headers)
    listed = client.get("/admin/api/shops", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == shop["id"]
    assert client.get("/admin/api/summary", headers=headers).status_code == 200


def test_search_finds_a_business_name(monkeypatch):
    headers = _session(monkeypatch)
    _seller_and_creator(headers)
    found = client.get("/admin/search", params={"q": "Acme Pottery"}, headers=headers)
    assert "Acme Pottery" in found.text
    assert "Seller" in found.text
    blank = client.get("/admin/search", headers=headers)
    assert "Type a name" in blank.text
