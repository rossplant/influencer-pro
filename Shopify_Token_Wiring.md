# Shopify Token Wiring — PlantR

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

## 3. Add to main_expanded.py
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

def map_shopify_to_plant(sp):
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
        mapped = map_shopify_to_plant(sp)
        # call enrich logic here
        enriched.append(mapped)
    return {"synced": len(enriched), "products": enriched[:5]}
```

## 4. Test
```bash
curl -X POST http://localhost:8000/sync-shopify
```
