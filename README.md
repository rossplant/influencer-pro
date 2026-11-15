# Plantr

AI-powered overlay for TikTok Shop and other platforms. `main.py` is the API entry point.

## What's in this repo

- `main.py` — FastAPI app (`uvicorn main:app`), version 0.10
- `grok_helper.py` — xAI enrichment with a local fallback
- `models.py`, `db.py`, `events.py`, `services.py` — SQLAlchemy persistence, `events.emit`, and plan limits
- `alembic/` — schema migrations. `0001_initial` is the current head
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
| `API_KEYS` | Comma-separated keys. When set, `/enrich`, `/match`, `/onboard-influencer`, `/sync-shopify`, `/shops`, and `/creators` require `X-API-Key`. `GET /` stays public. Leave unset until keys are ready. |
| `CORS_ORIGINS` | Comma-separated browser origins, or `*`. Defaults to `*` and is read when the process starts. |
| `MAX_BODY_BYTES` | Maximum request body size. Defaults to `1048576`. |
| `RATE_LIMIT_REQUESTS` | Requests allowed per window for each valid API key, or per client IP when the request has no valid key. Defaults to `30`. |
| `RATE_LIMIT_WINDOW_SECONDS` | Length of that window. Defaults to `60`. |
| `DATABASE_URL` | Postgres URL. `postgres://` and `postgresql://` are rewritten to `postgresql+psycopg://`. When unset, the API logs a warning and keeps the in-memory fallback so it can boot before Postgres is attached. The URL is not logged. |
| `SHOPIFY_STORE` and `SHOPIFY_TOKEN` | Both must be set or `/sync-shopify` reports that Shopify is not configured. The sync itself is not implemented in this version. |
| `NOTION_TOKEN`, `NOTION_PRODUCTS_DB`, `NOTION_INFLUENCERS_DB` | Optional and unused. `main.py` does not read them. Notion is not the database. |

## Persistence

When `DATABASE_URL` is set, startup runs `alembic upgrade head` (revision `0001_initial`) and then deletes `api_requests` rows older than 90 days. A Postgres advisory lock wraps the migration so a single replica applies it safely; a second replica waits instead of racing. No Railway pre-deploy command is required. The in-memory profiles that existed before Postgres was attached are not imported.

`events.emit(name, entity, actor, **props)` inserts an append-only `events` row. For `product_enriched`, `match_run`, and `brief_generated` it also increments the shop's usage counter and sets `first_enrich_at`, `first_match_at`, or `first_brief_at` the first time. Shop events copy `plan_tier`, `plan_status`, and the usage counters into `properties`. Operator actions use the same event names with `actor="operator"`.

Starter allows 50 product enrichments and 5 match runs per UTC calendar month (`docs/PlantR_Pricing.md`). At the cap the API writes `usage_limit_hit`. A non-pilot shop is blocked with `402`. A pilot shop (`is_pilot`) is allowed through and the event is still written. Pilot shops default to `plan_tier=starter` and MRR 0. MRR is the monthly price, an annual price divided by 12, or 0 for free, trial, and starter.

Each request writes a JSON line to stdout (`ts`, `route`, `method`, `status`, `latency_ms`, `key`) and, when the database is configured, an `api_requests` row. The key is a `sha256:` label. Request and response bodies are not logged or stored. Each Grok call writes its own JSON line (`model`, `prompt_tokens`, `completion_tokens`, `fallback`, `latency_ms`).

`subscriptions`, `subscription_changes` (including `change_type='contraction'` and `upgrade_trigger_snapshot`), `monthly_mrr_snapshots`, and `attributed_orders` are created by the migration and have no endpoints yet.

## Endpoints

- `GET /` — public status. `version` is `0.10`.
- `POST /shops` — create a shop. `is_pilot` and `plan_tier` are settable. The response includes `mrr`.
- `GET /shops/{id}` and `GET /creators/{id}` — read one row.
- `POST /enrich` — talking points and FAQs. The response keeps the existing fields and adds `source` (`grok` or `fallback`). Pass `shop_id` and/or `product_id` to store the product, write a brief, and emit `product_enriched`. Without those ids the call behaves as before. Token counts are logged, not returned.
- `POST /match` — body `{"influencer": {...}, "products": [...]}`. Returns up to 10 matches, each `{product, match_score}`. Scoring is still the placeholder. When a database is configured the call also stores a shortlist with `match_version='placeholder-0'` and emits `match_run`. Optional `shop_id` and `product_id` attach that shortlist to a shop and product.
- `POST /onboard-influencer` — validates a profile against `influencer_schema.json`, stores the creator, emits `creator_onboarded`, and returns `id`. With a database, `id` is the row id. The response shape stays `{id, influencer}`.
- `POST /sync-shopify` — `501`. The message says Shopify sync isn't configured until both Shopify variables are set. With those variables set, the response still says sync is not implemented, and no products are synced.

`POST /shops`, `GET /shops/{id}`, and `GET /creators/{id}` use the same `X-API-Key` rule as the other guarded routes.

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```

GitHub Actions installs `requirements.txt`, `pytest`, and `httpx`, then runs `test_api.py` and `tests/` on every push and pull request. The xAI call is mocked. Persistence tests use a temporary SQLite file, so CI does not need Postgres.

See `docs/Plant_Influencer_Platform_Overview.md` for the product model.
