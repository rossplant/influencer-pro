# Deployment Guide — Plant Influencer API

## Local

```bash
pip install -r requirements.txt
uvicorn main_expanded:app --reload
```

Open `onboarding_form.html` in a browser (it posts to http://localhost:8000).

## Railway (recommended free tier)

1. Push repo to GitHub (already done).
2. Go to railway.app → New Project → Deploy from GitHub repo.
3. Select `influencer-pro`.
4. Add environment variables if needed later (Shopify token, Notion token).
5. Railway auto-detects Python. Add a start command:
   ```
   uvicorn main_expanded:app --host 0.0.0.0 --port $PORT
   ```
6. Deploy. You’ll get a public URL.

## Render

1. render.com → New Web Service → Connect GitHub.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main_expanded:app --host 0.0.0.0 --port $PORT`

## Vercel (serverless)

Use FastAPI + Mangum or a separate serverless adapter. Railway/Render are simpler for this stage.

## Next after deploy
- Point the onboarding form at the live URL.
- Add Notion / Shopify tokens as secrets.
- Add authentication if needed.
