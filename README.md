# Plantr Collab

Plantr is the company. Plantr Collab is the product: brand–creator collaboration for any category, for small-to-mid Shopify sellers and nano and micro creators. There is no starting vertical. `main.py` is the API entry point.

## What's in this repo

- `main.py` — FastAPI app (`uvicorn main:app`), version 0.25.0
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
uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
```

Set `PORT=8000` when you run that command locally. The default xAI model is `grok-4.3`.

Open `/admin`, `/plantr_dashboard.html`, `/plantr_onboarding.html`, or `/onboarding_form.html` on that same host. `/plantr_admin.html` redirects to `/admin`. The pages call the API at their own origin. Override the base with `?api=https://your-api.example` or `window.PLANTR_API_BASE`. Pages do not read an API key from the URL. The dashboard sends the admin session cookie.

## Environment

Copy `.env.example`. `main.py` reads these variables:

| Variable | Purpose |
| --- | --- |
| `GROK_API_KEY` or `XAI_API_KEY` | xAI key. If neither is set, `/enrich` uses the local fallback. |
| `GROK_MODEL` | xAI model. Defaults to `grok-4.3` when unset. |
| `API_KEYS` | Comma-separated keys. Protected routes accept a matching `X-API-Key` or an admin session. `GET /`, the onboarding pages, `POST /onboard-influencer`, `/docs`, and `/openapi.json` stay public. Production and staging fail closed with 401 when this is unset and there is no admin session. |
| `SESSION_SECRET` | Signs the admin session cookie. Required for `/admin`. |
| `ADMIN_EMAIL` | The single admin email. Required for `/admin`. |
| `ADMIN_SETUP_TOKEN` | Required only until `POST /admin/setup` creates the admin user. Compare it in the browser form. Remove it afterwards. |
| `ADMIN_PASSWORD_HASH` | Optional scrypt or bcrypt fallback. The normal password is set in the browser and stored in `admin_users`. `python -m tools.hash_password` reads a password from stdin and prints a scrypt hash. |
| `CORS_ORIGINS` | Comma-separated browser origins. Unset means same-origin only. A wildcard is never used together with credentials. Read when the process starts. |
| `MAX_BODY_BYTES` | Maximum request body size. Defaults to `1048576`. |
| `RATE_LIMIT_REQUESTS` | Requests allowed per window for each valid API key, or per client IP when the request has no valid key. Defaults to `30`. |
| `RATE_LIMIT_WINDOW_SECONDS` | Length of that window. Defaults to `60`. |
| `DATABASE_URL` | Postgres URL. `postgres://` and `postgresql://` are rewritten to `postgresql+psycopg://`. When unset, the API logs a warning and keeps the in-memory fallback so it can boot before Postgres is attached. The URL is not logged. |
| `PUBLIC_BASE_URL` | Public https origin, with no trailing path. Checkout, the billing portal, and tracking links use it. Behind Railway, set this to the public site URL. |
| `SHOPIFY_STORE` and `SHOPIFY_TOKEN` | Both must be set or `/sync-shopify` reports that Shopify is not configured. The sync itself is not implemented in this version. |
| `NOTION_TOKEN`, `NOTION_PRODUCTS_DB`, `NOTION_INFLUENCERS_DB` | Optional and unused. `main.py` does not read them. Notion is not the database. |

## Persistence

When `DATABASE_URL` is set, startup runs `alembic upgrade head` (revision `0001_initial`) and then deletes `api_requests` rows older than 90 days. A Postgres advisory lock wraps the migration so a single replica applies it safely; a second replica waits instead of racing. No Railway pre-deploy command is required. The in-memory profiles that existed before Postgres was attached are not imported.

`events.emit(name, entity, actor, **props)` inserts an append-only `events` row. `product_enriched` increments `products_enriched` and sets `first_enrich_at` the first time. `match_run` increments `matches_run`, sets `first_match_at` the first time, and sets `last_match_at` every time. `brief_sent` (not enrichment) increments `briefs_generated`, sets `first_brief_at` the first time, and sets `last_campaign_at` every time. Shop events copy `plan_tier` and `plan_status` into `properties`. Catalogue and period counters are columns on the event. Names are listed in `docs/event_names.md`. Operator actions use the same names with `actor="operator"`.

