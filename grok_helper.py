import os
import requests
import json
from typing import List, Dict, Any

GROK_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL") or "grok-4.3"
GROK_URL = "https://api.x.ai/v1/chat/completions"

def generate_with_grok(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title", "product")
    desc = (product.get("description_long") or product.get("description_short") or "")[:500]

    fallback = {
        "talking_points": [
            f"Meet {title}",
            "Easy care and beginner friendly",
            "Great for apartments and small spaces"
        ],
        "faqs": [
            {"question": "Is it pet safe?", "answer": "Check the product details."},
            {"question": "How often to water?", "answer": "See care instructions."}
        ]
    }

    if not GROK_API_KEY:
        return fallback

    prompt = f"""Create short influencer talking points and FAQs for this plant product.
Product: {title}
Description: {desc}

Return ONLY valid JSON:
{{
  "talking_points": ["point 1", "point 2", "point 3"],
  "faqs": [
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}}
  ]
}}"""

    resp = None
    try:
        resp = requests.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 400
            },
            timeout=20
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        if "```" in content:
            content = content.split("```")[1].replace("json", "").strip()
        data = json.loads(content)
        return data
    except Exception as e:
        body = ""
        if resp is not None:
            try:
                body = resp.text[:500]
            except Exception:
                body = ""
        if body:
            print(f"Grok failed: {e} | {body}")
        else:
            print(f"Grok failed: {e}")
        return fallback
