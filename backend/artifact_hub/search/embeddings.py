"""Pluggable text-embedding providers.

  FakeEmbeddingProvider    deterministic, offline, dependency-free: hashed word and
                           character-trigram features. Good enough to exercise the
                           pipeline and to match paraphrases that share stems; it
                           has no real semantics. Dev and tests only.
  VertexEmbeddingProvider  Vertex AI text embeddings (text-embedding-005 or
                           gemini-embedding-001) through the google-genai SDK, with
                           the RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY task types.

Both return L2-normalised vectors, so cosine similarity is a dot product.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Protocol, Sequence


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str], task: str = "document") -> list[list[float]]:
        """task is 'document' (indexing) or 'query' (searching)."""


_STOP = set("""
a an and are as at be by for from has have how in is it its of on or that the this to was were what when where which
who why will with do does did per vs
le la les un une des du de d l et ou en au aux pour par sur dans est sont que qui quoi quel quelle quels quelles
ce cet cette ces se sa son ses leur leurs nos notre vos votre il elle ils elles on nous vous je tu y a ne pas plus
""".split())


def _normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", text)
    out = []
    for w in words:
        if w in _STOP or len(w) < 2:
            continue
        for suffix in ("ing", "es", "s"):  # crude stemming, enough for plurals
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


class FakeEmbeddingProvider:
    model_id = "fake-hash-v1"

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def _bucket(self, feature: str) -> tuple[int, float]:
        h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % self.dimensions
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for w in _normalize(text):
            i, s = self._bucket("w:" + w)
            vec[i] += 2.0 * s
            padded = f"#{w}#"
            for k in range(len(padded) - 2):
                i, s = self._bucket("t:" + padded[k:k + 3])
                vec[i] += 0.5 * s
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts, task="document"):
        return [self._one(t or "") for t in texts]


class VertexEmbeddingProvider:
    """Requires `pip install google-genai` and Application Default Credentials with
    `roles/aiplatform.user` on the project. Never used by the test suite."""

    def __init__(self, project: str, location: str, model: str = "text-embedding-005", dimensions: int = 768):
        from google import genai  # lazy: optional dependency

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model
        self.dimensions = dimensions
        self.model_id = f"vertex:{model}:{dimensions}"

    def embed(self, texts, task="document"):
        from google.genai import types

        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        resp = self._client.models.embed_content(
            model=self._model,
            contents=list(texts),
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions),
        )
        out = []
        for e in resp.embeddings:
            v = list(e.values)
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def build_embedder(settings):
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider == "vertex":
        return VertexEmbeddingProvider(settings.gcp_project, settings.vertex_location, settings.vertex_embedding_model)
    raise ValueError(f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")
