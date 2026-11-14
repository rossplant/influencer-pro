# plantr-collab API — Hybrid Regex + Embedding Matching (v0.7)
# Run: uvicorn main_expanded:app --reload
# Requires .env with NOTION_TOKEN, NOTION_PRODUCTS_DB, NOTION_INFLUENCERS_DB,
# SHOPIFY_STORE, SHOPIFY_TOKEN, GROK_API_KEY (or XAI_API_KEY)
#
# Dependencies:
#   pip install fastapi uvicorn notion-client python-dotenv requests sentence-transformers numpy
#
# Future improvement options:
# - External pattern config (JSON/YAML)
# - Vector DB for product embeddings (pgvector / Qdrant)
# - Learn weights from conversion data
# - Category-specific pattern sets
# - A/B test scoring variants
# - Async embedding generation for large catalogs

import os
import re
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from notion_client import Client
import requests
from dotenv import load_dotenv
import numpy as np
from sentence_transformers import SentenceTransformer

load_dotenv()

app = FastAPI(title="plantr-collab API", version="0.7")

# Optional Grok / xAI integration
GROK_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or "grok-2-latest"

# Embedding model (loaded once)
EMBED_MODEL = None
try:
    EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    print("Embedding model loaded")
except Exception as e:
    print(f"Embedding model failed to load (semantic boost disabled): {e}")

notion = None
if os.getenv("NOTION_TOKEN"):
    notion = Client(auth=os.getenv("NOTION_TOKEN"))

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN")

# Weighted interest patterns
WEIGHTED_PATTERNS = {
    "beginner": [
        (r'\b(beginner|easy care|low maintenance)\b', 12),
        (r'\b(hard to kill|forgiving|neglect)\b', 8),
        (r'\b(watering once|every few weeks)\b', 5),
    ],
    "houseplants": [
        (r'\b(house ?plant|indoor plant)\b', 12),
        (r'\b(potted|container|apartment)\b', 6),
    ],
    "low light": [
        (r'\b(low[- ]?light|shade[- ]?tolerant|indirect)\b', 12),
        (r'\b(north window|low light room)\b', 6),
    ],
    "pet safe": [
        (r'\b(pet[- ]?safe|non[- ]?toxic|cat[- ]?friendly|dog[- ]?safe)\b', 14),
    ],
    "air purifying": [
        (r'\b(air[- ]?purif(?:ying|ier)|nasa[- ]?clean)\b', 10),
    ],
    "trailing": [
        (r'\b(trailing|hanging|vining|cascade)\b', 10),
    ],
    "gift": [
        (r'\b(gift|present|housewarming)\b', 10),
    ],
}

NEGATIVE_PATTERNS = {
    "pet safe": r'\b(toxic|poisonous|not pet safe|keep away from pets)\b',
    "low light": r'\b(full sun|bright direct|high light only)\b',
    "beginner": r'\b(expert only|difficult|high maintenance|fussy)\b',
}

CONTEXT_PATTERNS = {
    "educational_fit": r'\b(how to|care guide|watering|light requirements|propagate)\b',
    "lifestyle_fit": r'\b(shelf|desk|living room|bedroom|aesthetic|decor)\b',
    "gift_fit": r'\b(gift|present|housewarming|new home|thank you)\b',
    "unboxing_fit": r'\b(packaging|arrives|unbox|fresh|healthy plant)\b',
}


def call_grok(prompt: str, system_prompt: str = "You are a helpful product marketing assistant.") -> str:
    """Call the xAI Grok API if a key is present."""
    if not GROK_API_KEY:
        raise RuntimeError("GROK_API_KEY or XAI_API_KEY is not configured.")

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
    }

    response = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not text:
        raise ValueError("Empty response from Grok API")
    return text.strip()


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
    claims = product.get("claims", [])
    if claims:
        parts.append(" ".join([c.get("claim", "") for c in claims if isinstance(c, dict)]))
    return " ".join(parts).lower()


