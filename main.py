from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
from grok_helper import generate_with_grok

app = FastAPI(title="plantr-collab API", version="0.8-grok")

class ProductIn(BaseModel):
    title: str
    description_short: str = ""
    description_long: str = ""
    category: str = ""
    images: List[str] = []

class EnrichResponse(BaseModel):
    signal_score: int
    creator_talking_points: List[str]
    common_questions: List[Dict[str, str]] = []
    enriched: Dict[str, Any]

@app.get("/")
def root():
    import os
    return {
        "status": "plantr-collab API running",
        "version": "0.8-grok",
        "grok_enabled": bool(os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY"))
    }

@app.post("/enrich", response_model=EnrichResponse)
def enrich(product: ProductIn):
    data = product.dict()
    grok = generate_with_grok(data)
    return {
        "signal_score": 70,
        "creator_talking_points": grok.get("talking_points", []),
        "common_questions": grok.get("faqs", []),
        "enriched": data
    }

@app.post("/match")
def match(influencer: Dict[str, Any], products: List[Dict[str, Any]]):
    return {"matches": products[:3]}
