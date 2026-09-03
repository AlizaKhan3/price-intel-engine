from __future__ import annotations

"""
Tier B+ matching — semantic similarity via sentence embeddings.

Optional upgrade over fuzzy_text.py. Fuzzy string matching only catches
products described in similar WORDS. Embeddings catch products described
in similar MEANING — e.g. "abaya" vs "modest maxi dress" — which matters
a lot for Fashion, where different sellers describe near-identical items
very differently.

Not required for the MVP. Turn this on once:
  - you've validated the pipeline on Electronics/structured categories, and
  - fuzzy_text.py's false-positive rate on Fashion is too high for the
    review queue to keep up with.

Requires: `pip install sentence-transformers` (downloads a small
~90MB model from huggingface.co on first run — needs outbound internet
access from wherever this runs, which most hosts allow by default).

The import is deferred so the rest of the app works fine even if this
package isn't installed yet.
"""
from functools import lru_cache

import numpy as np


@lru_cache
def _get_model():
    from sentence_transformers import SentenceTransformer
    # all-MiniLM-L6-v2: small, fast, free, good enough for short product titles.
    return SentenceTransformer("all-MiniLM-L6-v2")


def embed_texts(texts: list[str]) -> np.ndarray:
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True)


def embedding_match_scores(query_title: str, candidate_titles: list[str]) -> list[float]:
    """
    Returns cosine-similarity-based scores (0-100) between the query
    title and each candidate title.
    """
    if not candidate_titles:
        return []

    vectors = embed_texts([query_title] + candidate_titles)
    query_vec, candidate_vecs = vectors[0], vectors[1:]

    # Vectors are normalized, so dot product == cosine similarity, in [-1, 1].
    similarities = candidate_vecs @ query_vec
    return [round(float(max(0.0, s)) * 100, 1) for s in similarities]
