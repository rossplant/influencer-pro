# Deployment Guide — Plantr API

## Local

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Open `/onboarding_form.html` on that same host. The page posts to the API at its own origin.

## Railway (recommended free tier)

1. Push repo to GitHub (already done).
2. Go to railway.app → New Project → Deploy from GitHub repo.
3. Select `influencer-pro`.
4. Add environment variables from `.env.example` if needed. `NOTION_*` is unused. Notion is not the database. Postgres is the planned system of record. The default model is `grok-4.3`.
5. Railway auto-detects Python. Add a start command:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
6. Deploy. You’ll get a public URL.

## Render

1. render.com → New Web Service → Connect GitHub.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Vercel (serverless)

Use FastAPI + Mangum or a separate serverless adapter. Railway/Render are simpler for this stage.

## Next after deploy
- Open the onboarding form on the deployed host, or pass `?api=` if it is hosted elsewhere.
- Set `API_KEYS` when you want `X-API-Key` required on the API routes. `GET /` stays public.
- Shopify sync stays a 501 until it is implemented. Do not add Notion tokens for storage.