def weighted_interest_score(product_text: str, interest: str) -> float:
    score = 0.0
    for pattern, weight in WEIGHTED_PATTERNS.get(interest.lower(), []):
        if re.search(pattern, product_text, re.IGNORECASE):
            score += weight
    return score


def has_negative(product_text: str, interest: str) -> bool:
    pattern = NEGATIVE_PATTERNS.get(interest.lower())
    if not pattern:
        return False
    return bool(re.search(pattern, product_text, re.IGNORECASE))


def word_match(text: str, term: str) -> bool:
    if not text or not term:
        return False
    pattern = r'\b' + re.escape(term) + r'\b'
    return bool(re.search(pattern, text, re.IGNORECASE))


def embed(text: str):
    if not EMBED_MODEL or not text:
        return np.zeros(384)
    return EMBED_MODEL.encode(text, normalize_embeddings=True)


def semantic_similarity(text_a: str, text_b: str) -> float:
    if not text_a or not text_b or EMBED_MODEL is None:
        return 0.0
    emb_a = embed(text_a)
    emb_b = embed(text_b)
    return float(np.dot(emb_a, emb_b))


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
    if product.get("title") and product.get("description_short") and product.get("description_long"):
        score += 20
    if len(product.get("key_features", [])) >= 5:
        score += 15
    if product.get("target_audience") and product.get("occasions"):
        score += 15
    if product.get("claims"):
        score += 10
    if len(product.get("common_questions", [])) >= 5:
        score += 20
    if product.get("care_instructions"):
        score += 10
    if len(product.get("images", [])) >= 3:
        score += 10
    return min(score, 100)


def generate_talking_points(product: Dict[str, Any]) -> List[str]:
    title = product.get("title", "this product")
    features = product.get("key_features", [])

    if GROK_API_KEY:
        prompt = (
            f"Create exactly 5 short marketing talking points for a plant product called '{title}'. "
            f"Key features: {', '.join(features) if features else 'easy care, indoor friendly'} . "
            "Keep each point to one sentence and make them suitable for an influencer campaign."
        )
        try:
            response = call_grok(prompt, system_prompt="You are a plant marketing copywriter.")
            lines = [
                line.strip().lstrip("-*• ").strip()
                for line in response.splitlines()
                if line.strip()
            ]
            cleaned = [line for line in lines if len(line) > 8][:5]
            if cleaned:
                return cleaned
        except Exception as exc:
            print(f"Grok talking points fallback: {exc}")

    return [
        f"Meet {title} — perfect for anyone who wants easier plant care.",
        f"Key benefit: {features[0] if features else 'low maintenance'}",
        "Great for small spaces and beginners.",
        "Here’s how I keep mine thriving...",
        "Would this fit your home?",
    ]


def generate_common_questions(product: Dict[str, Any]) -> List[Dict[str, str]]:
    if GROK_API_KEY:
        prompt = (
            f"For this product, create 3 concise FAQ pairs in JSON list format with keys 'question' and 'answer'. "
            f"Product title: {product.get('title', '')}. "
            f"Care: {product.get('care_instructions', '')}. "
            f"Features: {', '.join(product.get('key_features', []))}."
        )
        try:
            response = call_grok(prompt, system_prompt="Return valid JSON only.")
            text = response.strip()
            if text.startswith("```"):
                text = text.strip("` ")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            questions = __import__("json").loads(text)
            if isinstance(questions, list) and questions:
                return questions[:5]
        except Exception as exc:
            print(f"Grok FAQ fallback: {exc}")

    return [
        {"question": "Is this pet safe?", "answer": "Check the claims section."},
        {"question": "How often should I water it?", "answer": product.get("care_instructions") or "See care instructions."},
    ]


