"""Search facade used by the REST API and the MCP `search_artifacts` tool."""
from __future__ import annotations

from .. import access
from .index import BruteForceIndex

RESULT_FIELDS = ("id", "title", "kind", "owner", "owner_name", "description", "source_question",
                 "tags", "data_as_of", "visibility", "current_version", "updated_at")


class SearchService:
    def __init__(self, store, embedder, index=None, min_score: float = 0.15):
        self.store = store
        self.embedder = embedder
        self.index = index or BruteForceIndex(store)
        self.min_score = min_score

    def search(self, principal, query: str, limit: int = 10) -> list[dict]:
        """Metadata of the artifacts most similar to `query` that `principal` may
        open. Never returns bodies."""
        query = (query or "").strip()
        if not query or principal is None:
            return []
        limit = max(1, min(int(limit), 50))
        qv = self.embedder.embed([query], task="query")[0]
        hits = self.index.nearest(qv, principal, self.embedder.model_id, k=limit * 3)
        out = []
        for art, score in hits:
            if score < self.min_score:
                continue
            if not access.can_open(principal, art):  # defense in depth
                continue
            item = {k: art.get(k) for k in RESULT_FIELDS}
            item["score"] = round(float(score), 4)
            item["access"] = access.access_level(principal, art)
            out.append(item)
            if len(out) >= limit:
                break
        return out
