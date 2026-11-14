# grok_helper.py — Optional Grok talking-points and FAQ generation
# Falls back to local logic if GROK_API_KEY is missing or the call fails.
#
# Env vars:
#   GROK_API_KEY  (or XAI_API_KEY)
#   GROK_MODEL    (default: grok-2-latest)

import os
import requests
from typing import List, Dict, Any

GROK_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or "grok-2-latest"
GROK_URL = "https://api.x.ai/v1/chat/completions"

def _local_talking_points(product: Dict[str, Any]) -> List[str]:
    title = product.get("title", "this product")
    features = product.get("key_features", [])
    return [
        f"Meet {title} — perfect for anyone who wants easier plant care.",
        f"Key benefit: {features[0] if features else 'low maintenance'}",
        "Great for small spaces and beginners.",
        "Here’s how I keep mine thriving...",
        "Would this fit your home?"
    ]

def _local_faqs(product: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"question": "Is this pet safe?", "answer": "Check the claims section."},
        {"question": "How often should I water it?", "answer": product.get("care_instructions") or "See care instructions."}
    ]

def generate_with_grok(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
      {
        "talking_points": [...],
        "faqs": [{"question": "...", "answer": "..."}, ...]
      }
    Uses Grok when possible, otherwise local fallback.
    """
    if not GROK_API_KEY:
        return {
            "talking_points": _local_talking_points(product),
            "faqs": _local_faqs(product)
        }

    title = product.get("title", "product")
    desc = product.get("description_long") or product.get("description_short") or ""
    features = ", ".join(product.get("key_features", []))
    audience = ", ".join(product.get("target_audience", []))
    care = product.get("care_instructions") or "standard care"

    prompt = f"""You are helping an influencer create short-form content for a plant product.

Product: {title}
Description: {desc[:600]}
Key features: {features}
Target audience: {audience}
Care: {care}

Return ONLY valid JSON in this exact shape:
{{
  "talking_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "faqs": [
    {{"question": "short question", "answer": "short answer"}},
    {{"question": "short question", "answer": "short answer"}}
  ]
}}

Keep talking points under 120 characters each. Keep FAQ answers under 160 characters. Focus on practical, creator-friendly language."""

    try:
        resp = requests.post(
            GROK_URL,
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 600
            },
            timeout=30
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        # Extract JSON if the model wrapped it in markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        import json
        data = json.loads(content.strip())
        if not isinstance(data.get("talking_points"), list) or not isinstance(data.get("faqs"), list):
            raise ValueError("Invalid shape")
        return data
    except Exception as e:
        print(f"Grok call failed, using local fallback: {e}")
        return {
            "talking_points": _local_talking_points(product),
            "faqs": _local_faqs(product)
        }
