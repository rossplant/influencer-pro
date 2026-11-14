# Deploy PlantR to Railway (recommended)

## 1. Prepare
- Repo is already on GitHub: https://github.com/rossplant/influencer-pro
- Make sure `main_expanded-1.py` is renamed to `main_expanded.py` (or update start command).
- Create `.env` locally first to test, then add the same variables as Railway secrets.

## 2. Railway
1. Go to https://railway.app → New Project
2. Deploy from GitHub repo → select `influencer-pro`
3. Settings → Variables → add:
   - NOTION_TOKEN
   - NOTION_PRODUCTS_DB
   - NOTION_INFLUENCERS_DB
   - SHOPIFY_STORE
   - SHOPIFY_TOKEN
4. Settings → Deploy → Start Command:
   ```
   uvicorn main_expanded:app --host 0.0.0.0 --port $PORT
   ```
5. Deploy. Railway gives you a public URL (e.g. `plantr-production.up.railway.app`).

## 3. Render (alternative)
1. https://render.com → New Web Service → Connect GitHub
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn main_expanded:app --host 0.0.0.0 --port $PORT`
4. Add the same environment variables.

## 4. After Deploy
- Update the frontend (`plantr_dashboard.html`) to point to the live URL instead of `localhost:8000`.
- Test `/sync-shopify` and the onboarding form.
