# FastAPI Skeleton for Plant Influencer Platform

from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import json

app = FastAPI(title="Plant Influencer API")

# Load schemas (in production pull from Notion or DB)
with open("product_schema.json") as f:
    PRODUCT_SCHEMA = json.load(f)

class ProductIn(BaseModel):
    title: str
    description_short: str = ""
    description_long: str = ""
    category: str = ""
    images: List[str] = []

class EnrichResponse(BaseModel):
    signal_score: int
    creator_talking_points: List[str]
    enriched: Dict[str, Any]

@app.get("/")
def root():
    return {"status": "Plant Influencer API running"}

@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn):
    # Placeholder — replace with real LLM call
    enriched = product.dict()
    enriched["key_features"] = ["Easy care", "Attractive", "Indoor"]
    enriched["target_audience"] = ["beginners"]
    enriched["occasions"] = ["home", "gift"]
    enriched["common_questions"] = [
        {"question": "Is it pet safe?", "answer": "Check claims."}
    ]
    score = 65  # call calculate_signal_score here
    points = [f"Meet {product.title}", "Perfect for beginners"]
    return {
        "signal_score": score,
        "creator_talking_points": points,
        "enriched": enriched
    }

@app.post("/match")
def match(influencer: Dict[str, Any], products: List[Dict[str, Any]]):
    # Call matching_example logic
    return {"matches": products[:3]}  # placeholder
