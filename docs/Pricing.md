# Plantr Collab pricing

Plantr is the company. Plantr Collab is the product: a brand–creator collaboration tool for any category. It is aimed at small-to-mid Shopify sellers and nano and micro creators. There is no starting vertical.

## Plan tiers in the API

`shops.plan_tier` defaults to `starter`. A pilot shop (`is_pilot`) defaults to `starter`, `plan_status` `free`, and MRR 0. Growth and Scale default to `plan_status` `active` when status is omitted. `trial` and `trialing` both count as MRR 0.

The product cap is the number of product rows in the shop's catalogue. Enriching a product that already exists does not use another slot.

| plan_tier | Catalogue | Match runs | Price at paid launch |
| --- | --- | --- | --- |
| starter | 50 products | 5 per calendar month (UTC) | US$0 |
| growth | 500 products | no monthly cap | US$49 / month or US$490 / year |
| scale | unlimited | no monthly cap | US$149 / month or US$1,490 / year |

When a shop is already at a cap, the API writes `usage_limit_hit`. A non-pilot shop is then blocked. A pilot shop is allowed to continue, and the event is still written.

### MRR

- Monthly list price is the MRR: Growth 49, Scale 149.
- Annual list price / 12 is the MRR: Growth 490, Scale 1490.
- Free, trial, trialing, and starter contribute 0.

`monthly_mrr_snapshots` stores that number. Nothing writes the table from an endpoint yet.

## For sellers

### Starter — US$0
- Up to 50 products in the catalogue
- Up to 5 match runs per UTC month
- Basic enrichment

### Growth — US$49 / month, or US$490 / year
- Up to 500 products
- AI enrichment (signal score, talking points, common questions)
- Shopify sync when it ships
- Creator match recommendations
- No monthly match cap in this version

### Scale — US$149 / month, or US$1,490 / year
- Unlimited products
- Unlimited matches in this version
- Shopify sync when it ships
- API access

**Performance fee, not at launch.** A 5% fee on attributed GMV is held until order attribution works. It is not billed now.

## For creators

Creators are free at launch. A launch creator can keep a profile and see matches. There is no creator subscription at launch.

### Later options (not launch tiers)

#### Creator Pro — US$19 / month
- Unlimited matches
- Priority in seller recommendations
- Talking points generated for each product
- Performance analytics

#### Elite (invite only)
- Brand partnerships
- Higher commission share
- Co-branded campaigns

## How money flows
1. The seller pays the Plantr Collab subscription. The performance fee waits until attribution works.
2. The creator earns commission from the seller.
3. Plantr may later take a small cut of commission on Elite deals, or stay on subscription pricing only.

## Positioning
- Sellers pay for clearer product signals and relevant creators, in any category.
- Creators are free at launch. Creator Pro and Elite are later options.
- Plantr Collab does not hold inventory.
