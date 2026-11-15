# Plantr Pricing & Commercial Model

## Plan tiers in the API

`shops.plan_tier` defaults to `starter`. A pilot shop (`is_pilot`) defaults to `starter` as well, and its MRR is 0.

| plan_tier | Product enrichments | Match runs | Monthly price |
| --- | --- | --- | --- |
| starter, free | 50 (`products_enriched`) | 5 per calendar month (UTC) | $0 |
| growth | 500 | no monthly cap in this version | $49 |
| scale | unlimited | no monthly cap in this version | $149 |

When a shop is already at a cap, the API writes `usage_limit_hit`. A non-pilot shop is then blocked. A pilot shop is allowed to continue, and the event is still written.

### MRR

- A monthly price is the MRR.
- An annual price is divided by 12.
- Free and trial contribute 0.
- Starter, the entry plan_tier, contributes 0.

`monthly_mrr_snapshots` stores that number. Nothing writes the table from an endpoint yet.

## For Sellers (Merchants)

### Free Tier
- Up to 50 products synced
- Basic signal score
- Manual enrichment only
- Plantr branding on matches

### Growth — $49 / month
- Up to 500 products
- AI enrichment (signal score, talking points, common questions)
- Shopify sync (daily)
- Influencer match recommendations
- Priority support

### Scale — $149 / month
- Unlimited products
- Real-time Shopify sync
- Advanced AI enrichment + custom prompts
- Featured placement in influencer dashboards
- Dedicated success manager
- API access

**Performance Add-on (optional)**  
5% of attributed GMV from Plantr influencer matches (billed monthly).

---

## For Influencers

Creators are free at launch. A launch creator can keep a profile, see matches, and earn standard commission rates from sellers. There is no creator subscription at launch.

### Later options (not launch tiers)

#### Creator Pro — US$19 / month
- Unlimited matches
- Priority in seller recommendations
- Custom talking points generated for each product
- Early access to new products
- Performance analytics

#### Elite (invite only)
- Exclusive brand partnerships
- Higher commission share
- Co-branded campaigns

---

## How Money Flows
1. Seller pays Plantr subscription (or performance fee).
2. Influencer earns commission from the seller (TikTok Shop / affiliate).
3. Plantr can take a small cut of the commission (2–4%) on Elite deals or keep it pure SaaS.

## Positioning
- Sellers pay for better AI signals and access to relevant influencers.
- Creators are free at launch. Creator Pro (US$19/mo) and Elite are later options, not launch tiers.
- Plantr stays asset-light — no inventory.
