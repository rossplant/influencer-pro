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
- At least 3 areas of interest required
- At least 1 preferred promotion style required

### 3. Matching Trigger
After save:
- Call `/match` endpoint with the influencer profile against current product catalog
- Show top 10 product matches with match score + creator talking points
- Allow influencer to request samples or generate content

### 4. API Endpoints (already in main_expanded.py)
- `POST /onboard-influencer` — save profile
- `POST /match` — return ranked products

### 5. Next Implementation
- Frontend form (React / simple HTML)
- Save to Notion database
- Notify influencer of new matches via email or in-app

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
