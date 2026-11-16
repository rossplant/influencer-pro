"""Shopify order sync, light commission tracking, collab agreements and the no-ABN warning."""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

import auth
import commission
import db
import main
import shopify_sync
from models import (
    AbnWarningAck,
    AttributedOrder,
    CollabAgreement,
    CommissionAuditLog,
    CommissionStatement,
    Creator,
    CreatorProductLink,
    Event,
    Product,
    Shop,
    ShopifyConnection,
    Shortlist,
    ShortlistItem,
    utcnow,
)
from test_api import client
from tests.test_admin import _cookie_header, _login, _setup

SIGNING_KEY = "store_signing_key_0123456789abcdef"
APP_SECRET = "app-secret-for-tests"
VALID_ABN = "51824753556"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'plantr.db'}")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SHOPIFY_TOKEN_KEY", Fernet.generate_key().decode())
    for name in ("SHOPIFY_API_KEY", "SHOPIFY_API_SECRET", "COMMISSION_OVERDUE_RULE", "COMMISSION_EXCLUDE_PLATFORM_PAID",
                 "PAYS_ON_TIME_VISIBILITY", "COMMISSION_CONFIRM_WINDOW_DAYS", "COMMISSION_DISPUTE_RESOLVE_AFTER_DAYS",
                 "PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    main.init_storage()
    ids = {"shop": str(uuid.uuid4()), "creator": str(uuid.uuid4()), "creator2": str(uuid.uuid4()), "product": str(uuid.uuid4())}
    with db.session_scope() as session:
        session.add(Shop(id=ids["shop"], name="Sunny Goods", currency="AUD", country="AU"))
        for key, handle, abn in (("creator", "@nina", None), ("creator2", "@omar", VALID_ABN)):
            session.add(Creator(id=ids[key], handle=handle, interests=[], expertise_level={}, preferred_promotion_styles=[],
                                content_formats=[], past_categories=[], restrictions=[], excluded_categories=[], excluded_claims=[],
                                platforms=[], profile={}, abn=abn))
        session.flush()
        session.add(Product(id=ids["product"], shop_id=ids["shop"], title="Sun Hat", source="manual", commission_rate=10, images=[], tags=[], claims=[],
                            attributes={"url": "https://sunny.example/products/hat"}))
        session.flush()
        for key, code in (("creator", "PCNINA0001"), ("creator2", "PCOMAR0002")):
            link_id = str(uuid.uuid4())
            ids[f"link_{key}"] = link_id
            session.add(CreatorProductLink(id=link_id, shop_id=ids["shop"], product_id=ids["product"], creator_id=ids[key],
                                           link_url="https://sunny.example/products/hat", discount_code=code, platform="plantr"))
    return ids


def _seller_headers(shop_id, email="seller@sunny.example"):
    token, csrf = auth.issue_role_session("seller", email, shop_id)
    return {"Cookie": f"{auth.SELLER_COOKIE}={token}", "X-CSRF-Token": csrf}


def _creator_headers(creator_id, email="nina@example.com"):
    token, csrf = auth.issue_role_session("creator", email, creator_id)
    return {"Cookie": f"{auth.CREATOR_COOKIE}={token}", "X-CSRF-Token": csrf}


def _admin_headers(monkeypatch):
    _setup(monkeypatch)
    return _cookie_header(_login())


def _connect_bridge(ids):
    response = client.post(
        "/seller/api/shopify/store-webhooks",
        json={"shop_domain": "sunny-goods", "signing_secret": SIGNING_KEY},
        headers=_seller_headers(ids["shop"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _send(path, topic, payload, *, key=SIGNING_KEY, webhook_id=None, domain="sunny-goods.myshopify.com"):
    body = json.dumps(payload).encode()
    signature = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()
    headers = {
        "X-Shopify-Topic": topic,
        "X-Shopify-Hmac-Sha256": signature,
        "X-Shopify-Shop-Domain": domain,
        "Content-Type": "application/json",
    }
    if webhook_id:
        headers["X-Shopify-Webhook-Id"] = webhook_id
    return client.post(path, content=body, headers=headers)


def _order(order_id, *, codes=(), landing=None, price="110.00", qty=1, tax="10.00", taxes_included=True,
           financial="paid", created="2026-10-10T03:00:00Z", refunds=None, shipping="9.95"):
    order = {
        "id": order_id,
        "name": f"#{order_id}",
        "created_at": created,
        "updated_at": created,
        "currency": "AUD",
        "taxes_included": taxes_included,
        "financial_status": financial,
        "cancelled_at": None,
        "landing_site": landing,
        "discount_codes": [{"code": c, "amount": "0.00"} for c in codes],
        "line_items": [{"price": price, "quantity": qty, "discount_allocations": [], "tax_lines": [{"price": tax}]}],
        "shipping_lines": [{"price": shipping}],
        "customer": {"email": "buyer@example.com", "first_name": "Buyer"},
    }
    if refunds is not None:
        order["refunds"] = refunds
    return order


def _click(ids, key="creator", when=None):
    with db.session_scope() as session:
        session.add(Event(id=str(uuid.uuid4()), occurred_at=when or datetime(2026, 10, 9, tzinfo=timezone.utc), event_name="link_clicked",
                          entity_type="creator_product_link", entity_id=ids[f"link_{key}"], shop_id=ids["shop"], actor="system",
                          properties={"discount_code": "PCNINA0001" if key == "creator" else "PCOMAR0002"}))


def _orders():
    with db.session_scope() as session:
        return {o.external_order_id: o for o in session.scalars(select(AttributedOrder)).all()}


# ------------------------------------------------------------------ connection + webhooks

def test_bridge_connect_stores_secret_encrypted_and_returns_url(env):
    status = _connect_bridge(env)
    assert status["mode"] == "store_webhooks" and status["connected"]
    assert status["webhook_url"].startswith("http://testserver/shopify/webhooks/store/")
    assert status["webhook_events"] == ["Order creation", "Order update", "Refund create"]
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection))
        assert SIGNING_KEY not in (conn.webhook_secret_enc or "")
        assert shopify_sync.decrypt(conn.webhook_secret_enc) == SIGNING_KEY
        assert session.get(Shop, env["shop"]).shopify_domain == "sunny-goods.myshopify.com"
    # CSRF required
    bad = client.post("/seller/api/shopify/store-webhooks", json={"shop_domain": "x", "signing_secret": SIGNING_KEY},
                      headers={"Cookie": _seller_headers(env["shop"])["Cookie"]})
    assert bad.status_code == 403


def test_webhook_rejects_bad_hmac_and_is_idempotent(env):
    path = _connect_bridge(env)["webhook_path"]
    assert _send(path, "orders/create", _order(1001, codes=["PCNINA0001"]), key="wrong-key").status_code == 401
    assert _send("/shopify/webhooks/store/not-a-token", "orders/create", _order(1001)).status_code == 401
    first = _send(path, "orders/create", _order(1001, codes=["pcnina0001"]), webhook_id="wh-1")
    assert first.json() == {"status": "created"}
    again = _send(path, "orders/create", _order(1001, codes=["PCNINA0001"]), webhook_id="wh-1")
    assert again.json() == {"status": "duplicate"}
    assert len(_orders()) == 1


def test_code_sale_net_excludes_shipping_and_gst_and_stores_no_pii(env):
    path = _connect_bridge(env)["webhook_path"]
    _send(path, "orders/create", _order(1002, codes=["PCNINA0001"], price="55.00", qty=2, tax="10.00"))
    order = _orders()["1002"]
    assert order.attribution_method == "code" and order.attributed
    assert float(order.gmv_gross) == 100.00  # 110 incl GST - 10 GST, shipping excluded
    assert float(order.shipping_excluded) == 9.95
    assert order.settled
    assert "buyer@example.com" not in json.dumps(order.raw_payload)


def test_tax_exclusive_store_keeps_line_price(env):
    path = _connect_bridge(env)["webhook_path"]
    _send(path, "orders/create", _order(1003, codes=["PCNINA0001"], price="100.00", tax="10.00", taxes_included=False))
    assert float(_orders()["1003"].gmv_gross) == 100.00


def test_link_sale_needs_recent_click_and_unlinked_orders_are_not_stored(env):
    path = _connect_bridge(env)["webhook_path"]
    _send(path, "orders/create", _order(2001, landing="/products/hat?pc_ref=PCNINA0001"))
    assert not _orders()["2001"].attributed
    _click(env)
    _send(path, "orders/create", _order(2002, landing="/products/hat?utm_source=x&pc_ref=PCNINA0001"))
    assert _orders()["2002"].attributed and _orders()["2002"].attribution_method == "link"
    assert _send(path, "orders/create", _order(2003)).json() == {"status": "ignored_no_plantr_link"}
    assert "2003" not in _orders()


def test_link_and_code_same_creator_counts_once_and_code_beats_other_link(env):
    path = _connect_bridge(env)["webhook_path"]
    _click(env)
    _send(path, "orders/create", _order(3001, codes=["PCNINA0001"], landing="/p?pc_ref=PCNINA0001"))
    _send(path, "orders/create", _order(3002, codes=["PCOMAR0002"], landing="/p?pc_ref=PCNINA0001"))
    orders = _orders()
    assert orders["3001"].attribution_method == "link+code" and orders["3001"].creator_id == env["creator"]
    assert orders["3002"].attribution_method == "code" and orders["3002"].creator_id == env["creator2"]
    assert orders["3002"].raw_payload["superseded_link_creator_id"] == env["creator"]


def test_seller_store_code_alias_is_credited(env):
    path = _connect_bridge(env)["webhook_path"]
    response = client.put(f"/seller/api/links/{env['link_creator']}/store-code", json={"code": "nina10"}, headers=_seller_headers(env["shop"]))
    assert response.status_code == 200 and response.json()["store_discount_code"] == "NINA10"
    _send(path, "orders/create", _order(3101, codes=["NINA10"]))
    assert _orders()["3101"].creator_id == env["creator"]


def test_refunds_are_subtracted_once_and_pending_is_unsettled(env):
    path = _connect_bridge(env)["webhook_path"]
    _send(path, "orders/create", _order(4001, codes=["PCNINA0001"], financial="pending"))
    assert not _orders()["4001"].settled
    _send(path, "orders/updated", {**_order(4001, codes=["PCNINA0001"]), "updated_at": "2026-10-11T00:00:00Z"})
    assert _orders()["4001"].settled
    refund = {"id": 77, "order_id": 4001, "refund_line_items": [{"subtotal": "55.00", "total_tax": "5.00"}]}
    assert _send(path, "refunds/create", refund).json() == {"status": "refund_applied"}
    assert _send(path, "refunds/create", refund, webhook_id="another").json() == {"status": "refund_duplicate"}
    order = _orders()["4001"]
    assert float(order.refunded_amount) == 50.00 and float(order.net_gmv) == 50.00


def test_uninstall_and_compliance_webhooks(env, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_KEY", "key")
    monkeypatch.setenv("SHOPIFY_API_SECRET", APP_SECRET)
    _connect_bridge(env)
    assert _send("/shopify/webhooks", "customers/redact", {"shop_domain": "sunny-goods.myshopify.com", "orders_to_redact": [1]}, key="bad").status_code == 401
    assert _send("/shopify/webhooks", "customers/redact", {"orders_to_redact": [1]}, key=APP_SECRET).status_code == 200
    assert _send("/shopify/webhooks", "customers/data_request", {"orders_requested": [1]}, key=APP_SECRET).status_code == 200
    assert _send("/shopify/webhooks", "app/uninstalled", {}, key=APP_SECRET).json() == {"status": "uninstalled"}
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection))
        assert conn.status == "uninstalled" and conn.webhook_secret_enc is None
    assert _send("/shopify/webhooks", "shop/redact", {}, key=APP_SECRET).status_code == 200
    with db.session_scope() as session:
        assert session.scalar(select(ShopifyConnection)) is None
        actions = {row.action for row in session.scalars(select(CommissionAuditLog)).all()}
    assert {"customers_redact", "customers_data_request", "shop_redact"} <= actions


def test_oauth_install_callback_and_backfill(env, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_KEY", "client-id")
    monkeypatch.setenv("SHOPIFY_API_SECRET", APP_SECRET)
    start = client.get("/seller/shopify/install", params={"shop": "sunny-goods.myshopify.com"},
                       headers=_seller_headers(env["shop"]), follow_redirects=False)
    assert start.status_code == 303
    location = start.headers["location"]
    assert location.startswith("https://sunny-goods.myshopify.com/admin/oauth/authorize?client_id=client-id&scope=read_orders")
    state = location.split("state=")[1]
    params = {"code": "abc", "shop": "sunny-goods.myshopify.com", "state": state, "timestamp": "1"}
    params["hmac"] = shopify_sync.oauth_query_hmac(params, APP_SECRET)
    calls = []

    def fake_post(url, *, json_body, headers=None):
        calls.append((url, json_body))
        if url.endswith("/access_token"):
            assert json_body["expiring"] == 1
            return {"access_token": "shpat_live_token", "expires_in": 3600, "refresh_token": "r1", "scope": "read_orders"}
        node = {
            "legacyResourceId": "5001", "name": "#5001", "createdAt": "2026-10-10T00:00:00Z", "updatedAt": "2026-10-10T00:00:00Z",
            "cancelledAt": None, "currencyCode": "AUD", "taxesIncluded": True, "displayFinancialStatus": "PAID",
            "discountCodes": ["PCNINA0001"], "customAttributes": [], "customerJourneySummary": None,
            "shippingLines": {"nodes": []},
            "lineItems": {"nodes": [{"quantity": 1, "isGiftCard": False, "originalUnitPriceSet": {"shopMoney": {"amount": "22.00"}},
                                     "discountAllocations": [], "taxLines": [{"priceSet": {"shopMoney": {"amount": "2.00"}}}]}]},
            "refunds": [],
        }
        return {"data": {"orders": {"nodes": [node], "pageInfo": {"hasNextPage": False}}}}

    bad = dict(params, hmac="0" * 64)
    with pytest.raises(Exception):
        shopify_sync.complete_oauth(bad, http_post=fake_post)
    assert shopify_sync.complete_oauth(params, http_post=fake_post) == env["shop"]
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection))
        assert conn.mode == "app" and conn.status == "active" and "shpat_live_token" not in conn.access_token_enc
    result = shopify_sync.backfill(env["shop"], days=30, actor="admin@example.com", http_post=fake_post)
    assert result["counts"] == {"created": 1}
    assert float(_orders()["5001"].gmv_gross) == 20.00


