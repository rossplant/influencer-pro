# Notion scratch pages — Plantr Collab

Notion is not the Plantr Collab database. `main.py` does not read or write Notion. Postgres (`DATABASE_URL`) is the system of record. When that variable is unset, creator profiles stay in process memory.

If you want a scratch page for field lists, copy the columns from the database, not from an old vertical schema:

- Products: title, category, subcategory, price, currency, tags, attributes, claims (each claim has a source).
- Creators: handle, interests, restrictions (generic tags), excluded_categories, excluded_claims, platforms, follower band.

Do not sync these pages from the API.
