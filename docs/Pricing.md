# Plantr Collab pricing

Plantr is the company. Plantr Collab is the product: a brand–creator collaboration tool for any category. It is aimed at small-to-mid Shopify sellers and nano and micro creators. There is no starting vertical.

## Plan tiers in the API

`shops.plan_tier` defaults to `starter`. A pilot shop (`is_pilot`) defaults to `starter`, `plan_status` `free`, and MRR 0. Growth and Scale default to `plan_status` `active` when status is omitted. `trial` and `trialing` both count as MRR 0.

The product cap is the number of product rows in the shop's catalogue. Enriching a product that already exists does not use another slot.

| plan_tier | Catalogue | Campaigns | Match runs | Price, ex GST |
| --- | --- | --- | --- | --- |
| starter | 5 products | 1 | 3 per calendar month (UTC) | A$0 |
| growth | 50 products | 5 | 25 per calendar month | A$79 / month or A$790 / year |
| scale | 250+ | no cap | 100 per calendar month | A$229 / month or A$2,290 / year |

When a shop is already at a cap, the API writes `usage_limit_hit`. A non-pilot shop is then blocked. A pilot shop is allowed to continue, and the event is still written.

### MRR

- Monthly list price is the MRR: Growth 79, Scale 229.
- Annual list price / 12 is the MRR: Growth 790, Scale 2290.
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

Internal tier values stay `member`, `curator`, and `elite`. The names on screen are Membr, Curatr, and Trendsettr.

| tier | Label | Price, ex GST |
| --- | --- | --- |
| member | Membr | Included with an invite |
| curator | Curatr | A$19 / month or A$190 / year |
| elite | Trendsettr | A$49 / month or A$490 / year |

Foundrs are the invite-only cohort. They are free for the 2 month pilot (20 November 2026 through 20 January 2027), then A$9.50 a month for 12 months. The route stays `/founding-curators`.

## Pilot

Both cohorts run for 2 months, from 20 November 2026, with paid plans starting 20 January 2027.

- Suppliers: free during the pilot, then 50% off Growth (A$39.50) for 3 months, then A$79.
- Foundrs: free during the pilot, then A$9.50 a month for 12 months.

## How money flows
1. The seller pays the Plantr Collab subscription. The performance fee waits until attribution works.
2. The creator earns commission from the seller.
3. Plantr may later take a small cut of commission on Trendsettr deals, or stay on subscription pricing only.

## Positioning
- Sellers pay for clearer product signals and relevant creators, in any category.
- Creator plans are Membr, Curatr, and Trendsettr. Foundrs are the numbered pilot cohort.
- Plantr Collab does not hold inventory.
