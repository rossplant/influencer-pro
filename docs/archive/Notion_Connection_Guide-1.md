# Notion Connection Guide — PlantR

Duplicate upload. Not the current API. `main.py` does not read or write Notion. Notion is not the database. Postgres is the planned system of record. See `docs/Influencer_Onboarding_Flow.md`.

## 1. Create Notion Integration
1. Go to https://www.notion.so/my-integrations
2. Click **New integration**
3. Name: PlantR
4. Capabilities: Read content, Update content, Insert content
5. Copy the **Internal Integration Token** (starts with `secret_`)

## 2. Share Databases
1. Open your **Plant Products** database
2. Click ••• → Connections → Add connection → select PlantR
3. Repeat for **Plant Influencers** database
4. Copy each database ID from the URL (the long string after the last `/` and before `?`)

## 3. Environment Variables
Add to `.env` or Railway/Render secrets:
```
NOTION_TOKEN=secret_xxxxx
NOTION_PRODUCTS_DB=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_INFLUENCERS_DB=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## 4. Old sketch (not in `main.py`, and `main_expanded.py` is not the entry point)
```python
from notion_client import Client
import os

notion = Client(auth=os.getenv("NOTION_TOKEN"))

def save_product_to_notion(product: dict):
    notion.pages.create(
        parent={"database_id": os.getenv("NOTION_PRODUCTS_DB")},
        properties={
            "Title": {"title": [{"text": {"content": product["title"]}}]},
            "Brand": {"rich_text": [{"text": {"content": product.get("brand", "")}}]},
            "Category": {"select": {"name": product.get("category", "Uncategorized")}},
            "Price": {"number": product.get("price")},
            "Signal Score": {"number": product.get("signal_score", 0)}
        }
    )

def save_influencer_to_notion(influencer: dict):
    notion.pages.create(
        parent={"database_id": os.getenv("NOTION_INFLUENCERS_DB")},
        properties={
            "Handle": {"title": [{"text": {"content": influencer["handle"]}}]},
            "Areas of Interest": {"multi_select": [{"name": a} for a in influencer.get("areas_of_interest", [])]},
            "Preferred Promotion Styles": {"multi_select": [{"name": s} for s in influencer.get("preferred_promotion_styles", [])]}
        }
    )
```

## 5. Install
```bash
pip install notion-client
```

Wire the save functions into the `/enrich` and `/onboard-influencer` endpoints when ready.
