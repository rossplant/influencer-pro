# Plant Influencer Enablement Platform

AI-powered overlay for TikTok Shop and other platforms. `main.py` is the API entry point.

## What's in this repo

- `main.py` — FastAPI app (`uvicorn main:app`)
- `grok_helper.py` — xAI enrichment with a local fallback
- `product_schema.json` — product data schema
- `influencer_schema.json` — influencer profile schema
- `plantr_onboarding.html`, `onboarding_form.html`, `plantr_dashboard.html`, `plantr_admin.html` — pages served by the API
- `ai_enrichment_example.py` — signal scoring sketch
- `matching_example.py` — product-to-influencer matching sketch
- `docs/` — planning notes
- `archive/` — earlier API drafts
- `docs/archive/` — duplicate copies of the planning notes

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set `PORT=8000` when you run that command locally. The default xAI model is `grok-4.3`.

Open `/plantr_admin.html`, `/plantr_dashboard.html`, `/plantr_onboarding.html`, or `/onboarding_form.html` on that same host. The pages call the API at their own origin. Override the base with `?api=https://your-api.example` or `window.PLANTR_API_BASE`. When `API_KEYS` is set, pass the key as `?key=` or `window.PLANTR_API_KEY` and the pages send it as `X-API-Key`.

## Environment

Copy `.env.example`. `main.py` reads these variables:

| Variable | Purpose |
| --- | --- |
| `GROK_API_KEY` or `XAI_API_KEY` | xAI key. If neither is set, `/enrich` uses the local fallback. |
| `GROK_MODEL` | xAI model. Defaults to `grok-4.3` when unset. |
| `API_KEYS` | Comma-separated keys. When set, `/enrich`, `/match`, `/onboard-influencer`, and `/sync-shopify` require `X-API-Key`. `GET /` stays public. Leave unset until keys are ready. |
| `CORS_ORIGINS` | Comma-separated browser origins, or `*`. Defaults to `*` and is read when the process starts. |
| `MAX_BODY_BYTES` | Maximum request body size. Defaults to `1048576`. |
| `RATE_LIMIT_REQUESTS` | Requests allowed per window for each valid API key, or per client IP when the request has no valid key. Defaults to `30`. |
| `RATE_LIMIT_WINDOW_SECONDS` | Length of that window. Defaults to `60`. |
| `SHOPIFY_STORE` and `SHOPIFY_TOKEN` | Both must be set or `/sync-shopify` reports that Shopify is not configured. The sync itself is not implemented in this version. |
| `NOTION_TOKEN`, `NOTION_PRODUCTS_DB`, `NOTION_INFLUENCERS_DB` | Optional and unused. `main.py` does not read them. Notion is not the database. |

Influencer records and rate-limit counters live in process memory. They reset on deploy and are not shared across replicas. Postgres is the planned store for influencer profiles.

## Endpoints

- `GET /` — public status. `version` is `0.9`.
- `POST /enrich` — talking points and FAQs. The response keeps the existing fields and adds `source` (`grok` or `fallback`). Token `usage` from xAI is written to the logs and is not returned.
- `POST /match` — body `{"influencer": {...}, "products": [...]}`. Returns up to 10 matches, each `{product, match_score}`.
- `POST /onboard-influencer` — validates a profile against `influencer_schema.json`, stores it in memory, and returns `id`.
- `POST /sync-shopify` — `501`. The message says Shopify sync isn't configured until both Shopify variables are set. With those variables set, the response still says sync is not implemented, and no products are synced.

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```

GitHub Actions installs `requirements.txt`, `pytest`, and `httpx`, then runs `test_api.py` and `tests/` on every push and pull request. The xAI call is mocked.

See `docs/Plant_Influencer_Platform_Overview.md` for the product model.
