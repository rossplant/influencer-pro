# Improved Matching Algorithm — Plantr

Proposed replacement for `score_match` in `main.py`. Phase 0 does not use this scorer. `main.py` is the entry point. Older drafts are in `archive/`, and there is no `main_expanded.py`.

```python
def match_score(product: Dict[str, Any], influencer: Dict[str, Any]) -> float:
    score = 0.0

    # Interest overlap (weighted)
    product_tags = set()
    product_tags.update([t.lower() for t in product.get("target_audience", [])])
    product_tags.update([t.lower() for t in product.get("occasions", [])])
    product_tags.add(product.get("category", "").lower())
    product_tags.update([c.get("claim", "").lower() for c in product.get("claims", []) if isinstance(c, dict)])

    interests = set(i.lower() for i in influencer.get("areas_of_interest", []))
    overlap = product_tags & interests
    score += len(overlap) * 12

    # Expertise bonus
    expertise = influencer.get("expertise_level", {})
    for area in overlap:
        level = expertise.get(area, expertise.get(area.title(), "")).lower()
        if level == "expert":
            score += 8
        elif level == "intermediate":
            score += 4

    # Promotion style fit
    styles = set(s.lower() for s in influencer.get("preferred_promotion_styles", []))
    if "educational" in styles and product.get("care_instructions"):
        score += 18
    if "lifestyle" in styles and product.get("occasions"):
        score += 12
    if "unboxing" in styles and len(product.get("images", [])) >= 3:
        score += 8
    if "gift guide" in styles and "gift" in " ".join(product.get("occasions", [])).lower():
        score += 10

    # Signal score bonus (better products rank higher)
    signal = product.get("signal_score", 0)
    score += signal * 0.15

    # Restriction penalty
    restrictions = [r.lower() for r in influencer.get("restrictions", [])]
    claims_text = " ".join([c.get("claim", "") for c in product.get("claims", []) if isinstance(c, dict)]).lower()
    for r in restrictions:
        if r in claims_text or r in product.get("title", "").lower():
            score -= 40

    # Availability soft filter
    if influencer.get("availability") == "paused":
        score *= 0.3

    return max(round(score, 1), 0)
```

This version:
- Weights expertise
- Rewards style fit more precisely
- Boosts high-signal products
- Applies restriction penalties
- Soft-filters paused influencers