Starter allows 1 campaign and 3 match runs per UTC calendar month (`docs/Pricing.md`). Growth allows 5 campaigns and 25 matches. Scale does not cap campaigns and allows 100 matches. At a campaign or match cap the API writes `usage_limit_hit`. A non-pilot shop is blocked with `402`. A pilot shop is allowed through. Product counts are still written on shop events (`products_count`, `product_limit`, including the old Starter 5 and Growth 50 figures). Those product figures are not a hard block. Above `CATALOGUE_FEE_THRESHOLD` (default 10) the seller sees a warning that extra products may attract a fee after the pilot. The add is not blocked and nothing is charged. Crossing that line writes `catalogue_fee_threshold`. Prices are AUD ex GST: Starter A$29 or A$290 a year, Growth A$79 or A$790 a year, Scale A$229 or A$2,290 a year. Annual MRR is that price / 12. Unsubscribed, free, trial, and trialing contribute 0. An active Starter subscription contributes 29.

If `alembic_version` is `0001_initial` but the tables still have the pre-analytics shape (`attributed_orders.amount`, or `events` without `products_count`), startup drops those Plantr Collab tables and recreates them from the edited `0001` revision. Production is not on 0.10 and has no tables, so this does not run there. Do not rely on that drop once production has data.

Each request writes a JSON line to stdout (`ts`, `route`, `method`, `status`, `latency_ms`, `key`) and, when the database is configured, an `api_requests` row. The key is a `sha256:` label. Request and response bodies are not logged or stored. Each Grok call writes its own JSON line (`model`, `prompt_tokens`, `completion_tokens`, `fallback`, `latency_ms`).

`subscriptions`, `subscription_changes` (including `change_type='contraction'` and `upgrade_trigger_snapshot`), `monthly_mrr_snapshots`, and `attributed_orders` are created by the migration and have no endpoints yet.

## Endpoints

