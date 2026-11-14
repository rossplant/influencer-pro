"""
Quick test script for Plant API endpoints.
Run the API first: uvicorn main_expanded:app --reload
Then: python test_api.py
"""

import requests
import json

BASE = "http://localhost:8000"

def test_enrich():
    payload = {
        "title": "Snake Plant – Low Light Indoor",
        "description_short": "Hardy snake plant",
        "description_long": "Nearly impossible to kill.",
        "category": "Houseplants",
        "images": ["url1", "url2", "url3"]
    }
    r = requests.post(f"{BASE}/enrich", json=payload)
    print("ENRICH:", r.status_code)
    print(json.dumps(r.json(), indent=2))

def test_onboard_and_match():
    influencer = {
        "handle": "@plantlover",
        "areas_of_interest": ["houseplants", "low light plants"],
        "preferred_promotion_styles": ["educational", "lifestyle"],
        "availability": "open"
    }
    r = requests.post(f"{BASE}/onboard-influencer", json=influencer)
    print("\nONBOARD:", r.status_code, r.json())

    products = [
        {"title": "Snake Plant", "category": "Houseplants", "target_audience": ["beginners"], "care_instructions": "Water monthly"},
        {"title": "Orchid", "category": "Houseplants", "target_audience": ["experts"]}
    ]
    r = requests.post(f"{BASE}/match", json={"influencer": influencer, "products": products})
    print("\nMATCH:", r.status_code)
    print(json.dumps(r.json(), indent=2))

if __name__ == "__main__":
    test_enrich()
    test_onboard_and_match()