def test_redirect_adds_ref_only_for_connected_shop(env):
    plain = client.get("/r/PCNINA0001", follow_redirects=False)
    assert plain.headers["location"] == "https://sunny.example/products/hat"
    _connect_bridge(env)
    tagged = client.get("/r/PCNINA0001", follow_redirects=False)
    assert tagged.headers["location"] == "https://sunny.example/products/hat?pc_ref=PCNINA0001"


def test_shopify_secret_storage_requires_key(env, monkeypatch):
    monkeypatch.delenv("SHOPIFY_TOKEN_KEY")
    response = client.post("/seller/api/shopify/store-webhooks", json={"shop_domain": "sunny-goods", "signing_secret": SIGNING_KEY},
                           headers=_seller_headers(env["shop"]))
    assert response.status_code == 503


# ------------------------------------------------------------------ commission

def _statement_for(ids, monkeypatch, *, creator_key="creator", order_id=6001, codes=("PCNINA0001",)):
    path = _connect_bridge(ids)["webhook_path"] if not _connected() else _path()
    _send(path, "orders/create", _order(order_id, codes=codes))
    result = commission.generate_statements("2026-10", now=datetime(2026, 11, 2, tzinfo=timezone.utc))
    with db.session_scope() as session:
        st = session.scalar(select(CommissionStatement).where(CommissionStatement.creator_id == ids[creator_key]))
        return st.id, result


