"""
Simple influencer ↔ product matching example for Plant.
"""

from typing import Dict, List, Any

def match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    score = 0.0
    product_tags = set()
    product_tags.update(product.get("target_audience", []))
    product_tags.update(product.get("occasions", []))
    product_tags.add(product.get("category", "").lower())
    product_tags.update([c.get("claim", "").lower() for c in product.get("claims", [])])

    interests = set(i.lower() for i in influencer.get("areas_of_interest", []))
    overlap = len(product_tags & interests)
    score += overlap * 10

    # Style match bonus
    styles = set(influencer.get("preferred_promotion_styles", []))
    if "educational" in styles and product.get("care_instructions"):
        score += 15
    if "lifestyle" in styles and product.get("occasions"):
        score += 10

    # Restriction filter
    restrictions = [r.lower() for r in influencer.get("restrictions", [])]
    for claim in product.get("claims", []):
        if any(r in claim.get("claim", "").lower() for r in restrictions):
            score -= 50  # heavy penalty

    return max(score, 0)


def rank_products_for_influencer(products: List[Dict], influencer: Dict) -> List[Dict]:
    ranked = []
    for p in products:
        s = match_score(p, influencer)
        ranked.append({**p, "match_score": s})
    return sorted(ranked, key=lambda x: x["match_score"], reverse=True)


# Example
if __name__ == "__main__":
    influencer = {
        "areas_of_interest": ["houseplants", "low light plants", "beginner friendly"],
        "preferred_promotion_styles": ["educational", "lifestyle"],
        "restrictions": []
    }
    products = [
        {"title": "Snake Plant", "category": "plants", "target_audience": ["beginners"], "care_instructions": "Water monthly", "occasions": ["home"]},
        {"title": "High Maintenance Orchid", "category": "plants", "target_audience": ["experts"], "occasions": ["gift"]}
    ]
    results = rank_products_for_influencer(products, influencer)
    for r in results:
        print(r["title"], "→", r["match_score"])
