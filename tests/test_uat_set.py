"""UAT create builds the supplier and Creatr in a fresh database."""

from sqlalchemy import func, select

import db
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
    assert second["shop_id"] == first["shop_id"]
    assert second["reused"] is True
    with db.session_scope() as session:
        shops = session.scalars(select(Shop)).all()
        assert len(shops) == 1
        assert shops[0].name == "Homeware supplier"
        assert shops[0].business_name == "Homeware supplier"
        titles = set(session.scalars(select(Product.title).where(Product.shop_id == shops[0].id)).all())
        assert titles == {"Stoneware bowl", "Linen napkin", "Oak board"}
        roles = set(
            session.scalars(select(PortalAccount.role).where(PortalAccount.email == "partner@example.com")).all()
        )
        assert roles == {"seller", "creator"}
        creator = session.get(Creator, first["creator_id"])
        assert creator.avg_views == 45000
        assert creator.profile["audience"] == 45000
        assert session.scalar(select(func.count()).select_from(UatSet)) == 1


def test_create_then_send_once(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
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


def test_the_same_press_does_not_send_twice(tmp_path, monkeypatch):
    use_database(tmp_path, monkeypatch)
    monkeypatch.setenv("UAT_MODE", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    again = uat_mode.create_and_send("partner@example.com", "press-1", "https://staging.example")
    assert again["duplicate"] is True
    assert len(_emails()) == 1
    fresh = uat_mode.send_invite("press-2", "https://staging.example")
    assert fresh["sent"] is True
    assert len(_emails()) == 2
