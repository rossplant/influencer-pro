# Influencer Onboarding Flow — Plant App

## Goal
Influencers enter their areas of interest, expertise, and preferred promotion styles so Plant can target products to them.

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

### 4. What Phase 0 ships
- `POST /onboard-influencer` validates the body against `influencer_schema.json`, stores the profile in process memory, and returns an `id`.
- That store is not persistent. Records disappear on restart and are not shared across replicas.
- Postgres is the planned system of record.
- Notion is not the database. This endpoint does not write to Notion.
- `POST /match` ranks the products in the request. It does not load them from Notion or Shopify.

### 5. Next Implementation
- Persist influencer profiles in Postgres.
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
