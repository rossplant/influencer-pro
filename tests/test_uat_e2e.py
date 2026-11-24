"""Prove the UAT journey in-process with a fake mailer. No email is delivered."""

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import catalogue
import db
import mailer
import main
import uat_mode
from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

client = TestClient(main.app)
HTML = {"Accept": "text/html"}


def test_browser_journey_without_sending_email(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://influencer-pro-staging.up.railway.app")
    sent = []

    class Boom:
        def send(self, **kwargs):
            sent.append(kwargs)
            raise AssertionError("create set must not send email")

    monkeypatch.setattr(mailer, "default", lambda: Boom())
    admin = _cookie_header(_login())
    page = client.get("/admin/uat", headers={**admin, **HTML})
    assert page.status_code == 200
    assert "Create set (no email)" in page.text
    assert "First name" in page.text
    assert "Brand / shop name" in page.text
    assert "Creatr email (if different)" in page.text
    assert "disabled" in page.text
    token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/admin/uat/create-set",
        data={"csrf_token": token, "email": "uat-e2e@example.com"},
        headers={**admin, **HTML},
    )
    assert created.status_code == 200
    assert "No email was sent" in created.text
    assert "Ross at Plantr Collab" in created.text
    assert "hello@plantr-collab.com" in created.text
    assert sent == []
    assert "Open the supplier invite" in created.text
    supplier = created.text.split('href="/uat/invite/s/', 1)[1].split('"', 1)[0]
    creatr = created.text.split('href="/uat/invite/c/', 1)[1].split('"', 1)[0]
    supplier_page = client.get(f"/uat/invite/s/{supplier}", headers=HTML)
    assert supplier_page.status_code == 200
    assert "text/html" in supplier_page.headers["content-type"]
    assert "All users must be 18 or over" in supplier_page.text
    csrf = supplier_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    joined = client.post(
        f"/uat/invite/s/{supplier}",
        data={"csrf_token": csrf, "date_of_birth": "1990-01-01", "age_confirmed": "yes", "password": "a-long-password-1"},
        follow_redirects=False,
    )
    assert joined.status_code == 303
    assert joined.headers["location"] == "/seller"
    seller = {"Cookie": joined.headers["set-cookie"].split(";", 1)[0]}
    home = client.get("/seller", headers={**seller, **HTML})
    assert home.status_code == 200 and "Homeware supplier" in home.text
    products = client.get("/seller/products", headers={**seller, **HTML})
    assert products.status_code == 200
    template = client.get("/seller/products/csv-template", headers=seller)
    assert template.status_code == 200
    assert template.text == catalogue.TEMPLATE_CSV
    assert template.text.count("\n") >= 4
    preview = client.post(
        "/seller/products/import",
        data={"csrf_token": products.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]},
        files={"file": ("homeware.csv", catalogue.TEMPLATE_CSV, "text/csv")},
        headers={**seller, **HTML},
    )
    assert preview.status_code == 200
    assert preview.text.count("Stoneware bowl") >= 1
    assert preview.text.count("name='pick'") == 3
    picked = preview.text
    # Save every previewed row as a draft.
    import re

    titles = re.findall(r"name='title' value='([^']+)'", picked) or re.findall(r'name="title" value="([^"]+)"', picked)
    assert len(titles) == 3 or "Stoneware bowl" in picked
    studio = client.get("/dashboard/catalogue/listing-studio", headers={**seller, **HTML})
    assert studio.status_code == 200
    creatr_page = client.get(f"/uat/invite/c/{creatr}", headers=HTML)
    assert creatr_page.status_code == 200
    ccsrf = creatr_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    creatr_join = client.post(
        f"/uat/invite/c/{creatr}",
        data={"csrf_token": ccsrf, "date_of_birth": "1991-02-02", "age_confirmed": "yes", "password": "a-long-password-1"},
        follow_redirects=False,
    )
    assert creatr_join.status_code == 303
    creatr_cookie = {"Cookie": creatr_join.headers["set-cookie"].split(";", 1)[0]}
    profile = client.get("/creator/profile", headers={**creatr_cookie, **HTML})
    assert profile.status_code == 200
    assert "45,000" in profile.text
    offers = client.get("/creator/offers", headers={**creatr_cookie, **HTML})
    assert offers.status_code == 200
    missing = client.get("/uat/invite/s/not-a-real-code", headers=HTML)
    assert missing.status_code == 404
    assert "text/html" in missing.headers["content-type"]
    assert "not valid" in missing.text
    logged_out = client.get("/seller/products/csv-template", headers=HTML)
    assert logged_out.status_code == 200
    assert "Supplier sign in" in logged_out.text or "Sign in" in logged_out.text
    assert logged_out.headers["content-type"].startswith("text/html")
    for path in ("/seller/catalogue", "/seller/listings", "/seller/onboarding", "/seller/payouts", "/creator/matches", "/creator/browse", "/creator/onboarding"):
        moved = client.get(path, headers=HTML, follow_redirects=False)
        assert moved.status_code == 302, path
    unknown = client.get("/seller/no-such-page", headers=HTML)
    assert unknown.status_code == 404
    assert "text/html" in unknown.headers["content-type"]
    assert sent == []


