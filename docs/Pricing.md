# Plantr Collab pricing

Plantr is the company. Plantr Collab is the product: a brand–creator collaboration tool for any category. It is aimed at small-to-mid Shopify sellers and nano and micro creators. There is no starting vertical.

## Plan tiers in the API

`shops.plan_tier` defaults to `starter`. A pilot shop (`is_pilot`) defaults to `starter`, `plan_status` `free`, and MRR 0. Growth and Scale default to `plan_status` `active` when status is omitted. `trial` and `trialing` both count as MRR 0.

The product cap is the number of product rows in the shop's catalogue. Enriching a product that already exists does not use another slot.

| plan_tier | Catalogue | Campaigns | Match runs | Price, ex GST |
| --- | --- | --- | --- | --- |
| starter | 5 products | 1 | 3 per calendar month (UTC) | A$29 / month or A$290 / year |
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

**Subscriptions only.** Plantr charges subscriptions only, with no platform or transaction fee. This will be reviewed after March 2027. Commission is paid directly from seller to creator, and Plantr never handles that money.

## For creators

Internal tier values stay `member`, `curator`, and `elite`. The names on screen are Membr, Curatr, and Curatr Pro. Stripe lookup keys stay `creator_elite_month` and `creator_elite_year`.

| tier | Label | Price, ex GST | New matches / month | Curated offers | Active collabs |
| --- | --- | --- | --- | --- | --- |
| member | Membr | A$15 / month or A$150 / year | 4 | 0 | 3 |
| curator | Curatr | A$19 / month or A$190 / year | 12 | 4 | 6 |
| elite | Curatr Pro | A$49 / month or A$490 / year | Every qualifying match in the Creatr's categories | 12 | 12 |

Allowances refresh each calendar month. Unused matches do not roll over. Trendsettr adds 2 active collabs and Headlinr adds 4. Those are earned tiers, not plans. 5 attributed sales in a calendar month add 3 matches that month, on every plan. Curatr Pro does not say unlimited, and it does not include top placement in shortlists.

Earned Creatr tiers, from confirmed refund-adjusted sales over a rolling 3 months, counted from 20 November 2026: Contributr on joining, Influencr at 5, Trendsettr at 15, Headlinr at 40. Pilot invitees start at Influencr until 20 April 2027. A plan never buys a tier. 48-hour early access is Trendsettr and Headlinr only.

Suppliers do not earn those tiers. Creatrs see each Creatr's tier when a supplier is choosing. A supplier can show "Pays on time" and, separately, "Top seller". Top seller is free, earned, and does not change match order.

Every plan is paid. An account with no live subscription, and that is not in the current pilot, is unsubscribed. It can sign in and edit its profile, and it sees a subscribe prompt instead of creating products, campaigns, matches, or accepted offers.

Foundrs are the invite-only cohort. They have no charge for the pilot (20 November 2026 to 20 January 2027), then Curatr at A$9 a month ex GST for 12 months, then A$19. That price is an A$10 amount-off coupon (`foundr9_curatr_month_12mo`, restricted in Stripe to the Curatr product) with promo code FOUNDR9. The app applies FOUNDR9 only to monthly Curatr; Membr, Trendsettr and annual plans are never discounted. FOUNDING50 is retired and deactivated. The route stays `/founding-curators`.

The 5-sale waiver is Curatr only. A Curatr month with 5 or more attributed sales credits the customer balance with the next invoice amount, so the next invoice is A$0. It stacks with FOUNDR9: the FOUNDR9 discount stays on the subscription and the waiver covers the remaining A$9.

## Pilot

Both cohorts run from 20 November 2026 to 20 January 2027. The free period ends at 12:00 Sydney time on 20 January 2027, which is when the first invoice is raised and charged.

- Suppliers: Growth has no charge during the pilot, then A$39.50 a month ex GST (PILOTGROWTH, 50% off) for 3 months, then A$79.
- Foundrs: no charge during the pilot, then Curatr at A$9 a month ex GST (FOUNDR9) for 12 months, then A$19.

Codes are applied automatically for eligible accounts; nobody types them, and Stripe Checkout does not accept typed codes. A pilot subscriber who checks out during the pilot gets a Stripe subscription schedule: the free period, then 3 (or 12) monthly invoices with the code, then full price. This stops the code's months being used up during the free period. After the pilot ends, the code is attached directly at Checkout.

Checkout shows the disclosure from `09_Signup_Acceptance_Wording` section 10 next to Subscribe and on the Stripe page, for example "No charge today. Your first charge of A$39.50 (ex GST) will be on 20 January 2027 ...".

## Reminders

`reminders.py` sends, through the Resend adapter in `mailer.py`:

- the pilot-ending email to every signed-up pilot member from 5 January 2027 10:00 Sydney time (by 6 January 2027),
- a first-charge email 7 days before the first charge after any free period,
- an annual renewal email 14 days before renewal,
- a discount-ending email 14 days before PILOTGROWTH, FOUNDR9 or another discount ends.

`reminder_sends` is the idempotent send log. The job runs hourly inside the app (`REMINDERS_LOOP=off` turns it off) and can be run by hand with `POST /admin/api/reminders/run`. Until `RESEND_API_KEY` and `EMAIL_FROM` are set, nothing is sent, a warning is logged, and due reminders are retried on later runs until their date passes.

## How money flows
1. The seller pays the Plantr Collab subscription. Plantr charges subscriptions only, with no platform or transaction fee. This will be reviewed after March 2027.
2. Commission is paid directly from the seller to the creator. Plantr never handles that money.

## Positioning
- Sellers pay for clearer product signals and relevant creators, in any category.
- Creator plans are Membr, Curatr, and Trendsettr. Foundrs are the numbered pilot cohort.
- Plantr Collab does not hold inventory.
