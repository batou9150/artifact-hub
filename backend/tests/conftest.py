"""Shared fixtures. Store-level and API-level tests run against the in-memory
backend always, and against the Firestore emulator too when FIRESTORE_EMULATOR_HOST
is set (each test gets its own emulator project, so tests are isolated):

    gcloud emulators firestore start --host-port=127.0.0.1:8681 &
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8681 pytest
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from artifact_hub.api import create_app
from artifact_hub.config import Settings
from artifact_hub.search.embeddings import FakeEmbeddingProvider
from artifact_hub.store.memory import MemoryArtifactStore

BASE = "http://testserver"
PUBLISHERS = "artifact-publishers"
ADMINS = "artifact-admins"


def make_settings(**over):
    values = dict(public_base_url=BASE, app_origin=BASE, cors_origins=(), environment="test",
                  auth_mode="dev", search_min_score=0.15)
    values.update(over)
    return Settings(**values)


def _backends():
    backends = ["memory"]
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        backends.append("firestore")
    return backends


@pytest.fixture(params=_backends())
def store(request):
    embedder = FakeEmbeddingProvider()
    if request.param == "memory":
        return MemoryArtifactStore(embedder)
    from google.cloud import firestore

    from artifact_hub.store.firestore import FirestoreArtifactStore
    return FirestoreArtifactStore(firestore.Client(project=f"demo-hub-{uuid.uuid4().hex[:10]}"), embedder)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def client(store, settings):
    _, root = create_app(settings, store=store, embedder=store.embedder)
    with TestClient(root) as c:
        yield c


def auth(email, *groups):
    token = f"dev:{email}" + (f";groups={','.join(groups)}" if groups else "")
    return {"Authorization": f"Bearer {token}"}


ALICE = "alice@example.com"   # publisher (dev directory)
BOB = "bob@example.com"       # plain member
CAROL = "carol@example.com"   # admin
DAVE = "dave@example.com"     # plain member


def create(client, who=ALICE, **fields):
    payload = {"title": "Weekly sales", "kind": "html", "body": "<p>hello</p>"}
    payload.update(fields)
    r = client.post("/api/artifacts", json=payload, headers=auth(who))
    assert r.status_code == 201, r.text
    return r.json()["artifact"]


def sandbox_path(render_url: str) -> str:
    return render_url.replace(BASE, "")
