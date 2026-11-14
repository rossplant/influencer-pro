# Expanded FastAPI for Plant Influencer Platform
# Run with: uvicorn main:app --reload

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json

app = FastAPI(title="Plant Influencer API", version="0.2")

# In production these come from Notion or a database
SAMPLE_PRODUCTS = []
SAMPLE_INFLUENCERS = []

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
    images: Optional[List[str]] = []
    price: Optional[float] = None

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
    return [
        f"Meet {title} — perfect for anyone who wants easier plant care.",
        f"Key benefit: {features[0] if features else 'low maintenance'}",
        "Great for small spaces and beginners.",
        "Here’s how I keep mine thriving...",
        "Would this fit your home?"
    ]

def match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    score = 0.0
    product_tags = set()
    product_tags.update([t.lower() for t in product.get("target_audience", [])])
    product_tags.update([t.lower() for t in product.get("occasions", [])])
    product_tags.add(product.get("category", "").lower())
    interests = set(i.lower() for i in influencer.get("areas_of_interest", []))
    overlap = len(product_tags & interests)
    score += overlap * 10
    styles = set(influencer.get("preferred_promotion_styles", []))
    if "educational" in styles and product.get("care_instructions"):
        score += 15
    if "lifestyle" in styles and product.get("occasions"):
        score += 10
    return max(score, 0)

@app.get("/")
def root():
    return {"status": "Plant Influencer API running", "version": "0.2"}

@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn):
    data = product.dict()
    # Placeholder enrichment — replace with LLM call
    if not data.get("key_features"):
        data["key_features"] = ["Easy care", "Attractive foliage", "Suitable for indoors"]
    if not data.get("target_audience"):
        data["target_audience"] = ["beginners", "apartment dwellers"]
    if not data.get("occasions"):
        data["occasions"] = ["home decor", "gift"]
    data["common_questions"] = [
        {"question": "Is this pet safe?", "answer": "Check the claims section."},
        {"question": "How often should I water it?", "answer": data.get("care_instructions") or "See care instructions."}
    ]
    data["creator_talking_points"] = generate_talking_points(data)
    data["signal_score"] = calculate_signal_score(data)
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
        score = match_score(pdata, influencer.dict())
        results.append({"product": pdata, "match_score": score})
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return {"influencer": influencer.handle, "matches": results[:10]}

@app.post("/onboard-influencer")
def onboard(influencer: InfluencerIn):
    # In production: save to Notion / database
    return {"status": "onboarded", "influencer": influencer.dict(), "message": "Profile saved for matching"}
