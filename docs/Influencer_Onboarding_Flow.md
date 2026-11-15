# Influencer Onboarding Flow — Plantr

## Goal
Influencers enter their areas of interest, expertise, and preferred promotion styles so Plantr can target products to them.

## Steps

### 1. Profile Creation Screen
Fields (from influencer_schema.json):
- Handle (TikTok / Instagram)
- Name
- Bio
- Areas of Interest (multi-select or tags)
  - Examples: houseplants, urban gardening, sustainable living, pet-friendly plants, low-light plants
- Expertise Level per interest (beginner / intermediate / expert)
- Preferred Promotion Styles (multi-select)
  - educational, unboxing, before-after, lifestyle, gift guide, review, LIVE demo, storytelling
- Restrictions (optional tags)
- Availability (open / selective / paused)
- Commission preference

### 2. Validation
Phase 0 (`POST /onboard-influencer` in `main.py`) requires a handle, at least one area of interest, and at least one preferred promotion style from the schema enum. Availability must be `open`, `selective`, or `paused`. A product goal of at least three interests is not enforced yet.

### 3. Matching Trigger
The HTML dashboard can call `POST /match` with the profile and a list of products. Phase 0 has no stored product catalog, so the dashboard sends demo products. `/match` returns up to 10 `{product, match_score}` rows. It does not attach creator talking points. Those come from `POST /enrich`.

### 4. What the API stores
- `POST /onboard-influencer` validates the body against `influencer_schema.json` and returns `{id, influencer}`.
- When `DATABASE_URL` is set, the profile is written to `creators`. `id` is the database id. The row keeps the schema fields plus `interests` (the areas of interest), `restrictions` (for example `no_pet_toxic`), `platforms`, `follower_band`, and `created_at`. The call emits `creator_onboarded` with `actor='api'`.
- When `DATABASE_URL` is unset, the API logs a warning and keeps the profile in process memory. That copy is lost on restart. Notion is not the database, and this endpoint does not write to Notion.
- `GET /creators/{id}` reads the stored profile back. It uses the same `X-API-Key` rule as onboarding.
- `POST /match` still ranks the products in the request with the placeholder scorer. It does not load them from Notion or Shopify. With a database it also writes a `shortlists` row (`match_version='placeholder-0'`) and `shortlist_items`, and emits `match_run`.

### 5. Next Implementation
- Replace the placeholder match score with the real scorer.
- Notify an influencer of new matches by email or in the app.

## Sample Onboarding Payload
```json
{
  "handle": "@plantlover",
  "name": "Alex",
  "areas_of_interest": ["houseplants", "low light plants", "beginner friendly"],
  "preferred_promotion_styles": ["educational", "lifestyle"],
  "expertise_level": {"houseplants": "intermediate"},
  "restrictions": [],
  "availability": "open"
}
```
