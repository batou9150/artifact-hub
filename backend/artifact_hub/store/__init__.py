from .base import (  # noqa: F401
    MAX_BODY_BYTES, MAX_SHARED_WITH, ArtifactConflict, ArtifactError, ArtifactNotFound, ArtifactStore,
    ArtifactTooLarge,
)
from .memory import MemoryArtifactStore  # noqa: F401


def build_store(settings, embedder=None) -> ArtifactStore:
    if settings.store_backend == "firestore":
        from .firestore import FirestoreArtifactStore
        return FirestoreArtifactStore.from_settings(settings, embedder)
    if settings.store_backend == "memory":
        return MemoryArtifactStore(embedder)
    raise ValueError(f"unknown STORE_BACKEND {settings.store_backend!r}")
