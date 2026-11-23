"""Listing Studio generation, with every external call mocked."""

import json

import pytest

import listing_studio


class FakeClient:
    def __init__(self, *, fail_times=0):
        self.fail_times = fail_times
        self.checks = 0
        self.prompts = []

    def complete_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "angles": [
                    {"name": "Tuesday place", "insight": "Used, not displayed.", "moment": "A weeknight.", "hook": "Ready when you are.", "caption": "On the table. #ad"},
                    {"name": "One less step", "insight": "No extra step.", "moment": "Before leaving.", "hook": "Nothing extra.", "caption": "As it is. #ad"},
                    {"name": "Leave it out", "insight": "Ordinary enough for Tuesday.", "moment": "After the meal.", "hook": "Leave it out.", "caption": "Stays out. #ad"},
                    {"name": "Skip", "insight": "the best serum", "moment": "x", "hook": "x", "caption": "customers say it works"},
                ]
            }
        )

    def edit_image(self, *, model: str, prompt: str, reference: str) -> dict:
        self.prompts.append(prompt)
        return {"image": f"{model}:{reference}", "model": model}

    def check_image(self, *, output: str, reference: str, sheet: dict) -> dict:
        self.checks += 1
        if self.checks <= self.fail_times:
            return {"passed": False, "reasons": ["Button count is 3, sheet says 2"]}
        return {"passed": True, "reasons": []}


def _product():
    return {"id": "sku-1", "title": "Stoneware bowl", "category": "home", "image_url": "packshot.jpg"}


def test_positioning_drops_unverifiable_claims_and_prompts_lock_the_product():
    listing_studio.reset()
    sheet = listing_studio.draft_sheet(_product())
    sheet["confirmed"] = True
    listing_studio.lock_sheet("sku-1", sheet)
    fake = FakeClient()
    state = listing_studio.generate(_product(), supplier_id="shop-1", client=fake)
    assert len(state["angles"]) == 3
    assert all(row["caption"].startswith("#Ad") for row in state["angles"])
    assert all("best" not in row["insight"].lower() for row in state["angles"])
    assert "Never add, remove, or change" in fake.prompts[0]
    assert "No plants" in fake.prompts[1]
    assert state["scenes"][0]["status"] == "ready"
    assert state["scenes"][0]["provider"] == listing_studio.MAIN_MODEL


def test_product_check_retries_then_holds_and_tracks_fail_rate():
    listing_studio.reset()
    sheet = {**listing_studio.draft_sheet(_product()), "confirmed": True}
    listing_studio.lock_sheet("sku-1", sheet)
    fake = FakeClient(fail_times=99)
    state = listing_studio.generate(_product(), supplier_id="shop-1", client=fake, scenes=[{"type": "in a home", "setting": "shelf", "mood": "morning light", "model": "no people", "season": "early autumn"}])
    scene = state["scenes"][0]
    assert scene["status"] == "held"
    assert scene["tries"] == 3
    assert listing_studio.holds()
    assert listing_studio.fail_rates()
    listing_studio.release_hold(scene["id"])
    assert listing_studio.holds() == []


def test_missing_keys_do_not_call_out_and_caps_stop_spend(monkeypatch):
    listing_studio.reset()
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    sheet = {**listing_studio.draft_sheet(_product()), "confirmed": True}
    listing_studio.lock_sheet("sku-1", sheet)
    state = listing_studio.generate(_product(), supplier_id="shop-1")
    assert state["status"] == "Generation unavailable"
    assert "Generation unavailable" in state["message"]
    monkeypatch.setenv("LISTING_DAILY_SPEND_CENTS", "0")
    blocked = listing_studio.generate(_product(), supplier_id="shop-1", client=FakeClient())
    assert "spend cap" in blocked["message"]


def test_product_inaccuracy_is_free_and_a_later_brand_change_uses_a_credit():
    listing_studio.reset()
    sheet = {**listing_studio.draft_sheet(_product()), "confirmed": True}
    listing_studio.lock_sheet("sku-1", sheet)
    state = listing_studio.generate(_product(), supplier_id="shop-1", client=FakeClient(), scenes=[{"type": "held", "setting": "hands", "mood": "soft overcast", "model": "hands", "season": "early autumn"}])
    scene_id = state["scenes"][0]["id"]
    listing_studio.review_scene("sku-1", scene_id, decision="request changes", tag="product inaccurate")
    assert listing_studio.state_for("sku-1")["scenes"][0]["free_retry"] is True
    listing_studio.review_scene("sku-1", scene_id, decision="request changes", tag="off brand")
    assert listing_studio.state_for("sku-1")["change_rounds"] == 1
    listing_studio.review_scene("sku-1", scene_id, decision="request changes", tag="wrong positioning")
    assert listing_studio.state_for("sku-1")["scenes"][0]["free_retry"] is False
    listing_studio.review_scene("sku-1", scene_id, decision="approve")
    shown = listing_studio.approved_for_creatr("sku-1")
    assert shown["scenes"] and shown["scenes"][0]["approved"] is True


def test_apparel_sheet_blocks_generation_until_sleeve_length_is_confirmed():
    listing_studio.reset()
    product = {"id": "knit", "title": "Ribbed henley", "category": "apparel", "image_url": "knit.jpg"}
    sheet = listing_studio.draft_sheet(product)
    sheet["confirmed"] = True
    listing_studio.lock_sheet("knit", sheet)
    state = listing_studio.generate(product, supplier_id="shop-1", client=FakeClient())
    assert "attribute sheet" in state["message"]
    sheet["sleeve_length"] = "long"
    listing_studio.lock_sheet("knit", sheet)
    ready = listing_studio.generate(product, supplier_id="shop-1", client=FakeClient())
    assert ready["scenes"]


def test_prices_have_no_ninety_nine_cent_pack():
    text = open("listing_studio.py", encoding="utf-8").read() + open("portal.py", encoding="utf-8").read()
    assert "A$0.99" not in text
    assert "0.99" not in text
