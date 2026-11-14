# Shopify Product Sync Plan — Plant

## Goal
Pull products from Shopify → map to Plant product schema → enrich with AI signal layer → store in Notion / database for influencer matching.

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
3. Run through `/enrich` endpoint to add signal_score, talking points, common questions
4. Write enriched product to Notion database (or local JSON for now)

### 3. Code Skeleton (add to main_expanded.py)
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
- Or trigger manually via `/sync-shopify` endpoint

### 5. Next
Once Shopify connector is available, we can wire this directly.
