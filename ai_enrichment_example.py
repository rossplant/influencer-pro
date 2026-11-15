"""
Plantr enrichment example
Simple scoring + enrichment skeleton for TikTok Shopping Assistant readiness.
"""

from typing import Dict, List, Any

def calculate_signal_score(product: Dict[str, Any]) -> int:
    """Simple 0-100 AI readiness score."""
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
    """Placeholder — replace with real LLM call."""
    title = product.get("title", "this product")
    features = product.get("key_features", [])
    points = [
        f"Meet {title} — perfect for anyone who wants easier plant care.",
        f"Key benefit: {features[0] if features else 'low maintenance'}",
        "Great for small spaces and beginners.",
        "Here’s how I keep mine thriving...",
        "Would this fit your home?"
    ]
    return points


def enrich_product(raw_product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main enrichment function.
    In production this would call an LLM with the prompt from the overview.
    """
    enriched = raw_product.copy()
    # Placeholder enrichment
    if not enriched.get("key_features"):
        enriched["key_features"] = ["Easy care", "Attractive foliage", "Suitable for indoors"]
    if not enriched.get("target_audience"):
        enriched["target_audience"] = ["beginners", "apartment dwellers"]
    if not enriched.get("occasions"):
        enriched["occasions"] = ["home decor", "gift"]
    if not enriched.get("common_questions"):
        enriched["common_questions"] = [
            {"question": "Is this pet safe?", "answer": "Check the claims section for pet-safety info."},
            {"question": "How often should I water it?", "answer": enriched.get("care_instructions", "See care instructions.")}
        ]
    enriched["creator_talking_points"] = generate_talking_points(enriched)
    enriched["signal_score"] = calculate_signal_score(enriched)
    return enriched


# Example usage
if __name__ == "__main__":
    sample = {
        "title": "Snake Plant – Low Light Indoor",
        "description_short": "Hardy snake plant that thrives in low light.",
        "description_long": "The classic snake plant is nearly impossible to kill. Perfect for beginners.",
        "images": ["url1", "url2", "url3"]
    }
    result = enrich_product(sample)
    print("Signal Score:", result["signal_score"])
    print("Talking Points:", result["creator_talking_points"])
