# Shopify token wiring — Plantr Collab

## 1. Create Shopify Custom App
1. Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Create app → Configure Admin API scopes:
   - read_products
   - read_inventory
   - read_product_listings
3. Install app → reveal Admin API access token (starts with `shpat_`)

## 2. Environment
```
SHOPIFY_STORE=your-store.myshopify.com
SHOPIFY_TOKEN=shpat_xxxxx
```

## 3. Current route, and a sketch that is not running

`main.py` already exposes `POST /sync-shopify`. Missing `SHOPIFY_STORE` or `SHOPIFY_TOKEN` returns 501 with the message that Shopify sync isn't configured. With both set, it still returns 501 and says sync is not implemented. No products are fetched or saved.

This sketch is not the handler in `main.py`. Do not expect `{"synced": ...}` from the live route.
```python
import os
import requests

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN")

def fetch_shopify_products(limit=50):
    url = f"https://{SHOPIFY_STORE}/admin/api/2024-10/products.json?limit={limit}"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("products", [])

def map_shopify_product(sp):
    return {
        "title": sp["title"],
        "brand": sp.get("vendor"),
        "category": sp.get("product_type"),
        "description_long": sp.get("body_html", ""),
        "price": float(sp["variants"][0]["price"]) if sp.get("variants") else None,
        "images": [img["src"] for img in sp.get("images", [])],
        "inventory_status": "in_stock"
    }

@app.post("/sync-shopify")
def sync_shopify():
    products = fetch_shopify_products()
    enriched = []
    for sp in products:
        mapped = map_shopify_product(sp)
        # call enrich logic here
        enriched.append(mapped)
    return {"synced": len(enriched), "products": enriched[:5]}
```

## 4. Test the stub
```bash
curl -X POST "$BASE/sync-shopify"
```
`$BASE` is the API origin. A 501 body is the correct result until sync is implemented.
