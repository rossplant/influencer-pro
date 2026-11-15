# plantr-collab — Minimal LlamaIndex RAG Sketch
# Adds product knowledge retrieval + grounded talking-point generation
#
# Install:
#   pip install llama-index llama-index-embeddings-huggingface
#
# Sketch only. POST /retrieve does not exist. main.py is the entry point.
# or run this file standalone for testing.

from typing import List, Dict, Any, Optional
from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core.retrievers import VectorIndexRetriever

# Lightweight local embeddings (same family as previous hybrid matcher)
Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

# In-memory index (replace with persistent vector store later)
_product_index: Optional[VectorStoreIndex] = None
_product_docs: List[Document] = []

def build_product_document(product: Dict[str, Any]) -> Document:
    """Turn a product dict into a LlamaIndex Document with metadata."""
    text_parts = [
        product.get("title", ""),
        product.get("description_short", ""),
        product.get("description_long", ""),
        "Features: " + ", ".join(product.get("key_features", [])),
        "Audience: " + ", ".join(product.get("target_audience", [])),
        "Occasions: " + ", ".join(product.get("occasions", [])),
        "Care: " + (product.get("care_instructions") or ""),
    ]
    text = "\n".join([p for p in text_parts if p])

    metadata = {
        "title": product.get("title"),
        "category": product.get("category"),
        "price": product.get("price"),
        "signal_score": product.get("signal_score", 0),
        "brand": product.get("brand"),
    }
    return Document(text=text, metadata=metadata)

def index_products(products: List[Dict[str, Any]]) -> None:
    """Build or rebuild the in-memory product index."""
    global _product_index, _product_docs
    _product_docs = [build_product_document(p) for p in products]
    _product_index = VectorStoreIndex.from_documents(_product_docs)
    print(f"Indexed {len(_product_docs)} products")

def retrieve_relevant_products(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Semantic retrieval over the product catalog."""
    if _product_index is None:
        return []
    retriever = VectorIndexRetriever(
        index=_product_index,
        similarity_top_k=top_k,
    )
    nodes = retriever.retrieve(query)
    results = []
    for node in nodes:
        results.append({
            "score": round(node.score or 0.0, 3),
            "text": node.text[:300] + "...",
            "metadata": node.metadata,
        })
    return results

def generate_grounded_talking_points(product: Dict[str, Any], influencer_interests: List[str]) -> List[str]:
    """
    Simple grounded talking points using retrieved context.
    In production you would pass the retrieved nodes to an LLM.
    """
    title = product.get("title", "this product")
    interests = ", ".join(influencer_interests) or "general plant care"
    return [
        f"This {title} is a strong match for creators focused on {interests}.",
        f"Key care note: {product.get('care_instructions') or 'low maintenance'}.",
        "Highlight the practical benefits for apartment or busy lifestyles.",
        "Pair with a quick before/after or care tip in your video.",
    ]

# Example usage (standalone test)
if __name__ == "__main__":
    sample_products = [
        {
            "title": "Snake Plant – Low Light Indoor",
            "description_short": "Hardy houseplant",
            "description_long": "Perfect for beginners. Tolerates low light and irregular watering.",
            "key_features": ["low light", "easy care", "air purifying"],
            "target_audience": ["beginners", "apartment dwellers"],
            "occasions": ["home decor", "gift"],
            "care_instructions": "Water every 2-3 weeks",
            "category": "Houseplants",
            "price": 24.99,
            "signal_score": 82,
        },
        {
            "title": "Pothos Trailing Plant",
            "description_long": "Fast-growing vining plant. Great for shelves and hanging baskets.",
            "key_features": ["trailing", "easy care"],
            "target_audience": ["beginners"],
            "occasions": ["home decor"],
            "category": "Houseplants",
            "price": 18.50,
            "signal_score": 75,
        },
    ]

    index_products(sample_products)
    results = retrieve_relevant_products("easy care low light plants for apartments")
    print("\nRetrieval results:")
    for r in results:
        print(f"  Score {r['score']} — {r['metadata'].get('title')}")
