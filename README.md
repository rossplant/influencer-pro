# Plant Influencer Enablement Platform

AI-powered overlay for TikTok Shop and other platforms.

## What's in this repo

- `product_schema.json` — Product data schema optimized for TikTok Shopping Assistant
- `influencer_schema.json` — Influencer profile schema (interests, expertise, promo styles)
- `Plant_Influencer_Platform_Overview.md` — Full blueprint, matching logic, AI signal layer, unit economics
- `ai_enrichment_example.py` — Signal scoring + enrichment skeleton
- `matching_example.py` — Product-to-influencer matching

## Quick start

```bash
python ai_enrichment_example.py
python matching_example.py
```

## Grok / xAI setup

This repo now supports an optional Grok integration via the xAI API. Add one of these to your `.env` file or deployment secrets:

```bash
GROK_API_KEY=your_xai_api_key
GROK_MODEL=grok-2-latest
# or
XAI_API_KEY=your_xai_api_key
XAI_MODEL=grok-2-latest
```

Then start the API:

```bash
uvicorn main_expanded-4:app --reload
```

If no Grok key is set, the app falls back to the existing local placeholder logic.

## Next build steps

1. Import schemas into Notion
2. Connect Shopify for product sync
3. Turn enrichment + matching into a FastAPI service
4. Add influencer onboarding flow
5. Add Grok-powered enrichment for richer creator talking points and FAQs

See `Plant_Influencer_Platform_Overview.md` for the full model.
