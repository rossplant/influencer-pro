# Plantr

Duplicate upload. Not the current description. See `docs/Plant_Influencer_Platform_Overview.md`. Notion is not the database.
## Product Data Schema, Influencer Profiles & AI Signal Layer

**Brand:** Plantr  
**Purpose:** AI-powered overlay for TikTok Shop (and other platforms) that enriches product data so Shopping Assistants surface products, and matches them to influencers based on expertise + promotions.

### Core Idea
Merchants supply basic product data → Plantr enriches it into high-signal structured attributes → TikTok Shopping Assistant (and similar AI buyers) can answer questions accurately → Influencers get targeted product matches based on their stated interests, expertise, and preferred promo styles.

---

## 1. Product Data Schema

Parent + Variants model. Optimized for conversational AI (TikTok Shopping Assistant, Rufus-style agents).

### Core Product Fields (Parent)

| Field | Type | Required | Description | AI Signal Weight |
|-------|------|----------|-------------|------------------|
| product_id | string | Yes | Unique Plantr ID | - |
| title | string | Yes | Clear, keyword-rich title | High |
| brand | string | Yes | Brand name | High |
| category | string | Yes | Primary category (e.g. Home & Garden > Plants) | High |
| subcategory | string | No | More specific | Medium |
| description_short | string | Yes | 1-2 sentence factual summary | High |
| description_long | string | Yes | Detailed, benefit-focused, question-answering | High |
| key_features | array[string] | Yes | Bullet list of facts | High |
| materials | array[string] | No | What it’s made of | Medium |
| dimensions | object | No | {length, width, height, unit} | Medium |
| weight | object | No | {value, unit} | Low |
| care_instructions | string | No | Especially important for plants | High |
| target_audience | array[string] | No | e.g. ["beginners", "small apartments"] | High |
| occasions | array[string] | No | e.g. ["gift", "home office"] | High |
| claims | array[object] | No | See Claims Object below | High |
| images | array[string] | Yes | URLs | Medium |
| video_urls | array[string] | No | Product videos | Medium |
| price | number | Yes | Current price | Medium |
| compare_at_price | number | No | Original price | Low |
| currency | string | Yes | USD, etc. | - |
| inventory_status | string | Yes | in_stock / low_stock / out_of_stock | High |
| shipping | object | Yes | See Shipping Object | Medium |
| signal_score | number | Computed | 0-100 AI readiness score | - |
| creator_talking_points | array[string] | Computed | Ready-to-use hooks for influencers | - |
| common_questions | array[object] | Computed | Predicted Q&A for Shopping Assistant | - |

### Variant / SKU Schema

| Field | Type | Description |
|-------|------|-------------|
| sku | string | Unique SKU |
| variant_title | string | e.g. "Small", "Medium", "Large" |
| option1_name | string | Size / Color / etc. |
| option1_value | string | |
| price | number | |
| inventory_quantity | integer | |
| barcode | string | |
| weight | object | |

### Claims Object
```json
{
  "claim": "Pet-safe",
  "evidence": "Certified by ASPCA",
  "confidence": "verified"
}
```

### Shipping Object
```json
{
  "ships_from": "US",
  "handling_time_days": 1,
  "free_shipping_threshold": 50,
  "estimated_delivery_days": "3-5"
}
```

### Computed AI Enrichment Fields
- **signal_score**: Completeness + factual density + question coverage (0-100)
- **creator_talking_points**: 5 short hooks tailored for video
- **common_questions**: Array of {question, answer} the Shopping Assistant is likely to ask

---

## 2. Influencer Profile Schema

Influencers enter their interests, expertise, and preferred promotion styles. Plantr uses this for targeted matching.

### Influencer Fields

| Field | Type | Description |
|-------|------|-------------|
| influencer_id | string | Unique ID |
| handle | string | TikTok / Instagram handle |
| name | string | Display name |
| bio | string | Short bio |
| areas_of_interest | array[string] | e.g. ["houseplants", "urban gardening", "sustainable living"] |
| expertise_level | object | {area: "beginner"|"intermediate"|"expert"} |
| preferred_promotion_styles | array[string] | e.g. ["educational", "unboxing", "before-after", "lifestyle", "gift guide"] |
| audience_demographics | object | {age_range, gender_skew, location_focus} |
| content_formats | array[string] | ["short video", "LIVE", "carousel"] |
| average_engagement_rate | number | Optional |
| past_categories | array[string] | Categories they’ve promoted before |
| restrictions | array[string] | e.g. ["no live animals", "only organic"] |
| availability | string | open / selective / paused |
| commission_preference | string | standard / higher for exclusives |

### Matching Logic (High Level)
1. Extract product tags: category, target_audience, occasions, claims, key features.
2. Score overlap with influencer’s `areas_of_interest` + `expertise_level`.
3. Boost if `preferred_promotion_styles` matches the product’s natural content angle (e.g. care instructions → educational).
4. Filter by restrictions and availability.
5. Return ranked list of product matches with suggested talking points.

---

## 3. AI Signal Layer (TikTok Shopping Assistant Focus)

TikTok Shopping Assistant relies heavily on merchant-supplied structured attributes to answer questions conversationally.

### Enrichment Pipeline
1. **Ingest** basic product data from Shopify or manual entry.
2. **Extract** entities (materials, use cases, audience, occasions).
3. **Generate** missing high-value fields:
   - Expanded description that anticipates questions
   - key_features
   - care_instructions (plants)
   - target_audience
   - occasions
   - claims with evidence
4. **Predict** common_questions the Assistant will receive.
5. **Score** signal_score (completeness + answerability).
6. **Output** creator_talking_points for influencers.

### Example AI Prompt (Enrichment)
```
You are enriching a product for TikTok Shopping Assistant.
Product title: {title}
Raw description: {description}

Generate:
1. A factual long description that answers likely questions.
2. 6 key features as short bullets.
3. Target audiences.
4. Occasions / use cases.
5. 5 predicted common questions with clear answers.
6. 5 short video talking points for influencers.
Keep everything factual. Do not invent certifications.
```

### Signal Score Calculation (Simple Version)
- Title + short desc + long desc present: +20
- Key features ≥ 5: +15
- Target audience + occasions filled: +15
- Claims present: +10
- Common questions ≥ 5: +20
- Care instructions (if relevant): +10
- Images ≥ 3: +10
Max 100. Score ≥ 80 = “Assistant-ready”.

---

## 4. Sample Unit Economics (Influencer App)

| Metric | Value | Notes |
|--------|-------|-------|
| Average order value | $45 | Mid-ticket plants / home |
| Affiliate commission | 12% | Typical TikTok Shop range 5-20% |
| Plantr take rate | 3-5% of GMV or flat SaaS + performance | Hybrid |
| Creator payout | 8-10% | After Plantr share |
| Target net margin (Plantr) | 25-40% on performance fees | After AI costs |
| Onboarding cost per merchant | Low (AI-first) | Mostly software |

---

## Next Steps
1. Keep product and influencer records out of Notion. Postgres is the planned system of record.
2. Build simple matching function (Python example provided in code files).
3. Connect Shopify later for product sync.
4. Host enrichment service on GitHub + Vercel / Railway.

Files generated:
- `product_schema.json` — machine-readable schema
- `influencer_schema.json`
- `ai_enrichment_example.py` — sample enrichment + scoring
- `matching_example.py` — simple influencer-product matcher
