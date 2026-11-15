"""Seller and creator pilot apps."""

import db
from test_api import INFLUENCER, client
from tests.test_admin import _setup


PASSWORD = "seller-password"
CREATOR_PASSWORD = "creator-password"


def _token(page: str, name: str = "csrf_token") -> str:
    marker = f'name="{name}" value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def _meta_csrf(page: str) -> str:
    marker = 'name="csrf-token" content="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def _cookie(response, cookie_name: str) -> dict:
    raw = response.headers["set-cookie"]
    assert cookie_name in raw
    assert "httponly" in raw.lower()
    assert "secure" in raw.lower()
    assert "samesite=lax" in raw.lower()
    pair = raw.split(";", 1)[0]
    return {"Cookie": pair}


def test_seller_and_creator_apps(monkeypatch):
    _setup(monkeypatch)
    shop = client.post("/shops", json={"name": "Pilot Seller", "primary_category": "home"}).json()
    from tests.test_admin import EMAIL, PASSWORD as ADMIN_PASSWORD, _cookie_header, _login

    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    admin_page = client.get("/admin", headers=admin_cookie)
    admin_csrf = _meta_csrf(admin_page.text)
    missing = client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"email": "seller@example.com", "password": PASSWORD, "password_confirm": PASSWORD},
        headers=admin_cookie,
    )
    assert missing.status_code == 403

    created = client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={
            "csrf_token": admin_csrf,
            "email": "seller@example.com",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        },
        headers=admin_cookie,
    )
    assert created.status_code == 200
    assert created.json()["role"] == "seller"

    login_page = client.get("/seller")
    assert 'autocomplete="username"' in login_page.text
    assert 'autocomplete="current-password"' in login_page.text
    bad_csrf = client.post(
        "/seller/login",
        data={"email": "seller@example.com", "password": PASSWORD, "csrf_token": "nope"},
        follow_redirects=False,
    )
    assert bad_csrf.status_code == 403
    signed_in = client.post(
        "/seller/login",
        data={"email": "seller@example.com", "password": PASSWORD, "csrf_token": _token(login_page.text)},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    seller_cookie = _cookie(signed_in, "plantr_seller")

    products = client.get("/seller/products", headers=seller_cookie)
    assert products.status_code == 200
    saved = client.post(
        "/seller/products",
        data={
            "csrf_token": _token(products.text),
            "title": "Lamp",
            "category": "home",
            "price": "40",
            "url": "https://brand.example/lamp",
            "description": "A small lamp",
        },
        headers=seller_cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303
    listed = client.get("/seller/products", headers=seller_cookie)
    assert "Lamp" in listed.text
    product_id = next(iter(db.memory_products))
    assert db.memory_products[product_id].source == "manual"
    assert db.memory_products[product_id].attributes["url"] == "https://brand.example/lamp"

    onboard = client.post(
        "/onboard-influencer",
        json={
            **INFLUENCER,
            "handle": "@maker",
            "active_categories": ["home"],
            "email": "maker@example.com",
            "password": CREATOR_PASSWORD,
        },
    )
    assert onboard.status_code == 200
    assert "password" not in onboard.json()["influencer"]
    assert CREATOR_PASSWORD not in onboard.text
    creator_id = onboard.json()["id"]

    found = client.post(
        "/seller/find",
        data={"csrf_token": _token(listed.text), "product_id": product_id},
        headers=seller_cookie,
    )
    assert found.status_code == 200
    assert "Score" in found.text
    assert "@maker" in found.text
    assert "est_sales" in found.text or "estimates" in found.text.lower() or "reach" in found.text.lower()
    action = "/seller/shortlist-items/"
    assert action in found.text
    item_id = found.text.split(action, 1)[1].split("/accept", 1)[0]
    accepted = client.post(
        f"/seller/shortlist-items/{item_id}/accept",
        data={"csrf_token": _token(found.text)},
        headers=seller_cookie,
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/seller/accepted"

    link = client.post(
        "/creator-product-links",
        json={"creator_id": creator_id, "product_id": product_id, "destination_url": "https://brand.example/lamp"},
        headers=admin_cookie,
    )
    assert link.status_code == 200
    code = link.json()["discount_code"]
    assert code in client.get("/seller/accepted", headers=seller_cookie).text

    creator_login = client.get("/creator")
    signed = client.post(
        "/creator/login",
        data={"email": "maker@example.com", "password": CREATOR_PASSWORD, "csrf_token": _token(creator_login.text)},
        follow_redirects=False,
    )
    creator_cookie = _cookie(signed, "plantr_creator")
    assert "Lamp" in client.get("/creator/offers", headers=creator_cookie).text
    assert code in client.get("/creator/links", headers=creator_cookie).text


def test_portal_flow_with_admin_cookie(monkeypatch):
    from tests.test_admin import EMAIL, PASSWORD as ADMIN_PASSWORD, _cookie_header, _login

    _setup(monkeypatch)
    admin_cookie = _cookie_header(_login(password=ADMIN_PASSWORD, email=EMAIL))
    shop = client.post("/shops", json={"name": "View Shop"}).json()
    creator = client.post(
        "/onboard-influencer",
        json={**INFLUENCER, "handle": "@view", "active_categories": ["home"], "email": "view@example.com", "password": CREATOR_PASSWORD},
    ).json()
    admin_page = client.get("/admin", headers=admin_cookie)
    csrf = _meta_csrf(admin_page.text)
    assert 'Create login' in admin_page.text or "Create login" in admin_page.text
    assert "View as" in admin_page.text

    seller = client.post(
        f"/admin/api/shops/{shop['id']}/login",
        json={"csrf_token": csrf, "email": "view-seller@example.com", "password": PASSWORD, "password_confirm": PASSWORD},
        headers=admin_cookie,
    )
    assert seller.status_code == 200
    viewed = client.post(
        f"/admin/api/shops/{shop['id']}/view-as",
        json={"csrf_token": csrf},
        headers=admin_cookie,
    )
    assert viewed.status_code == 200
    seller_cookie = _cookie(viewed, "plantr_seller")
    profile = client.get("/seller/profile", headers=seller_cookie)
    assert "View Shop" in profile.text
    assert "Viewing as this shop." in profile.text

    creator_view = client.post(
        f"/admin/api/creators/{creator['id']}/view-as",
        json={"csrf_token": csrf},
        headers=admin_cookie,
    )
    creator_cookie = _cookie(creator_view, "plantr_creator")
    creator_page = client.get("/creator/offers", headers=creator_cookie)
    assert creator_page.status_code == 200
    assert "Offers" in creator_page.text
    assert client.get("/seller/products", headers=creator_cookie).text.find("Seller sign in") >= 0

    login_page = client.get("/creator")
    signed = client.post(
        "/creator/login",
        data={"email": "view@example.com", "password": CREATOR_PASSWORD, "csrf_token": _token(login_page.text)},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    own_cookie = _cookie(signed, "plantr_creator")
    saved = client.post(
        "/creator/profile",
        data={"csrf_token": _token(client.get("/creator/profile", headers=own_cookie).text), "active_categories": "home", "desired_categories": "beauty"},
        headers=own_cookie,
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert "home" in client.get("/creator/profile", headers=own_cookie).text

    for _ in range(5):
        page = client.get("/seller")
        failed = client.post(
            "/seller/login",
            data={"email": "view-seller@example.com", "password": "wrong-password", "csrf_token": _token(page.text)},
            follow_redirects=False,
        )
        assert failed.status_code == 401
    blocked = client.post(
        "/seller/login",
        data={"email": "view-seller@example.com", "password": "wrong-password", "csrf_token": _token(client.get("/seller").text)},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert PASSWORD not in "".join(account.password_hash for account in db.memory_portal_accounts)
