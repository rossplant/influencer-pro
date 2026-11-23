# Changelog

## 0.22.0

Supplier notes and Listing Studio. Creatr tiers stay Creatr-only. Stripe stays sandbox only. This does not merge.

- Top seller is 20 or more confirmed sales through 5 or more Creatrs in the last 3 months, and Pays on time. It is a badge only.
- Suppliers have no tier screens. Header pills link to "Notes Creatrs may see" and a glossary, "What Creatr tiers mean".
- Listing Studio is priced per product: free in the pilot for up to 4 images and selling tips, then A$4.99 from 20 January 2027. Extra images are A$1.50. The image API stays off.

## 0.21.0

Creatr tiers, match allowances, and supplier badges. Stripe stays sandbox only. This does not merge.

- The Creatr ladder shows only how to reach the next tier, with one progress bar. The quality standard is one line for every Creatr, and a separate line for every supplier. It is not repeated on each tier.
- Two consecutive months below the quality standard (QUALITY_FAIL_MONTHS, default 2) return a Creatr to Contributr, or to Influencr until 20 April 2027 for an invited pilot participant, and remove early access, Pays on time, and Top seller. The first failed month sends an in-app warning and a transactional email when Resend is configured.
- An admin can mark a failure as not their fault (supplier or courier delay, platform error, or other with a note). That excludes the month or restores status, and writes who, when, and the reason. A Creatr or supplier can request a review from Home, which opens an admin queue item.

- Creatr ladder: Contributr, Influencr at 5, Trendsettr at 15, Headlinr at 40 confirmed refund-adjusted sales over a rolling 3 months, from 20 November 2026. Pilot invitees start at Influencr until 20 April 2027. Plans do not buy a tier. 48-hour early access is Trendsettr and Headlinr only. The plan-based first look is removed.
- The elite plan's name on screen is Curatr Pro. The id and Stripe lookup keys stay `elite`.
- Match allowances refresh monthly with no rollover. Membr 4 matches and 3 active collabs. Curatr 12 matches, 4 offers, 6 collabs. Curatr Pro sees every qualifying category match, 12 offers, and 12 collabs. Trendsettr adds 2 active collabs and Headlinr adds 4. Five attributed sales in a month add 3 matches.
- Suppliers do not earn tiers. Creatrs see a Creatr's tier as a selection signal. Suppliers can show "Pays on time" and "Top seller". Top seller is free and earned, and it does not change ranking.
- Non-Shopify suppliers are hidden from Creatrs. Pilot caps are 20 Shopify and 10 non-Shopify. The product cap is not applied during the pilot.
- Listing Studio is stubbed and off. No image API is called.
- Transactional email uses `RESEND_API_KEY` and `RESEND_FROM`, with no reply-to. It covers sign-up verification, password reset, and billing reminders 7 days and 2 days before the first charge and before the Growth step-up.

## 0.19.0

Seller onboarding and a curated catalogue for the pilot. Stripe stays sandbox only: live secret keys are still refused, and this change does not touch Stripe or Railway.

`shops.name` stays the account name entered on the invite. `shops.business_name` is the trading name. It is copied from the pilot application and can be edited. The shop also stores an optional ABN (same checksum as the apply form), an editable primary category, a public website (`store_url`), and the existing Shopify domain.

### Curated catalogue

- A product stores `url` (filled from `attributes.url`, which is still written), `why_chosen`, `value_to_creators`, and an optional `image_url`. There is no image upload.
- The seller form requires why the product was chosen, the value for creators, and a commission. Older products missing why or value show "Complete this product".
- Only a product with all three is offered on Find creators, and only then can a brief be stored for it.
- Starter and Growth no longer block new products at 5 and 50. Those numbers are still written on usage events as `product_limit`. Scale is unchanged: no product cap, 100 matches a month. Campaign and match caps still block, and pilots are still allowed through.
- `CATALOGUE_FEE_THRESHOLD` (default 10) is a warning only. Above it, the products page says extra products may attract a fee after the pilot. Nothing is blocked and nothing is charged. Crossing the threshold writes `catalogue_fee_threshold` once.

### CSV pick-to-import

- A subscribed seller can upload a products CSV (`title`, `url`, `price`, `category`, `image_url`, optional `commission`), up to 500 rows and 1MB, and download a template.
- The preview has nothing ticked. Ticked rows can be saved as drafts (`source=csv`) or finished one by one. Drafts are not used for matching until they are complete.

## 0.18.0

The app version stays 0.18.0, already set on the Shopify order sync branch. This entry is the source-platform and Spark Ads work. Stripe stays sandbox only: live secret keys are still refused, and this change does not touch Stripe or Railway.

### Source platforms

- `source_platform` is `tiktok`, `instagram`, `youtube`, `facebook`, `other`, or `unknown`.
- Each tracked link has per-platform Plantr URLs (`/r/<code>?s=tt|ig|yt|fb|ot`) and a direct seller URL (`?plantr=<code>&s=`). The untagged link still works and records `unknown`.
- A click stores `source_platform`. An order stores `source_platform` and `channel_kind` (`link`, `code`, or `ad`).
- A link sale uses the platform on the click that won the 7-day window.
- A seller can register optional extra codes for the same creator, each tagged to a platform (for example `MIA-TT`). A code sale uses that platform, or `unknown` with the note "code, platform not known".
- Crediting is unchanged: 7-day click window for `/r/` and `pc_ref` links, code beats link, one credit per order. The split does not change amounts owed.
- Sellers can read sales and amounts by creator and source platform for a month (`GET /seller/api/commission/source-report`) and on the statement CSV.

### Direct seller URLs and Spark Ads

- Any tracked link can be issued as the seller's own product URL with `plantr`, `s`, and optionally `c`. TikTok ads use that URL (`?plantr=<code>&s=tt&c=ad`). The Plantr redirect is only an alternative.
- Shopify order sync reads `plantr`, `s`, and `c` from `landing_site`, then `referring_site`. A matching `plantr` code is the click for that order: `c=ad` is TikTok `ad`, otherwise `s` sets the platform and the channel is `link`. The raw URL and buyer details are not stored.
- `collab_spark_ads` stores the code encrypted with `SHOPIFY_TOKEN_KEY`, the post URL, the seller ad destination, `authorised_until` (7–365 days), status (`active`, `expired`, `revoked`), an optional usage note, consent time, and revoke time.
- Only the collab's creator can add or revoke a code. Only that seller and that creator can see the decrypted code. Lists and logs never include it.
- Status becomes `expired` after `authorised_until`. Revoking hides the code and the destination. Past sales stay credited.
- Seller views show: "Boosted posts must keep the creator's ad disclosure. Plantr doesn't run or pay for ads."
