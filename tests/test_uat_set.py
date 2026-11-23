"""UAT create builds the supplier and Creatr in a fresh database."""

from sqlalchemy import func, select

import db
import mailer
import main
import uat_mode
from models import Creator, EmailMessage, PortalAccount, Product, Shop, UatSet
from tests.test_persistence import use_database


def _emails():
    with db.session_scope() as session:
        rows = session.scalars(select(EmailMessage).order_by(EmailMessage.created_at.asc())).all()
        return [{"to_email": row.to_email, "body": row.body} for row in rows]


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


def _deliver(monkeypatch, provider_id="re_test_123"):
    class Ok:
        def send(self, **kwargs):
            return {"status": mailer.SENT, "id": provider_id}

    monkeypatch.setattr(mailer, "default", lambda: Ok())


def test_create_then_send_stores_the_resend_id(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _deliver(monkeypatch)
    uat_mode.create_set("partner@example.com")
    sent = uat_mode.send_invite("press-1", "https://staging.example")
    assert sent["sent"] is True
    emails = _emails()
    assert len(emails) == 1
    assert emails[0]["to_email"] == "partner@example.com"
    assert "supplier" in emails[0]["body"].lower()
    assert emails[0]["body"].count("/uat/invite/") == 2
    current = uat_mode.current_set()
    assert current["invite_sent_at"]
    assert current["invite_provider_id"] == "re_test_123"
    with db.session_scope() as session:
        row = session.scalar(select(UatSet))
        assert row.invite_provider_id == "re_test_123"


def test_the_same_press_does_not_send_twice(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _deliver(monkeypatch)
    uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    again = uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    assert again["duplicate"] is True
    assert len(_emails()) == 1
    fresh = uat_mode.send_invite("press-2", "https://staging.example")
    assert fresh["sent"] is True
    assert len(_emails()) == 2


def test_a_failed_send_is_not_marked_sent_and_a_fresh_press_can_retry(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    class Failed:
        def send(self, **kwargs):
            return {"status": mailer.FAILED, "id": None, "reason": "HTTP 403"}

    monkeypatch.setattr(mailer, "default", lambda: Failed())
    failed = uat_mode.create_and_send("partner@example.com", "press-fail", "https://staging.example")
    assert failed["sent"] is False
    assert failed["reason"] == "HTTP 403"
    assert uat_mode.current_set()["invite_sent_at"] is None
    assert uat_mode.current_set().get("invite_provider_id") in (None, "")
    _deliver(monkeypatch, "re_retry")
    retried = uat_mode.send_invite("press-retry", "https://staging.example")
    assert retried["sent"] is True
    assert uat_mode.current_set()["invite_provider_id"] == "re_retry"
