# Changelog

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
