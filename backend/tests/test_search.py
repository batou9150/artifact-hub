from artifact_hub.auth.identity import Principal
from artifact_hub.search.embeddings import FakeEmbeddingProvider
from artifact_hub.search.index import cosine
from artifact_hub.search.service import SearchService
from artifact_hub.store.memory import MemoryArtifactStore

ME = Principal(email="me@example.com")


def _svc():
    store = MemoryArtifactStore(FakeEmbeddingProvider())
    return store, SearchService(store, store.embedder, min_score=0.15)


def test_fake_embeddings_are_deterministic_and_normalised():
    e = FakeEmbeddingProvider()
    a, b = e.embed(["Weekly sales by region"])[0], e.embed(["Weekly sales by region"])[0]
    assert a == b
    assert abs(cosine(a, a) - 1.0) < 1e-9


def test_paraphrase_ranks_first():
    store, svc = _svc()
    target = store.create_artifact(owner=ME.email, title="Revenue per store, last 4 weeks", kind="text", body="x",
                                   metadata={"source_question": "What revenue did each store make recently?"})
    store.create_artifact(owner=ME.email, title="Headcount by department", kind="text", body="x")
    store.create_artifact(owner=ME.email, title="Return rate of online orders", kind="text", body="x")
    hits = svc.search(ME, "which stores generated the most revenue over the last weeks")
    assert hits[0]["id"] == target["id"]


def test_body_is_not_searchable():
    store, svc = _svc()
    store.create_artifact(owner=ME.email, title="Untitled chart", kind="html", body="<p>zanzibar quokka</p>")
    assert svc.search(ME, "zanzibar quokka") == []


def test_limit_and_empty_query():
    store, svc = _svc()
    for i in range(5):
        store.create_artifact(owner=ME.email, title=f"Sales report {i}", kind="text", body="x")
    assert len(svc.search(ME, "sales report", limit=2)) == 2
    assert svc.search(ME, "  ") == []
    assert svc.search(None, "sales") == []


def test_results_carry_access_level_and_no_vectors():
    store, svc = _svc()
    store.create_artifact(owner="o@example.com", title="Sales report", kind="text", body="x", visibility="shared")
    hit = svc.search(ME, "sales report")[0]
    assert hit["access"] == "organisation"
    assert "embedding" not in hit and "body" not in hit
