"""Vector search over artifacts, always filtered by what the caller may open.

The local implementation (BruteForceIndex) pre-filters the corpus with
access.can_open, then ranks by cosine similarity of the stored vectors. It reads
vectors from the store on every query, so it is stateless and correct across any
number of instances; it is fine up to a few thousand artifacts.

Production options (same VectorIndex protocol, not implemented here):

  * Firestore vector search: store `embedding` as a Vector field, create a vector
    index, and run `collection.find_nearest(...)` three times with pre-filters the
    caller is entitled to (owner == me, shared_with array-contains me,
    visibility == 'shared'), then merge. Keeps a single datastore.
  * BigQuery VECTOR_SEARCH: stream metadata + vectors to a table (Firestore to
    BigQuery export or a write-through from the store), then
    `VECTOR_SEARCH(TABLE artifacts, 'embedding', (SELECT @q AS embedding), top_k => 50)`
    with a WHERE clause on owner / shared_with / visibility. Suits large corpora and
    joins with usage analytics.

Whatever the index, SearchService re-checks access.can_open on every hit before
returning it (defense in depth): an index bug can drop results, never leak them.
"""
from __future__ import annotations

from typing import Protocol

from .. import access


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalised


class VectorIndex(Protocol):
    def nearest(self, query_vector, principal, model_id: str, k: int) -> list[tuple[dict, float]]: ...


class BruteForceIndex:
    def __init__(self, store):
        self.store = store

    def nearest(self, query_vector, principal, model_id, k):
        scored = []
        for art in self.store.iter_all():
            if not access.can_open(principal, art):  # pre-filter: never score the unopenable
                continue
            if art.get("embedding_model") != model_id:
                continue
            scored.append((art, cosine(query_vector, art.get("embedding") or [])))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]
