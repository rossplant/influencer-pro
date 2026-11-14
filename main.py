# plantr-collab API — Lightweight (no local embedding model)
# Run: uvicorn main:app --host 0.0.0.0 --port $PORT
# Requires grok_helper.py in the same directory

import os
import re
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from notion_client import Client
import requests
from dotenv import load_dotenv
from grok_helper import generate_with_grok

load_dotenv()

app = FastAPI(title="plantr-collab API", version="0.8-light")

notion = None
if os.getenv("NOTION_TOKEN"):
    notion = Client(auth=os.getenv("NOTION_TOKEN"))

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN")

WEIGHTED_PATTERNS = {
    "beginner": [
        (r'\b(beginner|easy care|low maintenance)\b', 12),
        (r'\b(hard to kill|forgiving|neglect)\b', 8),
    ],
    "houseplants": [(r'\b(house ?plant|indoor plant)\b', 12)],
    "low light": [(r'\b(low[- ]?light|shade[- ]?tolerant|indirect)\b', 12)],
    "pet safe": [(r'\b(pet[- ]?safe|non[- ]?toxic|cat[- ]?friendly)\b', 14)],
    "trailing": [(r'\b(trailing|hanging|vining|cascade)\b', 10)],
    "gift": [(r'\b(gift|present|housewarming)\b', 10)],
}

NEGATIVE_PATTERNS = {
    "pet safe": r'\b(toxic|poisonous|not pet safe)\b',
    "low light": r'\b(full sun|bright direct|high light only)\b',
    "beginner": r'\b(expert only|difficult|high maintenance)\b',
}

def build_product_text(product: Dict[str, Any]) -> str:
    parts = [
        product.get("title", ""),
        product.get("category", ""),
        product.get("description_short", ""),
        product.get("description_long", ""),
        " ".join(product.get("key_features", [])),
        " ".join(product.get("target_audience", [])),
        " ".join(product.get("occasions", [])),
    ]
    return " ".join([p for p in parts if p]).lower()

def weighted_interest_score(product_text: str, interest: str) -> float:
    score = 0.0
    for pattern, weight in WEIGHTED_PATTERNS.get(interest.lower(), []):
        if re.search(pattern, product_text, re.IGNORECASE):
            score += weight
    return score

def has_negative(product_text: str, interest: str) -> bool:
    pattern = NEGATIVE_PATTERNS.get(interest.lower())
    return bool(pattern and re.search(pattern, product_text, re.IGNORECASE))

class ProductIn(BaseModel):
    title: str
    brand: Optional[str] = None
    category: Optional[str] = None
    description_short: Optional[str] = ""
    description_long: Optional[str] = ""
    key_features: Optional[List[str]] = []
    target_audience: Optional[List[str]] = []
    occasions: Optional[List[str]] = []
    care_instructions: Optional[str] = None
    claims: Optional[List[Dict[str, str]]] = []
    images: Optional[List[str]] = []
    price: Optional[float] = None
    signal_score: Optional[int] = 0

class InfluencerIn(BaseModel):
    handle: str
    name: Optional[str] = None
    areas_of_interest: List[str] = []
    preferred_promotion_styles: List[str] = []
    expertise_level: Optional[Dict[str, str]] = {}
    restrictions: Optional[List[str]] = []
    availability: Optional[str] = "open"

class EnrichResponse(BaseModel):
    signal_score: int
    creator_talking_points: List[str]
    common_questions: List[Dict[str, str]]
    enriched: Dict[str, Any]

def calculate_signal_score(product: Dict[str, Any]) -> int:
    score = 0
    if product.get("title") and product.get("description_long"):
        score += 20
    if len(product.get("key_features", [])) >= 3:
        score += 15
    if product.get("target_audience"):
        score += 10
    if product.get("care_instructions"):
        score += 10
    if len(product.get("images", [])) >= 2:
        score += 10
    return min(score, 100)

def hybrid_match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    score = 0.0
    product_text = build_product_text(product)

    for interest in influencer.get("areas_of_interest", []):
        score += weighted_interest_score(product_text, interest)
        if has_negative(product_text, interest):
            score -= 30

    score += product.get("signal_score", 0) * 0.15

    if influencer.get("availability") == "paused":
        score *= 0.3

    return max(round(score, 1), 0)

def save_product_to_notion(product: dict):
    if not notion or not os.getenv("NOTION_PRODUCTS_DB"):
        return
    try:
        notion.pages.create(
            parent={"database_id": os.getenv("NOTION_PRODUCTS_DB")},
            properties={
                "Title": {"title": [{"text": {"content": product.get("title", "Untitled")}}]},
                "Signal Score": {"number": product.get("signal_score", 0)}
            }
        )
    except Exception as e:
        print(f"Notion save failed: {e}")

def save_influencer_to_notion(influencer: dict):
    if not notion or not os.getenv("NOTION_INFLUENCERS_DB"):
        return
    try:
        notion.pages.create(
            parent={"database_id": os.getenv("NOTION_INFLUENCERS_DB")},
            properties={
                "Handle": {"title": [{"text": {"content": influencer.get("handle", "@unknown")}}]}
            }
        )
    except Exception as e:
        print(f"Notion influencer save failed: {e}")

def fetch_shopify_products(limit=50):
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        raise HTTPException(status_code=400, detail="Shopify credentials not configured")
    url = f"https://{SHOPIFY_STORE}/admin/api/2024-10/products.json?limit={limit}"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("products", [])

def map_shopify_to_plant(sp):
    return {
        "title": sp["title"],
        "brand": sp.get("vendor"),
        "category": sp.get("product_type"),
        "description_long": sp.get("body_html", ""),
        "price": float(sp["variants"][0]["price"]) if sp.get("variants") else None,
        "images": [img["src"] for img in sp.get("images", [])],
    }

@app.get("/")
def root():
    return {
        "status": "plantr-collab API running",
        "version": "0.8-light",
        "grok_enabled": bool(os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY"))
    }

@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn):
    data = product.dict()
    if not data.get("key_features"):
        data["key_features"] = ["Easy care", "Attractive foliage", "Suitable for indoors"]
    if not data.get("target_audience"):
        data["target_audience"] = ["beginners", "apartment dwellers"]

    grok_result = generate_with_grok(data)
    data["creator_talking_points"] = grok_result.get("talking_points", [])
    data["common_questions"] = grok_result.get("faqs", [])
    data["signal_score"] = calculate_signal_score(data)

    save_product_to_notion(data)
    return {
        "signal_score": data["signal_score"],
        "creator_talking_points": data["creator_talking_points"],
        "common_questions": data["common_questions"],
        "enriched": data
    }

@app.post("/match")
def match(influencer: InfluencerIn, products: List[ProductIn]):
    results = []
    for p in products:
        pdata = p.dict()
        score = hybrid_match_score(pdata, influencer.dict())
        results.append({"product": pdata, "match_score": score})
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return {"influencer": influencer.handle, "matches": results[:10]}

@app.post("/onboard-influencer")
def onboard(influencer: InfluencerIn):
    data = influencer.dict()
    save_influencer_to_notion(data)
    return {"status": "onboarded", "influencer": data}

@app.post("/sync-shopify")
def sync_shopify():
    products = fetch_shopify_products()
    enriched = []
    for sp in products:
        mapped = map_shopify_to_plant(sp)
        mapped["signal_score"] = calculate_signal_score(mapped)
        grok_result = generate_with_grok(mapped)
        mapped["creator_talking_points"] = grok_result.get("talking_points", [])
        save_product_to_notion(mapped)
        enriched.append(mapped)
    return {"synced": len(enriched), "products": enriched[:5]}
