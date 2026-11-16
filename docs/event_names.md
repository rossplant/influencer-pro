# Plantr Collab event names

Plantr is the company. Plantr Collab is the product. Events are append-only. `events.emit(name, entity, actor, **props)` inserts the row. `actor` is `system`, `operator`, or `api`. The same names are used for hand-run operator actions with `actor="operator"`.

Shop events copy `plan_tier` and `plan_status` into `properties`. These counters are columns, not JSON, and are stamped on every shop event:

| Column | Meaning |
| --- | --- |
| `products_count` | Product rows in the shop catalogue |
| `product_limit` | Catalogue cap for the plan, or null when unlimited |
| `matches_count_period` | `match_run` events in the current UTC month, including this event when it is a match |
| `match_limit` | Monthly match cap, or null when unlimited |
| `enrichments_count_period` | `product_enriched` events in the current UTC month |
| `briefs_count_period` | `brief_sent` events in the current UTC month |
| `billing_period_start` | UTC start of the current calendar month |
| `limit_hit` | True on `usage_limit_hit`, or when a finite cap is already met |

`first_*` timestamps are set only while null. `last_*` timestamps are updated on every matching event.

| Event | Trigger | What it updates |
| --- | --- | --- |
| `shop_signed_up` | `POST /shops` | Shop snapshot only. No usage counter. |
| `creator_onboarded` | `POST /onboard-influencer` | `creators.profile_completed_at` the first time, and only when the profile is complete. Not a shop event. |
| `profile_completed` | Emitted directly for the same milestone | `creators.profile_completed_at` the first time, and only when the profile is complete. |

A profile is complete when it has a handle, at least one `active_categories` slug, at least one preferred promotion style, and at least one platform. Desired categories and performance metrics are optional, so they do not gate this timestamp.
| `product_enriched` | `POST /enrich` when a shop or product id resolves | `products_enriched` +1, `first_enrich_at` the first time. Re-enriching an existing product still counts as an enrichment and does not add a catalogue row. A brief is stored only when the product has why, value, and a commission. |
| `match_run` | `POST /match` after a shortlist is stored | `matches_run` +1, `first_match_at` the first time, `last_match_at` every time. Starter cap is 5 per UTC month. |
| `brief_sent` | `POST /briefs/{id}/sent`, including `actor="operator"` | `briefs_generated` +1, `first_brief_at` the first time, `last_campaign_at` every time, `briefs.sent_at` the first time. Enrichment does not emit this. |
| `usage_limit_hit` | A shop is already at a campaign or match cap | No usage counter. `limit_hit` is true. Pilots are allowed through. Other shops receive HTTP 402. Product count is not a hard cap. |
| `catalogue_fee_threshold` | A new product takes the catalogue from at or under `CATALOGUE_FEE_THRESHOLD` to above it | No usage counter and no charge. Logged once per crossing. |
| `shopify_connected` | Emitted when a shop's Shopify connection is recorded | `shopify_connected_at` the first time. |
| `attributed_sale` | Emitted when an attributed order is recorded | `first_attributed_sale_at` the first time. |
| `payment_received` | Emitted when the first paid subscription is recorded | `first_paid_at` the first time. |
| `subscribe` | Stripe webhook, first paid invoice only | `first_paid_at` the first time. Server fan-out only. |
| `complete_registration` | Invite join creates the seller or creator account | Server-only. The browser must not fire this event. |
| `consent_revoked` | A signed-in seller or creator turns marketing consent off | Internal only. Updates `marketing_consent` on the account. |
| `match_accepted` | Emitted for a creator or shortlist item | `creators.first_match_accepted_at` the first time. On a shortlist item, `accepted_at` the first time. |
| `match_declined` | Emitted for a shortlist item | `declined_at` the first time, and `decline_reason` when passed and still empty. |
| `creator_posted` | Emitted when a creator's post is recorded | `creators.first_post_at` the first time. |
| `link_clicked` | `GET /r/{code}` | No plan counter. Properties contain the code, creator, and product. |
| `admin_login_succeeded` | `POST /admin/login` when the password matches | No shop counters. Properties contain the email, never the password. |
| `admin_login_failed` | `POST /admin/login` when the password is wrong or the attempt is rate-limited | No shop counters. Properties contain the email and a reason, never the password. |

`request_id` on an event is the id of the HTTP request that emitted it, when there is one. It matches the JSON log line and the `api_requests` row. Direct `emit()` calls outside a request leave it null.
