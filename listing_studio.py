"""Listing Studio generation, product check, and supplier review.

External calls go through a client object. Tests pass a fake client.
API keys are never written to logs. Missing keys return a clear
unavailable state instead of raising.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import db

logger = logging.getLogger("plantr.listing_studio")

MAIN_MODEL = "google/nano-banana-2"
BACKUP_MODEL = "grok-imagine-image-2.0"
VISION_MODEL = "google/gemini-2.5-flash"
MAIN_CENTS = 10
BACKUP_CENTS = 11
VISION_CENTS = 1
TEXT_CENTS = 1
MAX_MAIN_TRIES = 2

HARD_RULES = (
    "Never add, remove, or change any product attribute. "
    "That includes garment type, sleeves, buttons, neckline, colour, pattern, logo, "
    "label text, shape, count, and material. "
    "Only improve the lighting and place the product as photographed. "
    "No plants, leaves, flowers, or plant props. "
    "No Plantr mark. Nothing that implies an unverified health, safety, eco, or best claim. "
    "No fake stats. Worn scenes use a diverse AI figure, no real likeness, mid-torso crop, no identifiable face."
)

BANNED_COPY = ("clinically", "eco-friendly", "the best", "safest", "guaranteed", "customers say", "unbreakable")
SCENE_TYPES = ("in a home", "held", "worn on a model", "in use")
REVIEW_TAGS = ("off brand", "wrong positioning", "product inaccurate", "wrong setting or model", "other")

DEFAULT_PACK = {
    "name": "core",
    "version": 1,
    "date": "2026-11-23",
    "negatives": (
        "Do not invent short sleeves.",
        "Do not add buttons.",
        "Do not turn a henley into a cardigan.",
        "Do not move lettering onto another piece.",
        "Do not add plant props.",
        "Worn scenes must be worn, mid-torso, with no identifiable face.",
    ),
}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def keys_ready() -> dict:
    return {
        "text": bool(os.getenv("XAI_API_KEY", "").strip()),
        "main_image": bool(os.getenv("REPLICATE_API_TOKEN", "").strip()),
        "backup_image": bool(os.getenv("XAI_API_KEY", "").strip()),
    }


def unavailable_message() -> str:
    ready = keys_ready()
    if ready["text"] and (ready["main_image"] or ready["backup_image"]):
        return ""
    return "Generation unavailable. Add the image and text services to create scenes."


def spend_cap_cents() -> int:
    return _int_env("LISTING_DAILY_SPEND_CENTS", 2000)


def supplier_daily_cap() -> int:
    return _int_env("LISTING_SUPPLIER_DAILY_CAP", 20)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _records():
    if not hasattr(db, "memory_listing"):
        db.memory_listing = {}
        db.memory_listing_failures = []
        db.memory_listing_rules = []
        db.memory_listing_holds = []
        db.memory_listing_spend = []
    return db.memory_listing


def reset() -> None:
    db.memory_listing = {}
    db.memory_listing_failures = []
    db.memory_listing_rules = [dict(DEFAULT_PACK)]
    db.memory_listing_holds = []
    db.memory_listing_spend = []


def rule_packs() -> list[dict]:
    _records()
    if not db.memory_listing_rules:
        db.memory_listing_rules.append(dict(DEFAULT_PACK))
    return db.memory_listing_rules


def current_pack(category: str = "") -> dict:
    packs = [row for row in rule_packs() if not row.get("category") or row.get("category") == category]
    return packs[-1]


def save_rule_pack(name: str, negatives: list[str], *, category: str = "") -> dict:
    previous = [row for row in rule_packs() if row.get("name") == name and row.get("category") == category]
    version = (previous[-1]["version"] + 1) if previous else 1
    pack = {
        "name": name,
        "version": version,
        "date": _today(),
        "category": category,
        "negatives": tuple(negatives),
    }
    db.memory_listing_rules.append(pack)
    return pack


def prompt_for(product: dict, sheet: dict, scene: dict, *, corrections: list[str] | None = None) -> str:
    pack = current_pack(str(product.get("category") or ""))
    brand = product.get("brand_preferences") or {}
    lines = [
        HARD_RULES,
        f"Rule pack {pack['name']} v{pack['version']} ({pack['date']}).",
        *pack.get("negatives", ()),
        f"Attribute sheet: {json.dumps(sheet, sort_keys=True)}",
        f"Scene: {scene.get('type')} in {scene.get('setting')}. Mood {scene.get('mood')}. Season {scene.get('season')}. Model {scene.get('model')}.",
        "Pass the reference packshot in. Do not invent a lookalike.",
    ]
    if brand:
        lines.append(
            "Brand preferences: "
            f"tone {brand.get('tone') or 'plain'}; "
            f"avoid {brand.get('avoid') or 'none'}; "
            f"model guidance {brand.get('model_guidance') or 'mid-torso, no face'}; "
            f"colours {brand.get('colours') or 'as photographed'}."
        )
    for note in corrections or []:
        lines.append(f"Correct this: {note}")
    return " ".join(lines)


def copy_problem(text: str) -> str:
    lowered = (text or "").lower()
    for phrase in BANNED_COPY:
        if phrase in lowered:
            return "Remove unverifiable claims. Stay inside the approved brief."
    return ""


def draft_sheet(product: dict) -> dict:
    category = str(product.get("category") or "general")
    title = str(product.get("title") or "Product")
    sheet = {
        "title": title,
        "category": category,
        "colour": "as photographed",
        "piece_count": "1",
        "label_text": title,
        "material": "as photographed",
        "shape": "as photographed",
        "confirmed": False,
    }
    if category in {"fashion", "apparel"}:
        sheet["garment_type"] = title
        sheet["sleeve_length"] = ""
        sheet["button_count"] = ""
        sheet["neckline"] = ""
    return sheet


def sheet_ready(sheet: dict) -> bool:
    if not sheet or not sheet.get("confirmed"):
        return False
    if str(sheet.get("category") or "") in {"fashion", "apparel"} and not str(sheet.get("sleeve_length") or "").strip():
        return False
    return True


def _angles_from_text(raw: str, product: dict) -> list[dict]:
    try:
        parsed = json.loads(raw)
        angles = list(parsed.get("angles") or [])
    except (TypeError, json.JSONDecodeError):
        angles = []
    clean = []
    for angle in angles[:4]:
        caption = str(angle.get("caption") or "").strip()
        if caption and "#ad" not in caption.lower():
            caption = f"{caption} #ad"
        blob = " ".join(str(angle.get(key) or "") for key in ("name", "insight", "moment", "hook", "caption"))
        if copy_problem(blob):
            continue
        clean.append(
            {
                "name": str(angle.get("name") or "Angle"),
                "insight": str(angle.get("insight") or ""),
                "moment": str(angle.get("moment") or ""),
                "hook": str(angle.get("hook") or ""),
                "caption": caption,
                "approved": False,
            }
        )
    if len(clean) >= 3:
        return clean
    title = str(product.get("title") or "this product")
    return [
        {
            "name": "Tuesday place",
            "insight": f"{title} earns a place by being used, not displayed.",
            "moment": "A weeknight when the table is already set.",
            "hook": f"{title}, ready when you are.",
            "caption": f"{title} is on the table for an ordinary night. #ad",
            "approved": False,
        },
        {
            "name": "One less step",
            "insight": "The usual telling adds steps the brief does not.",
            "moment": "The minute before leaving the house.",
            "hook": "Nothing extra to add.",
            "caption": f"Using {title} as it is. #ad",
            "approved": False,
        },
        {
            "name": "Leave it out",
            "insight": "Handsome enough to stay in view, ordinary enough for Tuesday.",
            "moment": "After the meal, before the lights go down.",
            "hook": "Leave it where you used it.",
            "caption": f"{title} stays out after use. #ad",
            "approved": False,
        },
    ]


def _spend_today(supplier_id: str) -> tuple[int, int]:
    day = _today()
    cents = 0
    count = 0
    for row in db.memory_listing_spend:
        if row["day"] != day:
            continue
        cents += int(row["cents"])
        if row["supplier_id"] == supplier_id:
            count += 1
    return cents, count


def _note_spend(supplier_id: str, cents: int, model: str) -> None:
    db.memory_listing_spend.append({"day": _today(), "supplier_id": supplier_id, "cents": cents, "model": model})


def _within_caps(supplier_id: str, cents: int) -> Optional[str]:
    spent, count = _spend_today(supplier_id)
    if count >= supplier_daily_cap():
        return "This supplier has reached today's generation cap."
    if spent + cents > spend_cap_cents():
        return "Today's Listing Studio spend cap has been reached."
    return None


def _log_failure(*, model: str, category: str, reason: str, passed_later: bool) -> None:
    db.memory_listing_failures.append(
        {
            "model": model,
            "category": category,
            "reason": reason,
            "passed_later": passed_later,
            "pack": f"{current_pack(category)['name']} v{current_pack(category)['version']}",
        }
    )


def fail_rates() -> list[dict]:
    _records()
    tally: dict[tuple[str, str], list[int]] = {}
    for row in db.memory_listing_failures:
        key = (row["model"], row["category"])
        hits = tally.setdefault(key, [0, 0])
        hits[0] += 1
        if row.get("passed_later"):
            hits[1] += 1
    report = []
    for (model, category), (fails, later) in sorted(tally.items()):
        report.append({"model": model, "category": category, "fails": fails, "passed_later": later})
    return report


def preferred_model(category: str) -> str:
    """Use the backup first when its fail count is lower for this category."""
    rates = {(row["model"], row["category"]): row["fails"] for row in fail_rates()}
    main = rates.get((MAIN_MODEL, category), 0)
    backup = rates.get((BACKUP_MODEL, category), 0)
    if backup and backup < main:
        return BACKUP_MODEL
    return MAIN_MODEL


class LiveClient:
    """Network client. Tests do not use this."""

    def complete_text(self, prompt: str) -> str:
        import urllib.request

        key = os.getenv("XAI_API_KEY", "").strip()
        body = json.dumps(
            {
                "model": os.getenv("GROK_MODEL", "grok-4.3"),
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            "https://api.x.ai/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode() or "{}")
        return str(payload["choices"][0]["message"]["content"])

    def edit_image(self, *, model: str, prompt: str, reference: str) -> dict:
        import urllib.request

        if model == MAIN_MODEL:
            token = os.getenv("REPLICATE_API_TOKEN", "").strip()
            body = json.dumps({"input": {"prompt": prompt, "image": reference}}).encode()
            request = urllib.request.Request(
                "https://api.replicate.com/v1/models/google/nano-banana-2/predictions",
                data=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode() or "{}")
            return {"image": str(payload.get("output") or payload.get("id") or ""), "model": model}
        key = os.getenv("XAI_API_KEY", "").strip()
        body = json.dumps({"model": "grok-imagine-image-2.0", "prompt": prompt, "image": reference}).encode()
        request = urllib.request.Request(
            "https://api.x.ai/v1/images/edits",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode() or "{}")
        return {"image": str(payload.get("url") or ""), "model": BACKUP_MODEL}

    def check_image(self, *, output: str, reference: str, sheet: dict) -> dict:
        import urllib.request

        token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        prompt = (
            "Compare the output with the packshot and the attribute sheet. "
            "Reply JSON {\"passed\": true} or {\"passed\": false, \"reasons\": [\"...\"]}. "
            f"Sheet: {json.dumps(sheet)}. Output: {output}. Reference: {reference}."
        )
        body = json.dumps({"input": {"prompt": prompt}}).encode()
        request = urllib.request.Request(
            "https://api.replicate.com/v1/models/google/gemini-2.5-flash/predictions",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode() or "{}")
        text = payload.get("output") or "{}"
        if isinstance(text, list):
            text = "".join(str(part) for part in text)
        try:
            parsed = json.loads(str(text))
        except json.JSONDecodeError:
            parsed = {"passed": False, "reasons": ["Product check could not read the vision result."]}
        return {"passed": bool(parsed.get("passed")), "reasons": list(parsed.get("reasons") or [])}


def _client_or_live(client):
    return client or LiveClient()


def state_for(product_id: str) -> dict:
    _records()
    return db.memory_listing.setdefault(
        product_id,
        {
            "status": "Not started",
            "sheet": {},
            "angles": [],
            "scenes": [],
            "change_rounds": 0,
            "message": "",
        },
    )


def lock_sheet(product_id: str, sheet: dict) -> dict:
    state = state_for(product_id)
    sheet = dict(sheet)
    sheet["confirmed"] = True
    state["sheet"] = sheet
    state["status"] = "Awaiting your approval" if sheet_ready(sheet) else "Not started"
    state["message"] = "" if sheet_ready(sheet) else "Confirm the attribute sheet before scenes are made."
    return state


def generate(product: dict, *, supplier_id: str, client=None, scenes: list[dict] | None = None) -> dict:
    product_id = str(product.get("id") or "")
    state = state_for(product_id)
    if not sheet_ready(state.get("sheet") or {}):
        state["message"] = "Confirm the attribute sheet before scenes are made."
        state["status"] = "Not started"
        return state
    notice = unavailable_message()
    if client is None and notice:
        state["message"] = notice
        state["status"] = "Generation unavailable"
        return state
    worker = _client_or_live(client)
    block = _within_caps(supplier_id, TEXT_CENTS)
    if block:
        state["message"] = block
        return state
    prompt = prompt_for(product, state["sheet"], {"type": "positioning", "setting": "", "mood": "", "season": "", "model": ""})
    raw = worker.complete_text(prompt + " Return JSON {\"angles\": [{\"name\", \"insight\", \"moment\", \"hook\", \"caption\"}]} with 3 or 4 angles.")
    _note_spend(supplier_id, TEXT_CENTS, "grok-4.3")
    state["angles"] = _angles_from_text(raw, product)
    planned = scenes or [
        {"type": "in a home", "setting": "kitchen shelf", "mood": "morning light", "model": "no people", "season": "early autumn"},
        {"type": "held", "setting": "weekday morning", "mood": "soft overcast", "model": "hands", "season": "early autumn"},
        {"type": "in use", "setting": "weeknight table", "mood": "weeknight warm", "model": "hands", "season": "mid-winter"},
        {"type": "worn on a model", "setting": "indoor", "mood": "morning light", "model": "worn on a model", "season": "late spring"},
    ]
    built = []
    for spec in planned[:4]:
        built.append(_make_scene(product, state["sheet"], spec, supplier_id, worker))
    state["scenes"] = built
    if any(row["status"] == "held" for row in built):
        state["status"] = "Held for review"
    else:
        state["status"] = "Awaiting your approval"
    state["message"] = ""
    return state


def _make_scene(product: dict, sheet: dict, spec: dict, supplier_id: str, worker) -> dict:
    category = str(product.get("category") or "general")
    order = [preferred_model(category)]
    if BACKUP_MODEL not in order:
        order.append(BACKUP_MODEL)
    scene = {
        "id": str(uuid.uuid4()),
        "type": spec.get("type"),
        "setting": spec.get("setting"),
        "mood": spec.get("mood"),
        "model_control": spec.get("model"),
        "season": spec.get("season"),
        "status": "held",
        "image": "",
        "provider": "",
        "tries": 0,
        "reasons": [],
        "review": "",
        "approved": False,
        "free_retry": False,
    }
    corrections: list[str] = []
    for provider in order:
        tries = MAX_MAIN_TRIES if provider == MAIN_MODEL else 1
        for _ in range(tries):
            cents = MAIN_CENTS if provider == MAIN_MODEL else BACKUP_CENTS
            block = _within_caps(supplier_id, cents + VISION_CENTS)
            if block:
                scene["reasons"] = [block]
                return scene
            prompt = prompt_for(product, sheet, spec, corrections=corrections)
            image = worker.edit_image(model=provider, prompt=prompt, reference=str(product.get("image_url") or product.get("title") or ""))
            _note_spend(supplier_id, cents, provider)
            scene["tries"] += 1
            scene["provider"] = provider
            scene["image"] = str(image.get("image") or "")
            check = worker.check_image(output=scene["image"], reference=str(product.get("image_url") or ""), sheet=sheet)
            _note_spend(supplier_id, VISION_CENTS, VISION_MODEL)
            if check.get("passed"):
                if corrections:
                    _log_failure(model=provider, category=category, reason="; ".join(corrections), passed_later=True)
                scene["status"] = "ready"
                scene["reasons"] = []
                scene["free_retry"] = bool(corrections)
                return scene
            reasons = [str(item) for item in check.get("reasons") or ["Product check failed."]]
            corrections = reasons
            scene["reasons"] = reasons
            _log_failure(model=provider, category=category, reason="; ".join(reasons), passed_later=False)
    scene["status"] = "held"
    db.memory_listing_holds.append({"product_id": product.get("id"), "scene_id": scene["id"], "reasons": scene["reasons"]})
    return scene


def review_scene(product_id: str, scene_id: str, *, decision: str, tag: str = "", note: str = "") -> dict:
    state = state_for(product_id)
    scene = next((row for row in state["scenes"] if row["id"] == scene_id), None)
    if scene is None:
        raise ValueError("Scene not found")
    if tag and tag not in REVIEW_TAGS:
        raise ValueError("Unknown review tag")
    if tag == "other" and not (note or "").strip():
        raise ValueError("Add a note for other")
    scene["review"] = decision
    scene["tag"] = tag
    scene["note"] = note
    if decision == "approve":
        scene["approved"] = True
        scene["status"] = "Approved"
    elif decision == "reject":
        scene["approved"] = False
        scene["status"] = "Rejected"
    else:
        scene["approved"] = False
        scene["status"] = "Changes requested"
        if tag == "product inaccurate":
            scene["free_retry"] = True
        elif state["change_rounds"] == 0:
            scene["free_retry"] = True
            state["change_rounds"] = 1
        else:
            scene["free_retry"] = False
    if any(row.get("approved") for row in state["scenes"]):
        state["status"] = "Approved"
    return state


def approved_for_creatr(product_id: str) -> dict:
    state = state_for(product_id)
    return {
        "angles": [row for row in state.get("angles") or [] if row.get("approved")],
        "scenes": [row for row in state.get("scenes") or [] if row.get("approved")],
    }


def holds() -> list[dict]:
    _records()
    return list(db.memory_listing_holds)


def release_hold(scene_id: str) -> None:
    db.memory_listing_holds = [row for row in db.memory_listing_holds if row.get("scene_id") != scene_id]
