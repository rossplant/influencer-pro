from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any

app = FastAPI(title="Plant Influencer API")

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
    enriched = product.dict()
    enriched["key_features"] = ["Easy care", "Attractive", "Indoor"]
    enriched["target_audience"] = ["beginners"]
    enriched["occasions"] = ["home", "gift"]
    score = 65
    points = [f"Meet {product.title}", "Perfect for beginners"]
    return {
        "signal_score": score,
        "creator_talking_points": points,
        "enriched": enriched
    }

@app.post("/match")
def match(influencer: Dict[str, Any], products: List[Dict[str, Any]]):
    return {"matches": products[:3]}