def test_send_invite_delivers_two_emails_and_a_failed_one_retries_alone(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://influencer-pro-staging.up.railway.app")
    calls = []

    class Split:
        creatr_ok = False

        def send(self, **kwargs):
            calls.append(kwargs)
            html_body = kwargs.get("html") or ""
            if "Set up my Creatr profile" in html_body and not Split.creatr_ok:
                return {"status": mailer.FAILED, "id": None, "reason": "HTTP 403"}
            if "Set up my Creatr profile" in html_body:
                return {"status": mailer.SENT, "id": "re_creatr"}
            return {"status": mailer.SENT, "id": "re_supplier"}

    monkeypatch.setattr(mailer, "default", lambda: Split())
    admin = _cookie_header(_login())
    page = client.get("/admin/uat", headers={**admin, **HTML})
    token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/admin/uat/create-set",
        data={"csrf_token": token, "email": "uat-two@example.com"},
        headers={**admin, **HTML},
    )
    assert "not sent yet" in created.text
    send_token = created.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    press = created.text.split('name="press_id" value="', 1)[1].split('"', 1)[0]
    sent_page = client.post(
        "/admin/uat/send",
        data={"csrf_token": send_token, "email": "uat-two@example.com", "press_id": press},
        headers={**admin, **HTML},
    )
    assert sent_page.status_code == 200
    assert "Supplier invite sent" in sent_page.text
    assert "re_supplier" in sent_page.text
    assert "Creatr invite failed: HTTP 403" in sent_page.text
    assert "Retry the Creatr invite" in sent_page.text
    assert len(calls) == 2
    assert calls[0].get("reply_to") == "hello@plantr-collab.com"
    assert "{{" not in (calls[0].get("html") or "")
    assert "{{" not in (calls[0].get("text") or "")
    Split.creatr_ok = True
    before_role, after_role = sent_page.text.split('name="role" value="creator"', 1)
    retry_token = before_role.rsplit('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    retry_press = after_role.split('name="press_id" value="', 1)[1].split('"', 1)[0]
    retried = client.post(
        "/admin/uat/send",
        data={"csrf_token": retry_token, "email": "uat-two@example.com", "press_id": retry_press, "role": "creator"},
        headers={**admin, **HTML},
    )
    assert "Creatr invite sent" in retried.text
    assert "re_creatr" in retried.text
    assert len(calls) == 3
    assert "Set up my Creatr profile" in (calls[-1].get("html") or "")


def _csrf(page: str) -> str:
    return page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]


def _invite_path(page: str, slug: str) -> str:
    marker = f'href="/uat/invite/{slug}/'
    return "/uat/invite/" + slug + "/" + page.split(marker, 1)[1].split('"', 1)[0]


