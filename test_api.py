"""Offline API tests. xAI is mocked; nothing listens on localhost."""

import json

from fastapi.testclient import TestClient

import grok_helper
import main

client = TestClient(main.app)

INFLUENCER = {
    "handle": "@creator",
    "name": "Alex",
    "areas_of_interest": ["alpha", "beta"],
    "preferred_promotion_styles": ["educational", "lifestyle"],
    "availability": "open",
}

PRODUCT = {
    "title": "Sample item",
    "description_short": "Short description",
    "description_long": "Longer description.",
    "category": "alpha",
    "images": ["url1", "url2", "url3"],
}


def _grok_response(usage=None):
    content = json.dumps(
        {
            "talking_points": ["Clear benefit", "Who it is for"],
            "faqs": [{"question": "What is included?", "answer": "See the product details."}],
        }
    )

    class Response:
        text = content

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": content}}],
                "usage": usage
                or {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }

    return Response()


def test_root_shape_and_version():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "grok_enabled"}
    assert body["status"] == "Plantr Collab API running"
    assert body["version"] == "0.10"
    assert body["grok_enabled"] is False


def test_root_stays_public_when_api_keys_are_set(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key")
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["version"] == "0.10"


def test_enrich_fallback_without_xai_key():
    response = client.post("/enrich", json=PRODUCT)
    assert response.status_code == 200
    body = response.json()
    assert body["signal_score"] == 70
    assert body["source"] == "fallback"
    assert body["creator_talking_points"][0].startswith("Meet ")
    assert body["enriched"]["title"] == PRODUCT["title"]
    assert "common_questions" in body


def test_enrich_uses_grok_and_logs_usage(monkeypatch, capsys):
    monkeypatch.setattr(grok_helper, "GROK_API_KEY", "test-key")

    def fake_post(url, headers, json, timeout):
        assert url == grok_helper.GROK_URL
        assert json["model"] == grok_helper.GROK_MODEL
        assert headers["Authorization"] == "Bearer test-key"
        return _grok_response()

    monkeypatch.setattr(grok_helper.requests, "post", fake_post)
    response = client.post("/enrich", json=PRODUCT)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "grok"
    assert body["creator_talking_points"] == ["Clear benefit", "Who it is for"]
    assert "usage" not in body
    logged = capsys.readouterr().out
    assert "Grok usage:" in logged
    assert '"total_tokens": 20' in logged


def test_enrich_falls_back_when_xai_returns_an_error(monkeypatch, capsys):
    monkeypatch.setattr(grok_helper, "GROK_API_KEY", "test-key")

    class BadResponse:
        text = '{"error":"model retired"}'

        def raise_for_status(self):
            raise RuntimeError("400 Client Error")

    monkeypatch.setattr(grok_helper.requests, "post", lambda *args, **kwargs: BadResponse())
    response = client.post("/enrich", json=PRODUCT)
    assert response.status_code == 200
    assert response.json()["source"] == "fallback"
    assert "model retired" in capsys.readouterr().out


def test_api_key_required_only_when_configured(monkeypatch):
    open_response = client.post("/enrich", json=PRODUCT)
    assert open_response.status_code == 200

    monkeypatch.setenv("API_KEYS", "alpha, beta")
    missing = client.post("/enrich", json=PRODUCT)
    assert missing.status_code == 401
    wrong = client.post("/enrich", json=PRODUCT, headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    accepted = client.post("/enrich", json=PRODUCT, headers={"X-API-Key": "beta"})
    assert accepted.status_code == 200


def test_protected_routes_accept_a_configured_key(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key")
    headers = {"X-API-Key": "secret-key"}

    onboard = client.post("/onboard-influencer", json=INFLUENCER, headers=headers)
    assert onboard.status_code == 200

    match = client.post(
        "/match",
        json={"influencer": INFLUENCER, "products": [PRODUCT]},
        headers=headers,
    )
    assert match.status_code == 200

    sync = client.post("/sync-shopify", headers=headers)
    assert sync.status_code == 501

    blocked = client.post("/sync-shopify")
    assert blocked.status_code == 401


def test_onboard_returns_an_id_and_keeps_the_record():
    first = client.post("/onboard-influencer", json=INFLUENCER)
    assert first.status_code == 200
    body = first.json()
    assert body["id"]
    assert body["influencer"]["influencer_id"] == body["id"]
    assert body["influencer"]["handle"] == "@creator"
    assert main.INFLUENCERS[body["id"]]["areas_of_interest"] == INFLUENCER["areas_of_interest"]

    second = client.post("/onboard-influencer", json=INFLUENCER)
    assert second.json()["id"] != body["id"]


def test_onboard_rejects_a_profile_outside_the_schema():
    missing = client.post("/onboard-influencer", json={"handle": "@creator"})
    assert missing.status_code == 422

    bad_style = dict(INFLUENCER, preferred_promotion_styles=["not-a-style"])
    assert client.post("/onboard-influencer", json=bad_style).status_code == 422

    bad_availability = dict(INFLUENCER, availability="always")
    assert client.post("/onboard-influencer", json=bad_availability).status_code == 422


def test_match_returns_scored_products():
    products = [
        {
            "title": "Sample one",
            "category": "alpha",
            "target_audience": ["beginners"],
            "care_instructions": "Use as directed",
            "occasions": ["everyday"],
        },
        {"title": "Sample two", "category": "other", "target_audience": ["experts"]},
    ]
    response = client.post(
        "/match",
        json={"influencer": INFLUENCER, "products": products},
    )
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert [item["product"]["title"] for item in matches] == ["Sample one", "Sample two"]
    assert matches[0]["match_score"] > matches[1]["match_score"]


def test_sync_shopify_is_an_honest_stub(monkeypatch):
    missing = client.post("/sync-shopify")
    assert missing.status_code == 501
    assert "isn't configured" in missing.json()["detail"]
    assert "synced" not in missing.json()

    monkeypatch.setenv("SHOPIFY_STORE", "brand.myshopify.com")
    still_missing = client.post("/sync-shopify")
    assert still_missing.status_code == 501
    assert "isn't configured" in still_missing.json()["detail"]

    monkeypatch.setenv("SHOPIFY_TOKEN", "shpat_test")
    configured = client.post("/sync-shopify")
    assert configured.status_code == 501
    assert "not implemented" in configured.json()["detail"]
    assert "synced" not in configured.json()


def test_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    assert client.post("/enrich", json=PRODUCT).status_code == 200
    assert client.post("/enrich", json=PRODUCT).status_code == 200
    blocked = client.post("/enrich", json=PRODUCT)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Rate limit exceeded"
    assert blocked.headers["retry-after"] == "60"


def test_body_size_limit(monkeypatch):
    monkeypatch.setenv("MAX_BODY_BYTES", "40")
    response = client.post("/enrich", json={"title": "x" * 200})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large"


def test_cors_allows_configured_origins():
    response = client.get("/", headers={"Origin": "https://brand.example"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_origins_parser(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert main.cors_origins() == ["*"]
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    assert main.cors_origins() == ["https://a.example", "https://b.example"]


def test_html_pages_use_the_same_origin():
    for page in main.HTML_PAGES:
        response = client.get(f"/{page}")
        assert response.status_code == 200
        assert "localhost:8000" not in response.text
        assert "window.location.origin" in response.text
        assert "plantrApiBase" in response.text