def _connected():
    with db.session_scope() as session:
        return session.scalar(select(ShopifyConnection.id)) is not None


def _path():
    with db.session_scope() as session:
        return "/shopify/webhooks/store/" + session.scalar(select(ShopifyConnection.webhook_path_token))


def _set_overdue(statement_id, days_ago=1):
    with db.session_scope() as session:
        st = session.get(CommissionStatement, statement_id)
        st.overdue_on = date.today() - timedelta(days=days_ago)


def test_statement_amounts_and_idempotent_generation(env, monkeypatch):
    st_id, result = _statement_for(env, monkeypatch)
    assert len(result["created"]) == 1
    detail = client.get(f"/seller/api/commission/statements/{st_id}", headers=_seller_headers(env["shop"])).json()
    assert detail["amount_owed"] == 10.00  # 10% of 100 net
    assert detail["label"] == commission.ESTIMATE_LABEL
    assert detail["lines"][0]["order"] == "#6001"
    # Default overdue rule: 30 days after the end of the sale month.
    assert detail["overdue_on"] == "2026-11-30"
    again = commission.generate_statements("2026-10", now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert again["created"] == [] and again["skipped_existing"] == 1
    with pytest.raises(Exception):
        commission.generate_statements("2026-11", now=datetime(2026, 11, 3, tzinfo=timezone.utc))


def test_later_refund_becomes_next_month_adjustment(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    refund = {"id": 9, "order_id": 6001, "refund_line_items": [{"subtotal": "55.00", "total_tax": "5.00"}]}
    _send(_path(), "refunds/create", refund)
    commission.generate_statements("2026-11", now=datetime(2026, 12, 2, tzinfo=timezone.utc))
    with db.session_scope() as session:
        nov = session.scalar(select(CommissionStatement).where(CommissionStatement.period == "2026-11"))
        assert float(nov.amount_owed) == -5.00


def test_overdue_rule_settings(env, monkeypatch):
    end = date(2026, 10, 31)
    # Default (Ross, Risk): 30 days after the end of the sale month, even when a payment date is agreed.
    assert commission.overdue_rule() == "month_end"
    assert commission.overdue_on(end, date(2026, 11, 14)) == date(2026, 11, 30)
    monkeypatch.setenv("COMMISSION_OVERDUE_DAYS", "45")
    assert commission.overdue_on(end, None) == date(2026, 12, 15)
    monkeypatch.setenv("COMMISSION_OVERDUE_RULE", "payment_date")
    assert commission.overdue_on(end, date(2026, 11, 14)) == date(2026, 11, 21)
    assert commission.overdue_on(end, None) == date(2026, 12, 15)


def test_platform_paid_collabs_excluded_by_default(env, monkeypatch):
    with db.session_scope() as session:
        session.add(CollabAgreement(id=str(uuid.uuid4()), shop_id=env["shop"], creator_id=env["creator"], product_id=env["product"],
                                    commission_rate=15, paid_via="tiktok_shop", status="active", terms_version="v", terms_text="t",
                                    terms_sha256="h", seller_accepted_at=utcnow(), seller_accepted_by="s", creator_accepted_at=utcnow()))
    _connect_bridge(env)
    _send(_path(), "orders/create", _order(6101, codes=["PCNINA0001"]))
    assert commission.generate_statements("2026-10", now=datetime(2026, 11, 2, tzinfo=timezone.utc))["created"] == []
    monkeypatch.setenv("COMMISSION_EXCLUDE_PLATFORM_PAID", "false")
    with db.session_scope() as session:
        session.get(AttributedOrder, _orders()["6101"].id).statement_reported_net = 0
    created = commission.generate_statements("2026-10", now=datetime(2026, 11, 2, tzinfo=timezone.utc))["created"]
    with db.session_scope() as session:
        assert float(session.get(CommissionStatement, created[0]).amount_owed) == 15.00


def test_mark_paid_confirm_and_bank_details_rejected(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    seller = _seller_headers(env["shop"])
    bank = client.post(f"/seller/api/commission/statements/{st_id}/mark-paid",
                       json={"paid_on": "2026-11-05", "reference": "BSB 062-000 acct 12345678"}, headers=seller)
    assert bank.status_code == 422
    paid = client.post(f"/seller/api/commission/statements/{st_id}/mark-paid",
                       json={"paid_on": "2026-11-05", "reference": "Nov transfer"}, headers=seller)
    assert paid.status_code == 200 and paid.json()["display"]["code"] == "awaiting_confirmation"
    ok = client.post(f"/creator/api/commission/statements/{st_id}/confirm", headers=_creator_headers(env["creator"]))
    assert ok.json()["status"] == "confirmed"
    record = client.get("/seller/api/pays-on-time", headers=seller).json()
    assert record["text"] == "1 of 1 paid on time"


def test_overdue_blocks_new_collabs_only_and_mark_paid_clears(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    _set_overdue(st_id)
    block = client.get("/seller/api/commission/block", headers=_seller_headers(env["shop"])).json()
    assert block["blocked"] and block["reasons"][0]["kind"] == "overdue"
    offer = client.post("/seller/api/collabs", json={"creator_id": env["creator2"], "commission_rate": 10}, headers=_seller_headers(env["shop"]))
    assert offer.status_code == 409
    # Live links keep working while blocked.
    assert client.get("/r/PCNINA0001", follow_redirects=False).status_code == 302
    assert commission.overdue_sweep()["overdue"] == [st_id]
    commission.mark_paid(env["shop"], st_id, paid_on=date.today().isoformat(), reference=None, actor="s")
    assert not commission.collab_block(env["shop"])["blocked"]
    assert client.get("/seller/api/pays-on-time", headers=_seller_headers(env["shop"])).json()["text"] == "0 of 1 paid on time"


def test_unconfirmed_after_window_and_no_late_dispute(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    commission.mark_paid(env["shop"], st_id, paid_on="2026-11-05", reference=None, actor="s")
    with db.session_scope() as session:
        session.get(CommissionStatement, st_id).marked_paid_at = utcnow() - timedelta(days=15)
    detail = commission.statement_detail(st_id, viewer="seller")
    assert detail["display"]["label"] == "Marked paid by Seller, not confirmed"
    late = client.post(f"/creator/api/commission/statements/{st_id}/dispute", json={"reason": "Not received", "amount": 10},
                       headers=_creator_headers(env["creator"]))
    assert late.status_code == 409
    assert not commission.collab_block(env["shop"])["blocked"]


def test_dispute_blocks_withdraw_clears(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    creator = _creator_headers(env["creator"])
    assert client.post(f"/creator/api/commission/statements/{st_id}/dispute", json={"reason": "Not received"}, headers=creator).status_code == 422
    disputed = client.post(f"/creator/api/commission/statements/{st_id}/dispute", json={"reason": "Not received yet", "amount": "10"}, headers=creator)
    assert disputed.status_code == 200
    assert commission.collab_block(env["shop"])["reasons"][0]["kind"] == "disputed"
    client.post(f"/creator/api/commission/statements/{st_id}/withdraw-dispute", headers=creator)
    assert not commission.collab_block(env["shop"])["blocked"]


def test_admin_resolve_dispute_after_window_with_reason(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    commission.dispute(env["creator"], st_id, reason="Amount is wrong", amount=5, actor="nina@example.com")
    admin = _admin_headers(monkeypatch)
    early = client.post(f"/admin/api/commission/statements/{st_id}/resolve-dispute",
                        json={"reason": "Reviewed both sides' evidence", "outcome": "clear_block"}, headers=admin)
    assert early.status_code == 409
    with db.session_scope() as session:
        session.get(CommissionStatement, st_id).disputed_at = utcnow() - timedelta(days=15)
    no_reason = client.post(f"/admin/api/commission/statements/{st_id}/resolve-dispute", json={"reason": "", "outcome": "clear_block"}, headers=admin)
    assert no_reason.status_code == 422
    done = client.post(f"/admin/api/commission/statements/{st_id}/resolve-dispute",
                       json={"reason": "Reviewed both sides' evidence", "outcome": "clear_block"}, headers=admin)
    assert done.status_code == 200 and done.json()["status"] == "resolved"
    assert not commission.collab_block(env["shop"])["blocked"]
    log = client.get("/admin/api/commission/audit", params={"statement_id": st_id}, headers=admin).json()
    resolved = [row for row in log if row["action"] == "owed_dispute_resolved"][0]
    assert resolved["actor"] == "admin@example.com" and resolved["reason"] == "Reviewed both sides' evidence"


def test_admin_lift_block_covers_current_statements_only(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    _set_overdue(st_id)
    admin = _admin_headers(monkeypatch)
    lifted = client.post(f"/admin/api/shops/{env['shop']}/collab-block/lift", json={"reason": "Seller showed proof of payment"}, headers=admin)
    assert lifted.status_code == 200 and not lifted.json()["blocked"]


def test_shortlist_acceptance_is_blocked(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    _set_overdue(st_id)
    with db.session_scope() as session:
        shortlist = Shortlist(id=str(uuid.uuid4()), shop_id=env["shop"], product_id=env["product"], match_version="t")
        session.add(shortlist)
        session.flush()
        item = ShortlistItem(id=str(uuid.uuid4()), shortlist_id=shortlist.id, creator_id=env["creator2"], score=1, reasons={}, rank=1)
        session.add(item)
        item_id = item.id
    import services

    with pytest.raises(Exception) as caught:
        services.decide_shortlist_item(item_id, "match_accepted")
    assert getattr(caught.value, "status_code", None) == 409


def test_pays_on_time_visibility_and_response(env, monkeypatch):
    seller = _seller_headers(env["shop"])
    too_long = client.put("/seller/api/pays-on-time/response", json={"text": "x" * 281}, headers=seller)
    assert too_long.status_code == 422
    saved = client.put("/seller/api/pays-on-time/response", json={"text": "We pay on the 5th of each month."}, headers=seller)
    assert saved.json()["seller_response"] == "We pay on the 5th of each month."
    creator = _creator_headers(env["creator2"])
    # Default (Ross): seller plus creators viewing that seller's briefs. A creator with no link to the seller sees nothing.
    assert commission.pays_on_time_visibility() == "seller_and_brief_creators"
    assert client.get(f"/creator/api/shops/{env['shop']}/pays-on-time", headers=creator).status_code == 404
    commission.create_offer(env["shop"], actor="s", ip=None, creator_id=env["creator2"], product_id=None, shortlist_item_id=None,
                            commission_rate=10, payment_terms_days=None)
    shown = client.get(f"/creator/api/shops/{env['shop']}/pays-on-time", headers=creator)
    assert shown.json()["text"] == "No payments have fallen due yet"
    assert shown.json()["seller_response"] == "We pay on the 5th of each month."
    monkeypatch.setenv("PAYS_ON_TIME_VISIBILITY", "seller")
    assert client.get(f"/creator/api/shops/{env['shop']}/pays-on-time", headers=creator).status_code == 404
    # Never public: no session, no record.
    assert client.get(f"/creator/api/shops/{env['shop']}/pays-on-time").status_code in (401, 503)


# ------------------------------------------------------------------ agreements + ABN

def test_collab_offer_stores_terms_and_both_acceptances(env):
    seller = _seller_headers(env["shop"])
    offer = client.post("/seller/api/collabs", json={"creator_id": env["creator2"], "product_id": env["product"], "commission_rate": 12,
                                                    "payment_terms_days": 14}, headers=seller)
    assert offer.status_code == 200, offer.text
    body = offer.json()
    assert "abn_warning" not in body
    assert body["terms_sha256"] == hashlib.sha256(body["terms_text"].encode()).hexdigest()
    creator = _creator_headers(env["creator2"], "omar@example.com")
    stale = client.post(f"/creator/api/collabs/{body['id']}/accept", json={"terms_version": body["terms_version"], "terms_sha256": "0" * 64}, headers=creator)
    assert stale.status_code == 409
    accepted = client.post(f"/creator/api/collabs/{body['id']}/accept",
                           json={"terms_version": body["terms_version"], "terms_sha256": body["terms_sha256"]}, headers=creator)
    assert accepted.json()["status"] == "active"
    with db.session_scope() as session:
        row = session.get(CollabAgreement, body["id"])
        assert row.seller_accepted_by == "seller@sunny.example" and row.creator_accepted_by == "omar@example.com"
        assert row.seller_accepted_at and row.creator_accepted_at and row.terms_text == body["terms_text"]


def test_no_abn_creator_needs_seller_acknowledgement(env):
    seller = _seller_headers(env["shop"])
    first = client.post("/seller/api/collabs", json={"creator_id": env["creator"], "commission_rate": 10}, headers=seller)
    assert first.status_code == 422
    assert "47%" in first.json()["detail"]["abn_warning"]["text"]
    ok = client.post("/seller/api/collabs", json={"creator_id": env["creator"], "commission_rate": 10, "abn_warning_acknowledged": True}, headers=seller)
    assert ok.status_code == 200
    assert "accountant" in ok.json()["abn_warning"]["text"]
    view_ack = client.post("/seller/api/commission/abn-ack", json={"creator_id": env["creator"], "context": "collab_view",
                                                                  "reference_id": ok.json()["id"]}, headers=seller)
    assert view_ack.json() == {"acknowledged": True, "needed": True}
    with db.session_scope() as session:
        acks = session.scalars(select(AbnWarningAck)).all()
        assert {a.context for a in acks} == {"collab_offer", "collab_view"}
        assert all(a.acknowledged_by == "seller@sunny.example" and a.warning_version == commission.ABN_WARNING_VERSION for a in acks)


def test_no_abn_warning_on_statements_and_ack(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    seller = _seller_headers(env["shop"])
    listed = client.get("/seller/api/commission/statements", headers=seller).json()["statements"][0]
    assert listed["abn_warning"]["acknowledged"] is False
    client.post("/seller/api/commission/abn-ack", json={"creator_id": env["creator"], "context": "statement", "reference_id": st_id}, headers=seller)
    listed = client.get("/seller/api/commission/statements", headers=seller).json()["statements"][0]
    assert listed["abn_warning"]["acknowledged"] is True
    creator_view = client.get("/creator/api/commission/statements", headers=_creator_headers(env["creator"])).json()["statements"][0]
    assert "abn_warning" not in creator_view


def test_creator_abn_is_optional_and_validated(env):
    creator = _creator_headers(env["creator"])
    assert client.put("/creator/api/abn", json={"abn": "12345678901"}, headers=creator).status_code == 422
    assert client.put("/creator/api/abn", json={"abn": VALID_ABN}, headers=creator).json() == {"has_abn": True}
    seller = _seller_headers(env["shop"])
    assert client.post("/seller/api/collabs", json={"creator_id": env["creator"], "commission_rate": 10}, headers=seller).status_code == 200
    assert client.put("/creator/api/abn", json={"abn": ""}, headers=creator).json() == {"has_abn": False}


def test_bank_detail_filter():
    assert commission.looks_like_bank_details("PayID 0412 345 678")
    assert commission.looks_like_bank_details("BSB 062000")
    assert commission.looks_like_bank_details("acct no 12345678")
    assert not commission.looks_like_bank_details("Paid by transfer on 5 Nov, ref NOV-PLANTR")


def test_no_bank_fields_in_schema():
    from models import Base

    banned = ("bsb", "account_number", "payid", "paypal", "iban", "swift", "bank")
    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert not any(word in column.name.lower() for word in banned), f"{table.name}.{column.name}"


def test_seller_accepted_page_shows_no_abn_warning(env):
    with db.session_scope() as session:
        shortlist = Shortlist(id=str(uuid.uuid4()), shop_id=env["shop"], product_id=env["product"], match_version="t")
        session.add(shortlist)
        session.flush()
        for key in ("creator", "creator2"):
            session.add(ShortlistItem(id=str(uuid.uuid4()), shortlist_id=shortlist.id, creator_id=env[key], score=1, reasons={},
                                      rank=1, accepted_at=utcnow()))
    page = client.get("/seller/accepted", headers={"Cookie": _seller_headers(env["shop"])["Cookie"]})
    assert page.status_code == 200
    assert page.text.count("withhold 47%") == 1



def test_shopify_collabs_offer_carries_double_payment_warning(env):
    seller = _seller_headers(env["shop"])
    offer = client.post("/seller/api/collabs", json={"creator_id": env["creator2"], "commission_rate": 10, "paid_via": "shopify_collabs"},
                        headers=seller).json()
    assert offer["excluded_from_owed"] is True
    assert "pay twice" in offer["double_payment_warning"]
    tracked = client.post("/seller/api/collabs", json={"creator_id": env["creator2"], "commission_rate": 10}, headers=seller).json()
    assert "double_payment_warning" not in tracked
    assert "double_payment_warning" not in client.get(f"/creator/api/collabs", headers=_creator_headers(env["creator2"])).json()[0]


# ------------------------------------------------------------------ seller corrections

def _late_entry(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    _set_overdue(st_id, days_ago=10)
    late_day = date.today() - timedelta(days=2)
    commission.mark_paid(env["shop"], st_id, paid_on=late_day.isoformat(), reference=None, actor="s")
    return st_id


def _correction(env, st_id, **extra):
    body = {"reason": "Paid on time by bank transfer; marked it late by mistake", "proposed_paid_on": (date.today() - timedelta(days=12)).isoformat()}
    body.update(extra)
    return client.post(f"/seller/api/commission/statements/{st_id}/corrections", json=body, headers=_seller_headers(env["shop"]))


def test_correction_only_for_late_or_missed_entries_and_validated(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    assert _correction(env, st_id).status_code == 409  # not late or missed yet
    _set_overdue(st_id, days_ago=10)
    assert _correction(env, st_id, reason="short").status_code == 422
    assert _correction(env, st_id, evidence_text="BSB 062-000 acct 12345678").status_code == 422
    assert _correction(env, st_id, evidence_url="ftp://x").status_code == 422
    assert _correction(env, st_id, proposed_paid_on=date.today().isoformat()).status_code == 422  # still after due date
    ok = _correction(env, st_id, evidence_text="Bank receipt dated before the due date", evidence_url="https://drive.example/receipt")
    assert ok.status_code == 200 and ok.json()["entry_kind"] == "missed"
    assert _correction(env, st_id).status_code == 409  # one open correction per entry


def test_pending_correction_shows_under_review_and_leaves_record(env, monkeypatch):
    st_id = _late_entry(env, monkeypatch)
    seller = _seller_headers(env["shop"])
    assert client.get("/seller/api/pays-on-time", headers=seller).json()["text"] == "0 of 1 paid on time"
    assert _correction(env, st_id).status_code == 200
    record = client.get("/seller/api/pays-on-time", headers=seller).json()
    assert record["total"] == 0 and record["under_review"] == 1
    detail = client.get(f"/seller/api/commission/statements/{st_id}", headers=seller).json()
    assert detail["display"] == {"code": "under_review", "label": "Under review"}
    assert detail["corrections"][0]["status"] == "pending_creator"


def test_creator_agrees_and_entry_is_corrected(env, monkeypatch):
    st_id = _late_entry(env, monkeypatch)
    corr = _correction(env, st_id).json()
    creator = _creator_headers(env["creator"])
    agreed = client.post(f"/creator/api/commission/corrections/{corr['id']}/agree", json={}, headers=creator)
    assert agreed.status_code == 200 and agreed.json()["status"] == "agreed"
    assert client.get("/seller/api/pays-on-time", headers=_seller_headers(env["shop"])).json()["text"] == "1 of 1 paid on time"
    again = client.post(f"/creator/api/commission/corrections/{corr['id']}/disagree", json={"reason": "changed my mind"}, headers=creator)
    assert again.status_code == 409


def test_silence_counts_as_agreement_after_window(env, monkeypatch):
    monkeypatch.setenv("COMMISSION_CORRECTION_WINDOW_DAYS", "7")
    st_id = _late_entry(env, monkeypatch)
    corr = _correction(env, st_id).json()
    assert (datetime.fromisoformat(corr["respond_by"].replace("Z", "+00:00")) - datetime.fromisoformat(corr["requested_at"].replace("Z", "+00:00"))).days == 7
    later = utcnow() + timedelta(days=8)
    assert commission.settle_expired_corrections(now=later) == {"deemed_agreed": [corr["id"]]}
    assert commission.pays_on_time(env["shop"])["text"] == "1 of 1 paid on time"
    late = client.post(f"/creator/api/commission/corrections/{corr['id']}/disagree", json={"reason": "Too late now"},
                       headers=_creator_headers(env["creator"]))
    assert late.status_code == 409
    log = commission.audit_log(statement_id=st_id)
    assert [row["action"] for row in log if row["action"].startswith("correction")] == ["correction_requested", "correction_deemed_agreed"]


def test_missed_entry_deemed_agreement_marks_it_paid_and_clears_block(env, monkeypatch):
    st_id, _ = _statement_for(env, monkeypatch)
    _set_overdue(st_id, days_ago=10)
    assert commission.collab_block(env["shop"])["blocked"]
    corr = _correction(env, st_id).json()
    commission.settle_expired_corrections(now=utcnow() + timedelta(days=15))
    detail = commission.statement_detail(st_id, viewer="seller")
    assert detail["status"] == "marked_paid" and detail["marked_paid_on"] == date.fromisoformat(corr["proposed_paid_on"])
    assert not commission.collab_block(env["shop"])["blocked"]


def test_creator_disagrees_and_admin_corrects_or_keeps_with_logged_reason(env, monkeypatch):
    st_id = _late_entry(env, monkeypatch)
    corr = _correction(env, st_id).json()
    creator = _creator_headers(env["creator"])
    assert client.post(f"/creator/api/commission/corrections/{corr['id']}/disagree", json={}, headers=creator).status_code == 422
    disagreed = client.post(f"/creator/api/commission/corrections/{corr['id']}/disagree", json={"reason": "Money arrived late"}, headers=creator)
    assert disagreed.json()["status"] == "disagreed"
    # Still under review and still out of the record; silence rule no longer applies.
    commission.settle_expired_corrections(now=utcnow() + timedelta(days=30))
    assert commission.pays_on_time(env["shop"])["under_review"] == 1
    admin = _admin_headers(monkeypatch)
    queue = client.get("/admin/api/commission/corrections", params={"status": "disagreed"}, headers=admin).json()
    assert [c["id"] for c in queue] == [corr["id"]]
    assert client.post(f"/admin/api/commission/corrections/{corr['id']}/decide", json={"outcome": "keep", "reason": "short"}, headers=admin).status_code == 422
    kept = client.post(f"/admin/api/commission/corrections/{corr['id']}/decide",
                       json={"outcome": "keep", "reason": "Creator's bank statement shows the later date"}, headers=admin)
    assert kept.json()["status"] == "admin_kept"
    assert commission.pays_on_time(env["shop"])["text"] == "0 of 1 paid on time"
    log = client.get("/admin/api/commission/audit", params={"statement_id": st_id}, headers=admin).json()
    decided = [row for row in log if row["action"] == "correction_kept"][0]
    assert decided["actor"] == "admin@example.com" and decided["reason"] == "Creator's bank statement shows the later date"
    # A new correction can then be raised, and an admin can correct it.
    second = _correction(env, st_id).json()
    client.post(f"/creator/api/commission/corrections/{second['id']}/disagree", json={"reason": "Still late"}, headers=creator)
    fixed = client.post(f"/admin/api/commission/corrections/{second['id']}/decide",
                        json={"outcome": "correct", "reason": "Seller's receipt shows payment before the due date"}, headers=admin)
    assert fixed.json()["status"] == "admin_corrected"
    assert commission.pays_on_time(env["shop"])["text"] == "1 of 1 paid on time"
    actions = [row["action"] for row in commission.audit_log(statement_id=st_id) if row["action"].startswith("correction")]
    assert actions == ["correction_requested", "correction_disagreed", "correction_kept",
                       "correction_requested", "correction_disagreed", "correction_corrected"]