def test_uat_sets_stay_with_their_email_and_reset_rebuilds(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://influencer-pro-staging.up.railway.app")
    calls = []

    class Record:
        def send(self, **kwargs):
            calls.append(kwargs)
            return {"status": mailer.SENT, "id": "re_batch"}

    monkeypatch.setattr(mailer, "default", lambda: Record())
    admin = _cookie_header(_login())
    page = client.get("/admin/uat", headers={**admin, **HTML})
    token = _csrf(page.text)

    def post_set(path: str, email: str, **fields):
        data = {"csrf_token": token, "email": email, "first_name": "", "brand_name": "", "creatr_name": "", "creatr_email": ""}
        data.update(fields)
        return client.post(path, data=data, headers={**admin, **HTML})

    alpha = post_set("/admin/uat/create-set", "alpha@example.com", first_name="Ada", brand_name="Alpha Home", creatr_name="Alpha Creatr")
    assert alpha.status_code == 200
    assert "No email was sent" in alpha.text
    assert "Showing the set for alpha@example.com." in alpha.text
    assert calls == []
    alpha_supplier = _invite_path(alpha.text, "s")
    alpha_creatr = _invite_path(alpha.text, "c")
    beta = post_set("/admin/uat/create-set", "beta@example.com", first_name="Bea", brand_name="Beta Home", creatr_name="Beta Creatr")
    beta_supplier = _invite_path(beta.text, "s")
    assert alpha_supplier != beta_supplier
    viewed = client.get("/admin/uat?email=alpha@example.com", headers={**admin, **HTML})
    assert viewed.status_code == 200
    assert alpha_supplier in viewed.text
    assert beta_supplier not in viewed.text
    assert "not sent yet" in viewed.text
    silent = post_set("/admin/uat/create", "alpha@example.com")
    assert "No email was sent" in silent.text
    assert calls == []

    supplier_page = client.get(alpha_supplier, headers=HTML)
    joined = client.post(
        alpha_supplier,
        data={"csrf_token": _csrf(supplier_page.text), "date_of_birth": "1990-01-01", "age_confirmed": "yes", "password": "a-long-password-1"},
        follow_redirects=False,
    )
    seller = {"Cookie": joined.headers["set-cookie"].split(";", 1)[0]}
    products = client.get("/seller/products", headers={**seller, **HTML})
    drafted = client.post(
        "/seller/products/import/drafts",
        data={
            "csrf_token": _csrf(products.text),
            "row_count": "1",
            "pick": "0",
            "title_0": "Stoneware bowl",
            "url_0": "https://example.com/bowl",
            "price_0": "42",
            "category_0": "home",
            "image_url_0": "https://example.com/bowl.jpg",
            "commission_0": "15",
        },
        headers=seller,
        follow_redirects=False,
    )
    assert drafted.status_code == 303
    listed = client.get("/seller/products", headers={**seller, **HTML})
    assert "Stoneware bowl" in listed.text

    reset = post_set(
        "/admin/uat/reset-set",
        "alpha@example.com",
        first_name="Stew",
        brand_name="Stewart Home",
        creatr_name="Influencer Stew",
    )
    assert "UAT set reset. No email was sent." in reset.text
    assert "Showing the set for alpha@example.com." in reset.text
    assert 'value="Stewart Home"' in reset.text
    assert 'value="Influencer Stew"' in reset.text
    assert calls == []
    fresh_supplier = _invite_path(reset.text, "s")
    fresh_creatr = _invite_path(reset.text, "c")
    assert fresh_supplier != alpha_supplier
    assert client.get(alpha_supplier, headers=HTML).status_code == 404
    assert client.get(alpha_creatr, headers=HTML).status_code == 404
    fresh_page = client.get(fresh_supplier, headers=HTML)
    fresh_join = client.post(
        fresh_supplier,
        data={"csrf_token": _csrf(fresh_page.text), "date_of_birth": "1990-01-01", "age_confirmed": "yes", "password": "a-long-password-1"},
        follow_redirects=False,
    )
    fresh_seller = {"Cookie": fresh_join.headers["set-cookie"].split(";", 1)[0]}
    fresh_products = client.get("/seller/products", headers={**fresh_seller, **HTML})
    assert fresh_products.status_code == 200
    assert "Stoneware bowl" not in fresh_products.text
    creatr_page = client.get(fresh_creatr, headers=HTML)
    creatr_join = client.post(
        fresh_creatr,
        data={"csrf_token": _csrf(creatr_page.text), "date_of_birth": "1991-02-02", "age_confirmed": "yes", "password": "a-long-password-1"},
        follow_redirects=False,
    )
    creatr_cookie = {"Cookie": creatr_join.headers["set-cookie"].split(";", 1)[0]}
    profile = client.get("/creator/profile", headers={**creatr_cookie, **HTML})
    assert "<h1>Influencer Stew</h1>" in profile.text
    assert "<h1>@" not in profile.text
    press = reset.text.split('name="press_id" value="', 1)[1].split('"', 1)[0]
    sent = client.post(
        "/admin/uat/send",
        data={"csrf_token": token, "email": "alpha@example.com", "press_id": press},
        headers={**admin, **HTML},
    )
    assert sent.status_code == 200
    assert len(calls) == 2
    assert "Hi Stew," in (calls[0].get("text") or "") + (calls[1].get("text") or "")

