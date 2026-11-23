"""UAT create builds the supplier and Creatr in a fresh database."""

from sqlalchemy import func, select

import db
import mailer
import main
import uat_mode
from models import Creator, EmailMessage, PortalAccount, Product, Shop, UatInvite, UatSet
from tests.test_persistence import use_database


def _emails():
    with db.session_scope() as session:
        rows = session.scalars(select(EmailMessage).order_by(EmailMessage.created_at.asc())).all()
        return [{"to_email": row.to_email, "template_key": row.template_key, "body": row.body} for row in rows]


def _invites():
    with db.session_scope() as session:
        rows = session.scalars(select(UatInvite).order_by(UatInvite.role.asc())).all()
        return {row.role: {"status": row.status, "provider_id": row.provider_id, "last_error": row.last_error} for row in rows}


def test_create_from_an_empty_database_is_idempotent(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    first = uat_mode.create_set("partner@example.com")
    second = uat_mode.create_set("other@example.com")
    assert second["shop_id"] != first["shop_id"]
    assert second["reused"] is False
    again = uat_mode.create_set("partner@example.com")
    assert again["shop_id"] == first["shop_id"]
    assert again["reused"] is True
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Shop)) == 2
        products = session.scalars(select(Product).where(Product.shop_id == first["shop_id"])).all()
        assert products == []
        roles = set(
            session.scalars(select(PortalAccount.role).where(PortalAccount.email == "partner@example.com")).all()
        )
        assert roles == {"seller", "creator"}
        creator = session.get(Creator, first["creator_id"])
        assert creator.avg_views == 45000
        assert creator.profile["audience"] == 45000
        assert session.scalar(select(func.count()).select_from(UatSet)) == 2
        shop = session.get(Shop, first["shop_id"])
        assert shop.name == "Homeware supplier"


def _deliver(monkeypatch, supplier_id="re_supplier", creatr_id="re_creatr"):
    class Ok:
        def send(self, **kwargs):
            html_body = kwargs.get("html") or ""
            if "Set up my Creatr profile" in html_body:
                return {"status": mailer.SENT, "id": creatr_id}
            return {"status": mailer.SENT, "id": supplier_id}

    monkeypatch.setattr(mailer, "default", lambda: Ok())


def test_create_then_send_stores_a_resend_id_for_each_role(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _deliver(monkeypatch)
    uat_mode.create_set("partner@example.com")
    sent = uat_mode.send_invite("press-1", "https://staging.example")
    assert sent["sent"] is True
    emails = _emails()
    assert len(emails) == 2
    by_template = {row["template_key"]: row for row in emails}
    assert set(by_template) == {"supplier_invite", "creatr_invite"}
    assert {row["to_email"] for row in emails} == {"partner@example.com"}
    assert by_template["supplier_invite"]["body"].count("/uat/invite/") == 1
    assert "/uat/invite/s/" in by_template["supplier_invite"]["body"]
    assert by_template["creatr_invite"]["body"].count("/uat/invite/") == 1
    assert "/uat/invite/c/" in by_template["creatr_invite"]["body"]
    stored = _invites()
    assert stored["seller"]["provider_id"] == "re_supplier"
    assert stored["creator"]["provider_id"] == "re_creatr"
    assert stored["seller"]["status"] == "sent"
    assert stored["creator"]["status"] == "sent"
    assert uat_mode.current_set()["invite_sent_at"]


def test_the_same_press_does_not_send_twice(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _deliver(monkeypatch)
    uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    again = uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    assert again["duplicate"] is True
    assert len(_emails()) == 2
    fresh = uat_mode.send_invite("press-2", "https://staging.example")
    assert fresh["already"] is True
    assert len(_emails()) == 2


def test_a_failed_role_can_be_retried_alone(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    class OneFails:
        def send(self, **kwargs):
            html_body = kwargs.get("html") or ""
            if "Set up my Creatr profile" in html_body:
                return {"status": mailer.FAILED, "id": None, "reason": "HTTP 403"}
            return {"status": mailer.SENT, "id": "re_supplier"}

    monkeypatch.setattr(mailer, "default", lambda: OneFails())
    failed = uat_mode.create_and_send("partner@example.com", "press-fail", "https://staging.example")
    assert failed["sent"] is False
    assert failed["roles"]["seller"]["provider_id"] == "re_supplier"
    assert failed["roles"]["creator"]["reason"] == "HTTP 403"
    assert uat_mode.current_set()["invite_sent_at"] is None
    stored = _invites()
    assert stored["seller"]["status"] == "sent"
    assert stored["creator"]["status"] == "failed"
    assert stored["creator"]["last_error"] == "HTTP 403"
    _deliver(monkeypatch, supplier_id="re_should_not_send", creatr_id="re_creatr_retry")
    retried = uat_mode.send_invite("press-retry", "https://staging.example", role="creator")
    assert retried["sent"] is True
    assert "seller" not in retried["roles"] or retried["roles"]["seller"].get("skipped") is True
    stored = _invites()
    assert stored["seller"]["provider_id"] == "re_supplier"
    assert stored["creator"]["provider_id"] == "re_creatr_retry"
    assert stored["creator"]["status"] == "sent"
    assert len(_emails()) == 3
