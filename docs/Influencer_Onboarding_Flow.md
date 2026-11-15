# Creator onboarding — Plantr Collab

Plantr is the company. Plantr Collab is the product. It is for any category: small-to-mid Shopify sellers and nano and micro creators. There is no starting vertical.

## Goal
Creators enter the niches they will talk about, how they like to promote, and what they will not promote, so a seller can see relevant matches.

## Steps

### 1. Profile
Fields follow `influencer_schema.json`:
- Handle
- Name
- Bio
- Areas of interest (tags the creator will talk about). Do not assume a category.
- Expertise level per interest (beginner / intermediate / expert)
- Preferred promotion styles: educational, unboxing, before-after, lifestyle, gift guide, review, LIVE demo, storytelling
- Restrictions: generic tags the creator will not promote
- `active_categories`: taxonomy slugs the creator works in now. `desired_categories`: slugs they want to work in. Both use the shared list (`apparel`, `beauty`, `electronics`, `food-and-drink`, `health`, `home`, `kids`, `sports`, `other`). A product subcategory is free text, not a second vertical.
- Optional performance: `avg_views`, `engagement_rate` (fraction), `est_conversion_rate` (fraction), `avg_order_value`, `typical_price_band` (`{"min", "max"}`)
- `excluded_categories` and `excluded_claims`: taxonomy slugs and claim text the creator refuses
- Availability (open / selective / paused)
- Platforms and follower band, when known
- Commission preference

### 2. Validation
`POST /onboard-influencer` requires a handle, at least one area of interest, and at least one preferred promotion style from the schema enum. Availability must be `open`, `selective`, or `paused`.

### 3. What is stored
- The response is `{id, influencer}`.
- When `DATABASE_URL` is set, the profile is a `creators` row. `id` is the database id. `interests` stores the areas of interest. `restrictions` is a generic tag list. `excluded_categories` and `excluded_claims` are stored separately. The call emits `creator_onboarded` with `actor='api'`, which sets `profile_completed_at` the first time.
- A second profile with the same handle is rejected with 409.
- When `DATABASE_URL` is unset, the API logs a warning and keeps the profile in process memory. That copy is lost on restart. Notion is not the database.
- `GET /creators/{id}` reads the stored profile. It uses the same `X-API-Key` rule as onboarding.

### 4. Matching
`POST /match` still uses the placeholder scorer and returns up to 10 `{product, match_score}` rows for the products in the request. With a database it also writes a shortlist (`match_version='placeholder-0'`) and emits `match_run`. It does not load a catalogue from Notion or Shopify.

## Sample payload
```json
{
  "handle": "@creator",
  "name": "Alex",
  "areas_of_interest": ["one niche they cover", "another niche they cover"],
  "preferred_promotion_styles": ["educational", "lifestyle"],
  "restrictions": ["no competitor brands"],
  "excluded_categories": [],
  "excluded_claims": [],
  "availability": "open"
}
```
