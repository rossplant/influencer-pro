import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

GROK_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL") or "grok-4.3"
GROK_URL = "https://api.x.ai/v1/chat/completions"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _usage_tokens(usage: Any) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(usage, dict):
        return None, None
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def _obs(*, fallback: bool, latency_ms: int, prompt_tokens, completion_tokens) -> Dict[str, Any]:
    return {
        "model": GROK_MODEL,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "fallback": fallback,
        "latency_ms": latency_ms,
    }


def _log_grok(obs: Dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "grok",
                "model": obs.get("model"),
                "prompt_tokens": obs.get("prompt_tokens"),
                "completion_tokens": obs.get("completion_tokens"),
                "fallback": bool(obs.get("fallback")),
                "latency_ms": obs.get("latency_ms"),
            }
        ),
        flush=True,
    )


def generate_with_grok(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title", "product")
    desc = (product.get("description_long") or product.get("description_short") or "")[:500]

    fallback = {
        "talking_points": [
            f"Meet {title}",
            "Easy care and beginner friendly",
            "Great for apartments and small spaces",
        ],
        "faqs": [
            {"question": "Is it pet safe?", "answer": "Check the product details."},
            {"question": "How often to water?", "answer": "See care instructions."},
        ],
        "source": "fallback",
    }

    if not GROK_API_KEY:
        fallback["_obs"] = _obs(fallback=True, latency_ms=0, prompt_tokens=None, completion_tokens=None)
        _log_grok(fallback["_obs"])
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
    started = time.perf_counter()
    try:
        resp = requests.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 400,
            },
            timeout=20,
        )
        latency_ms = _elapsed_ms(started)
        resp.raise_for_status()
        payload = resp.json()
        usage = payload.get("usage")
        print(f"Grok usage: {json.dumps(usage)}")
        content = payload["choices"][0]["message"]["content"]
        if "```" in content:
            content = content.split("```")[1].replace("json", "").strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Grok response was not a JSON object")
        data["source"] = "grok"
        prompt_tokens, completion_tokens = _usage_tokens(usage)
        data["_obs"] = _obs(
            fallback=False,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        _log_grok(data["_obs"])
        return data
    except Exception as e:
        latency_ms = _elapsed_ms(started)
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
        fallback["_obs"] = _obs(fallback=True, latency_ms=latency_ms, prompt_tokens=None, completion_tokens=None)
        _log_grok(fallback["_obs"])
        return fallback