- `GET /` — public status. The status text is `Plantr Collab API running`. `version` is `0.25.0`.
- `/seller/profile` — edit the trading name, optional ABN, primary category, website, and Shopify domain. Joining with an invite copies the pilot application's business name, ABN, first category, and website onto the shop. The account name stays the name from the invite.
- `/seller/products` — a short curated list. Each product needs why it was chosen, the value for creators, and a commission before it can be matched. A CSV upload previews rows with nothing ticked; ticked rows can be saved as drafts (`source=csv`) or finished one by one. Drafts are not matched. Template: `GET /seller/products/csv-template`.
- `/seller/billing` creates or reuses a Stripe Customer. `POST /stripe/webhook` verifies the signature and stores events once.
- `/seller` — shop login, profile, products, find creators, accept or decline, and accepted creators with a tracking code when one exists.
- `/creator` — creator login, categories, accepted offers, and tracking codes. `/plantr_dashboard.html` redirects here.
- Admin shop and creator details can create those logins and open “view as”.
- `POST /creator-product-links` and `GET /creator-product-links` — admin session. One tracking code per creator and product. A second create returns the same code.
- `GET /r/{code}` — public redirect. Logs `link_clicked` with `source_platform` (`?s=tt|ig|yt|fb|ot`, or `unknown`) and `channel_kind` (`link`, or `ad` when `c=ad`), then sends the browser to the product URL. The same link is also issued as a direct seller URL: the product URL plus `?plantr=<code>` and the same `s` param. `GET /creator/api/links` returns one of each per platform.
- `POST /attributed-orders/import` — admin session. CSV of order id, date, code or link, gross amount, currency, and shop. Idempotent on shop and order id. `dry_run` writes nothing. Row errors are returned with the row.
- Shopify order sync (v0.18.0, `shopify_sync.py`). Two ways to connect: the pilot bridge (`POST /seller/api/shopify/store-webhooks` with the store domain and the signing key from Settings > Notifications > Webhooks; the seller creates Order creation, Order update and Refund create webhooks pointing at the URL returned) or the Plantr Collab app (`GET /seller/shopify/install?shop=` then `/shopify/oauth/callback`, read_orders, expiring offline tokens). Webhooks: `POST /shopify/webhooks` (app, HMAC with `SHOPIFY_API_SECRET`) and `POST /shopify/webhooks/store/{token}` (bridge, HMAC with the store key). Bad HMAC returns 401; repeats are ignored by webhook id. Only orders with a Plantr link reference (`pc_ref`, added by `/r/{code}` for connected shops) or a creator code are stored, with no customer details. Net sales exclude shipping and GST and subtract refunds. When link and code point at different creators, the code wins. `POST /admin/api/shopify/{shop_id}/backfill` pulls up to 60 days (app mode). Secrets are encrypted with `SHOPIFY_TOKEN_KEY`.
- Light commission (`commission.py`, `commission_routes.py`). Monthly statements (`POST /admin/api/commission/statements/generate`), seller mark paid, creator confirm / dispute / withdraw, admin resolve dispute after 14 days with a logged reason, admin block lift, pays-on-time record (seller and creators on that seller's briefs; never public) and seller response, seller corrections of late or missed entries (creator agrees or disagrees within 14 days, silence counts as agreement, disagreements go to an admin; entries under review leave the record), collab agreements with stored terms and both acceptances, and the no-ABN withholding warning with stored acknowledgements. Overdue or disputed amounts block only new collab offers and shortlist acceptances. No bank fields. Shopify Collabs collabs carry a double-payment warning for sellers. Settings: `COMMISSION_OVERDUE_RULE` (month_end default | payment_date), `COMMISSION_CORRECTION_WINDOW_DAYS`, `COMMISSION_OVERDUE_DAYS`, `COMMISSION_OVERDUE_GRACE_DAYS`, `COMMISSION_CONFIRM_WINDOW_DAYS`, `COMMISSION_DISPUTE_RESOLVE_AFTER_DAYS`, `COMMISSION_EXCLUDE_PLATFORM_PAID`, `COMMISSION_TIMEZONE`, `PAYS_ON_TIME_VISIBILITY`, `COLLAB_TERMS_VERSION`.
- Source platform is reporting only (v0.18.0). Orders store `source_platform` and `channel_kind` (`link`, `code`, or `ad`). A Shopify `landing_site` or `referring_site` with `plantr=<code>` counts as the click for that order (TikTok ads use `s=tt&c=ad` on the seller's own URL; the `/r/` link is the alternative). `pc_ref` links still need a click in the 7-day window. Code still beats link, and one order is one credit. The raw URL and buyer details are not stored. Optional extra codes: `POST /seller/api/links/{id}/platform-codes`. Sales by creator and platform: `GET /seller/api/commission/source-report?period=YYYY-MM` and `GET /seller/api/commission/statements/{id}/csv`.
- Spark Ads codes live on the collab (`collab_spark_ads`). Only that collab's creator can add or revoke one, with explicit consent. The code is Fernet-encrypted with `SHOPIFY_TOKEN_KEY` and is shown only to that creator and that seller, never in a list or a log. The ad destination is the seller's product URL. Revoking hides the code and the destination. Past sales stay credited. Seller views include: "Boosted posts must keep the creator's ad disclosure. Plantr doesn't run or pay for ads."
- `GET /admin/setup` and `POST /admin/setup` — set the admin password once. Afterwards these return 410.
- `POST /admin/login` and `POST /admin/logout` — signed HttpOnly session cookie, 12 hours.
- `GET /admin` — login and the read-only dashboard. Shop pilot and underserved flags, and shortlist accept or decline, are the only writes.
- `POST /shops` — create a shop. `is_pilot` and `plan_tier` are settable. The response includes `mrr`.
- `GET /shops/{id}` and `GET /creators/{id}` — read one row.
- `POST /enrich` — talking points and FAQs for any product. The response keeps the existing fields and adds `source` (`grok` or `fallback`). Pass `shop_id` and/or `product_id` to store the product (category, subcategory, price, currency, tags, attributes, claims with a source) and a brief draft, and emit `product_enriched`. That call does not set `first_brief_at`. `POST /briefs/{id}/sent` emits `brief_sent` (`actor` may be `operator`). Without shop or product ids, enrich behaves as before. Token counts are logged, not returned.
- `POST /match` — one product (`product` or `product_id`) and either `creators` or `all_onboarded: true`. Returns the top `limit` (default 10) creators with a 0–100 `rules-v1` score, component breakdown, estimates, and plain-language reasons. `optimise_for` is `balanced` (default), `reach`, `sales`, or `revenue`. Hard exclusions are listed separately and are not ranked. When a database is configured the call stores a shortlist with `match_version='rules-v1'` and emits `match_run`. Optional `shop_id` applies plan limits and the underserved-supplier boost.
- `POST /onboard-influencer` — validates a profile against `influencer_schema.json`, stores the creator, emits `creator_onboarded`, and returns `id`. With a database, `id` is the row id. The response shape stays `{id, influencer}`.
- `POST /sync-shopify` — `501`. The message says Shopify sync isn't configured until both Shopify variables are set. With those variables set, the response still says sync is not implemented, and no products are synced.

`POST /shops`, `GET /shops/{id}`, `GET /creators/{id}`, enrich, match, briefs, shortlist decisions, and Shopify sync are protected. `POST /onboard-influencer` is public and still rate-limited.

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```

GitHub Actions installs `requirements.txt`, `pytest`, and `httpx`, then runs `test_api.py` and `tests/` on every push and pull request. The xAI call is mocked. Persistence tests use a temporary SQLite file, so CI does not need Postgres.

See `docs/Pricing.md` and `docs/Plant_Influencer_Platform_Overview.md`. The overview filename is historical. The product is Plantr Collab.
