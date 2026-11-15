# Matching — Plantr Collab

`POST /match` in `main.py` is still the placeholder scorer. It ranks the products sent in the request and, when a database is configured, stores a shortlist with `match_version='placeholder-0'`.

The placeholder is not a category model and does not call a language model. A later change replaces it with an explainable rules scorer. Weights and exclusions for that scorer will live in code, not in this note.