def regex_match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    score = 0.0
    product_text = build_product_text(product)

    for interest in influencer.get("areas_of_interest", []):
        score += weighted_interest_score(product_text, interest)
        if has_negative(product_text, interest):
            score -= 30

    styles = [s.lower() for s in influencer.get("preferred_promotion_styles", [])]
    if "educational" in styles and re.search(CONTEXT_PATTERNS["educational_fit"], product_text, re.I):
        score += 15
    if "lifestyle" in styles and re.search(CONTEXT_PATTERNS["lifestyle_fit"], product_text, re.I):
        score += 12
    if "unboxing" in styles and len(product.get("images", [])) >= 3:
        score += 8
    if "gift guide" in styles and re.search(CONTEXT_PATTERNS["gift_fit"], product_text, re.I):
        score += 10

    expertise = influencer.get("expertise_level", {})
    for interest in influencer.get("areas_of_interest", []):
        if weighted_interest_score(product_text, interest) > 0:
            level = expertise.get(interest, expertise.get(interest.title(), "")).lower()
            if level == "expert":
                score += 6
            elif level == "intermediate":
                score += 3

    score += product.get("signal_score", 0) * 0.15

    for restriction in influencer.get("restrictions", []):
        if word_match(product_text, restriction) or has_negative(product_text, restriction):
            score -= 40

    if influencer.get("availability") == "paused":
        score *= 0.3

    return max(round(score, 1), 0)


def hybrid_match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    regex_score = regex_match_score(product, influencer)

    product_text = build_product_text(product)
    interest_text = " ".join(influencer.get("areas_of_interest", []))
    style_text = " ".join(influencer.get("preferred_promotion_styles", []))

    interest_sim = semantic_similarity(product_text, interest_text)
    style_sim = semantic_similarity(product_text, style_text)

    semantic_boost = (interest_sim * 25) + (style_sim * 10)
    final = regex_score + semantic_boost
    return max(round(final, 1), 0)


def save_product_to_notion(product: dict):
    if not notion or not os.getenv("NOTION_PRODUCTS_DB"):
        return
    try:
        notion.pages.create(
            parent={"database_id": os.getenv("NOTION_PRODUCTS_DB")},
            properties={
                "Title": {"title": [{"text": {"content": product.get("title", "Untitled")}}]},
                "Brand": {"rich_text": [{"text": {"content": product.get("brand") or ""}}]},
                "Category": {"select": {"name": product.get("category") or "Uncategorized"}},
                "Price": {"number": product.get("price")},
                "Signal Score": {"number": product.get("signal_score", 0)}
            }
        )
    except Exception as e:
        print(f"Notion product save failed: {e}")


def save_influencer_to_notion(influencer: dict):
    if not notion or not os.getenv("NOTION_INFLUENCERS_DB"):
        return
    try:
        notion.pages.create(
            parent={"database_id": os.getenv("NOTION_INFLUENCERS_DB")},
            properties={
                "Handle": {"title": [{"text": {"content": influencer.get("handle", "@unknown")}}]},
                "Areas of Interest": {"multi_select": [{"name": a} for a in influencer.get("areas_of_interest", [])]},
                "Preferred Promotion Styles": {"multi_select": [{"name": s} for s in influencer.get("preferred_promotion_styles", [])]}
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
        "inventory_status": "in_stock"
    }


@app.get("/")
def root():
    return {"status": "plantr-collab API running", "version": "0.7", "grok_enabled": bool(GROK_API_KEY)}


@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn):
    data = product.dict()
    if not data.get("key_features"):
        data["key_features"] = ["Easy care", "Attractive foliage", "Suitable for indoors"]
    if not data.get("target_audience"):
        data["target_audience"] = ["beginners", "apartment dwellers"]
    if not data.get("occasions"):
        data["occasions"] = ["home decor", "gift"]
    data["common_questions"] = generate_common_questions(data)
    data["creator_talking_points"] = generate_talking_points(data)
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
    return {"status": "onboarded", "influencer": data, "message": "Profile saved for matching"}


@app.post("/sync-shopify")
def sync_shopify():
    products = fetch_shopify_products()
    enriched = []
    for sp in products:
        mapped = map_shopify_to_plant(sp)
        mapped["signal_score"] = calculate_signal_score(mapped)
        mapped["common_questions"] = generate_common_questions(mapped)
        mapped["creator_talking_points"] = generate_talking_points(mapped)
        save_product_to_notion(mapped)
        enriched.append(mapped)
    return {"synced": len(enriched), "products": enriched[:5]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
