# Deploy Plantr Collab to Railway

## 1. Prepare
- Repo is already on GitHub: https://github.com/rossplant/influencer-pro
- The API entry point is `main.py`.
- Create `.env` locally first to test, then add the same variables as Railway secrets. See `.env.example`.

## 2. Railway
1. Go to https://railway.app → New Project
2. Deploy from GitHub repo → select `influencer-pro`
3. Settings → Variables → add the values from `.env.example` that this deploy needs. `main.py` reads `DATABASE_URL`, `GROK_API_KEY` (or `XAI_API_KEY`), `GROK_MODEL` (default `grok-4.3`), `API_KEYS`, `CORS_ORIGINS`, `MAX_BODY_BYTES`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`, `SHOPIFY_STORE`, and `SHOPIFY_TOKEN`. `NOTION_*` is optional and unused. Notion is not the database.
4. Attach Postgres to the service and set `DATABASE_URL` to the plugin reference, `${{Postgres.DATABASE_URL}}`. Railway's value uses `postgres://`; the API rewrites it to `postgresql+psycopg://`. Leave `DATABASE_URL` unset only if you want the in-memory fallback. Do not add a pre-deploy command: startup runs `alembic upgrade head` and then purges `api_requests` older than 90 days.
5. Settings → Deploy → Start Command:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
   ```
6. Deploy. Railway gives you a public URL (e.g. `plantr-production.up.railway.app`). In-memory profiles from before Postgres was attached are not copied over.

## 3. Render (alternative)
1. https://render.com → New Web Service → Connect GitHub
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
4. Add the same environment variables.

## 4. After Deploy
- The HTML pages call the API on the same origin. Open `/plantr_dashboard.html` on the Railway host, or pass `?api=` to point at another host.
- `POST /sync-shopify` returns 501 until sync is implemented. The onboarding form posts to `/onboard-influencer`.
