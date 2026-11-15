# Matching — Plantr Collab

`POST /match` uses `scoring.py` (`match_version='rules-v1'`). It does not call a language model.

The score is 0–100. Fit covers category (active categories score full, desired categories half), tags and attributes, price band, platform, and audience. Expected performance is reach, estimated sales (`reach × engagement × conversion`), and estimated revenue (`estimated sales × price`), plus estimated seller commission (`estimated revenue × commission rate`). Missing reach, engagement, or conversion uses the defaults in `scoring.DEFAULTS` and the reasons say they are estimates. `optimise_for` selects the weights: `balanced`, `reach`, `sales`, or `revenue`. An underserved shop adds `underserved_boost` points. Hard exclusions remove a creator and explain why.
