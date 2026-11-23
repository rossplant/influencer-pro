"""Quality warnings, the admin exception, and the review queue."""

from fastapi.testclient import TestClient

import db
import main
import quality_standing
from tests.test_admin import EMAIL, PASSWORD, _cookie_header, _login, _setup

client = TestClient(main.app)


def test_warning_email_excuse_and_review_queue(monkeypatch):
    _setup(monkeypatch)
    standing = quality_standing.note_month("creator", "c1", "2026-11", passed=False, email="ada@example.com")
    assert standing["warning"] is True
    assert standing["demoted"] is False
    assert any(row["template_key"] == "quality_warning" for row in db.memory_emails)
    quality_standing.note_month("creator", "c1", "2026-12", passed=False, email="ada@example.com")
    assert quality_standing.standing_for("creator", "c1", sales=40)["label"] == "Contributr"
    admin = _cookie_header(_login(password=PASSWORD, email=EMAIL))
    missing = client.post(
        "/admin/api/quality/excuse",
        json={"subject_type": "creator", "subject_id": "c1", "month": "2026-11", "reason": "other"},
        headers=admin,
    )
    assert missing.status_code == 422
    excused = client.post(
        "/admin/api/quality/excuse",
        json={
            "subject_type": "creator",
            "subject_id": "c1",
            "month": "2026-11",
            "reason": "supplier_or_courier_delay",
            "note": "Courier held the sample",
        },
        headers=admin,
    )
    assert excused.status_code == 200
    audit = excused.json()["audit"]
    assert audit["who"] == EMAIL
    assert audit["reason"] == "supplier_or_courier_delay"
    assert audit["when"]
    assert quality_standing.standing_for("creator", "c1", sales=40)["demoted"] is False
    logged = client.post(
        "/admin/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert logged.status_code == 200
    shop = client.post("/shops", json={"name": "Review Shop"}).json()
    csrf = client.get("/admin", headers=admin).text
    # Seller session is not required for the queue item itself.
    item = quality_standing.request_review("supplier", shop["id"], month="2026-12", note="The courier was late")
    queued = client.get("/admin/api/quality/queue", headers=admin)
    assert queued.status_code == 200
    assert item["id"] in [row["id"] for row in queued.json()["items"]]
    client.post(
        "/admin/api/quality/excuse",
        json={
            "subject_type": "supplier",
            "subject_id": shop["id"],
            "month": "2026-12",
            "reason": "platform_error",
            "review_id": item["id"],
            "restore": True,
        },
        headers=admin,
    )
    assert item["id"] not in [row["id"] for row in quality_standing.queue()]
    stored = next(row for row in db.memory_events if row.event_name == "quality_exception" and row.properties.get("review_id") == item["id"])
    assert stored.properties["who"] == EMAIL
    assert "password" not in stored.properties
