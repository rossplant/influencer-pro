"""Supplier screens stay free of tier-climb language. Listing Studio is per product."""

from datetime import date

from fastapi.testclient import TestClient

import main
import recognition
import supplier_copy
from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

client = TestClient(main.app)


def test_supplier_strings_do_not_offer_a_tier_to_climb():
    blob = "\n".join(supplier_copy.visible_strings()).lower()
    for banned in supplier_copy.BANNED:
        assert banned.lower() not in blob
    assert "Suppliers are not listed by tier." in supplier_copy.LEADERBOARD_HELP
    assert supplier_copy.HOW_STEP == "Track sales and pay Creatrs."
    assert "40+" in supplier_copy.MARKETING_TILE
    assert "A$0.99" not in blob
    page = client.get("/pilot-sellers").text
    assert supplier_copy.HOW_STEP in page
    assert supplier_copy.MARKETING_TILE in page
    assert supplier_copy.PRODUCT_CAP in page
    for banned in ("unlock Trendsettr", "level up", "Start at Influencr", "you unlocked"):
        assert banned.lower() not in page.lower()


def test_glossary_says_the_supplier_has_nothing_to_climb(monkeypatch):
    _setup(monkeypatch)
    shop = client.post("/shops", json={"name": "Glossary"}).json()
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    csrf = client.get("/admin", headers=admin).text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "gloss@example.com", "password": "password-pass", "password_confirm": "password-pass"},
        headers=admin,
    )
    login = client.get("/seller")
    token = login.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    signed = client.post(
        "/seller/login",
        data={"email": "gloss@example.com", "password": "password-pass", "csrf_token": token},
        follow_redirects=False,
    )
    cookie = {"Cookie": signed.headers["set-cookie"].split(";", 1)[0]}
    home = client.get("/seller", headers=cookie).text
    assert "How you move up" not in home
    assert supplier_copy.REVIEW_NOTE in home
    glossary = client.get("/seller/creatr-tiers", headers=cookie).text
    assert supplier_copy.GLOSSARY_LEAD in glossary
    assert "Influencr: a Creatr with 5+" in glossary
    assert "How you move up" not in glossary
    badges = client.get("/seller/badges", headers=cookie).text
    assert "20 or more confirmed sales" in badges
    old = client.get("/dashboard/catalogue/enhance", headers=cookie, follow_redirects=False)
    assert old.status_code == 302
    assert old.headers["location"] == "/dashboard/catalogue/listing-studio"
    studio = client.get("/dashboard/catalogue/listing-studio", headers=cookie).text
    assert "Listing Studio" in studio
    assert "A$1.50" in studio
    assert "A$0.99" not in studio
    price = recognition.listing_studio_price(5, on=date(2026, 11, 23))
    assert price["total_cents"] == 150
