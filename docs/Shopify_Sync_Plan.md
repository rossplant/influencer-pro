# Shopify Product Sync Plan — Plant

**Not shipped.** `POST /sync-shopify` in `main.py` returns 501. It does not call Shopify, does not enrich products, and does not write a catalog. Postgres is the planned system of record. Notion is not the database.

## Goal
Pull products from Shopify → map to the Plant product schema → enrich with the AI signal layer → store in Postgres for influencer matching.

## Steps

### 1. Shopify Setup
- Create a custom app in Shopify Admin → Settings → Apps and sales channels → Develop apps
- Grant scopes: `read_products`, `read_inventory`, `read_product_listings`
- Get Admin API access token

### 2. Sync Flow
1. Call Shopify Admin API: `GET /admin/api/2024-10/products.json`
2. Map fields to Plant schema:
   - title → title
   - body_html → description_long
   - vendor → brand
   - product_type → category
   - variants[0].price → price
   - images → images
3. `POST /enrich` already exists and can add talking points. The sync stub does not call it.
4. Write the enriched product to Postgres. Do not write it to Notion.

### 3. Code skeleton (not in `main.py`)
```python
import requests

SHOPIFY_STORE = "your-store.myshopify.com"
SHOPIFY_TOKEN = "shpat_xxxxx"

def fetch_shopify_products():
    url = f"https://{SHOPIFY_STORE}/admin/api/2024-10/products.json?limit=50"
    headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
    resp = requests.get(url, headers=headers)
    return resp.json().get("products", [])

def map_shopify_to_plant(sp):
    return {
        "title": sp["title"],
        "brand": sp.get("vendor"),
        "category": sp.get("product_type"),
        "description_long": sp.get("body_html", ""),
        "price": float(sp["variants"][0]["price"]) if sp.get("variants") else None,
        "images": [img["src"] for img in sp.get("images", [])]
    }
```

### 4. Schedule
- Use a simple cron or GitHub Action to sync every 6–12 hours
- Or trigger manually via `POST /sync-shopify` once that route actually syncs. Today the route only reports that sync is not configured or not implemented.

### 5. Next
Wire the skeleton into `main.py` when the connector is ready. Until then the endpoint must keep returning 501 instead of a fake sync count.
