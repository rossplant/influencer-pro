# Qdrant Integration Notes — plantr-collab

## Why Qdrant
- Fast vector search with strong metadata filtering
- Good production path (Docker or managed cloud)
- Works well as the retrieval layer before your hybrid regex re-ranking

## Setup
1. Run Qdrant:
   ```bash
   docker run -p 6333:6333 qdrant/qdrant
   ```
2. Add to requirements.txt:
   ```
   qdrant-client
   sentence-transformers
   ```
3. Set env (optional):
   ```
   QDRANT_URL=http://localhost:6333
   ```

## Future hook (not a current endpoint)

`POST /semantic-search` does not exist. `main.py` is the entry point. This sketch is not wired in.
```python
from plantr_qdrant_sketch import upsert_products, semantic_search

# After Shopify sync or enrichment:
upsert_products(enriched_products)

# New endpoint
@app.post("/semantic-search")
def search(query: str, top_k: int = 5, category: str = None):
    return {"results": semantic_search(query, top_k=top_k, category=category)}
```

## Recommended flow
1. Semantic search returns top candidates (Qdrant).
2. Re-rank those candidates with the existing hybrid regex + embedding score.
3. Return the final top matches to the influencer dashboard.

## Next upgrades
- Persistent storage volume for Docker
- Hybrid search (Qdrant sparse + dense)
- Payload indexing for faster filters
- Batch upsert after full Shopify sync
