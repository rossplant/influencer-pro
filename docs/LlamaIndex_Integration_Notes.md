# LlamaIndex notes — Plantr Collab

## What this sketch provides
- Product indexing with metadata (title, category, price, signal_score)
- Semantic retrieval endpoint-ready function
- Grounded talking-point helper
- Lightweight local embeddings (BAAI/bge-small)

## Future hook (not a current endpoint)

`POST /retrieve` does not exist. `main.py` is the entry point. This sketch is not wired in. When it is, the steps would be:
1. Add to requirements.txt:
   ```
   llama-index
   llama-index-embeddings-huggingface
   ```
2. Import the functions:
   ```python
   from plantr_llamaindex_sketch import index_products, retrieve_relevant_products
   ```
3. Call `index_products(enriched_list)` after Shopify sync or on startup.
4. Add a new endpoint:
   ```python
   @app.post("/retrieve")
   def retrieve(query: str):
       return {"results": retrieve_relevant_products(query)}
   ```

## Next upgrades (when ready)
- Persistent vector store (Qdrant or Chroma)
- Hybrid retrieval (keyword + vector)
- LLM-generated talking points using retrieved nodes as context
- Metadata filtering (price range, category, signal_score)

## Notes
- Index is in-memory for this sketch (rebuilds on restart).
- Embeddings download on first run.
- Works offline after the model is cached.
