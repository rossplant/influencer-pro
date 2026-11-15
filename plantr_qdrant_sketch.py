# plantr-collab — Qdrant Semantic Search Sketch
# Production-oriented vector search for product retrieval
#
# Install:
#   pip install qdrant-client sentence-transformers
#
# Run Qdrant locally (Docker):
#   docker run -p 6333:6333 qdrant/qdrant
#
# Sketch only. POST /semantic-search does not exist. main.py is the entry point.

import os
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer
import uuid

# Config
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "plantr_products"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
VECTOR_SIZE = 384

# Clients (load once)
qdrant = QdrantClient(url=QDRANT_URL)
embed_model = SentenceTransformer(EMBED_MODEL_NAME)

def ensure_collection():
    """Create the collection if it does not exist."""
    if not qdrant.collection_exists(COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"Created collection: {COLLECTION_NAME}")

def product_to_text(product: Dict[str, Any]) -> str:
    parts = [
        product.get("title", ""),
        product.get("description_short", ""),
        product.get("description_long", ""),
        " ".join(product.get("key_features", [])),
        " ".join(product.get("target_audience", [])),
        " ".join(product.get("occasions", [])),
        product.get("care_instructions") or "",
    ]
    return " ".join([p for p in parts if p])

def upsert_products(products: List[Dict[str, Any]]):
    """Embed and upsert products into Qdrant."""
    ensure_collection()
    points = []
    for p in products:
        text = product_to_text(p)
        vector = embed_model.encode(text).tolist()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, p.get("title", "") + str(p.get("price", ""))))
        payload = {
            "title": p.get("title"),
            "category": p.get("category"),
            "price": p.get("price"),
            "signal_score": p.get("signal_score", 0),
            "brand": p.get("brand"),
            "text": text[:500],
        }
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))
    if points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
        print(f"Upserted {len(points)} products")

def semantic_search(
    query: str,
    top_k: int = 5,
    category: Optional[str] = None,
    min_signal: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Semantic search with optional metadata filters."""
    ensure_collection()
    query_vector = embed_model.encode(query).tolist()

    query_filter = None
    conditions = []
    if category:
        conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
    if min_signal is not None:
        conditions.append(FieldCondition(key="signal_score", range={"gte": min_signal}))
    if conditions:
        query_filter = Filter(must=conditions)

    hits = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
        query_filter=query_filter,
    )

    results = []
    for hit in hits:
        results.append({
            "score": round(hit.score, 3),
            "title": hit.payload.get("title"),
            "category": hit.payload.get("category"),
            "price": hit.payload.get("price"),
            "signal_score": hit.payload.get("signal_score"),
            "text": hit.payload.get("text"),
        })
    return results

# Example usage
if __name__ == "__main__":
    sample = [
        {
            "title": "Snake Plant – Low Light Indoor",
            "description_long": "Hardy houseplant that tolerates low light and irregular watering. Great for beginners.",
            "key_features": ["low light", "easy care"],
            "target_audience": ["beginners", "apartment dwellers"],
            "occasions": ["home decor", "gift"],
            "category": "Houseplants",
            "price": 24.99,
            "signal_score": 82,
        }
    ]
    upsert_products(sample)
    print(semantic_search("easy care low light plants for apartments"))
