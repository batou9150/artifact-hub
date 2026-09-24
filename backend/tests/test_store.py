import threading

import pytest

from artifact_hub.store.base import (
    MAX_BODY_BYTES, ArtifactError, ArtifactNotFound, ArtifactTooLarge, new_id,
)

OWNER = "owner@example.com"


def _new(store, **over):
    fields = dict(owner=OWNER, owner_name="Owner", title="T", kind="html", body="<p>v1</p>")
    fields.update(over)
    return store.create_artifact(**fields)


def test_create_starts_private_at_version_1(store):
    art = _new(store)
    assert art["current_version"] == 1
    assert art["visibility"] == "private"
    assert art["owner"] == OWNER
    assert art["shared_with"] == []
    assert store.get_version_body(art["id"]) == "<p>v1</p>"


def test_ids_are_192_bit_urlsafe_and_unique():
    ids = {new_id() for _ in range(2000)}
    assert len(ids) == 2000
    for i in list(ids)[:50]:
        assert len(i) == 32  # 24 random bytes, base64url without padding
        assert all(c.isalnum() or c in "-_" for c in i)


def test_metadata_cannot_override_server_fields(store):
    art = _new(store, metadata={"owner": "mallory@evil.test", "current_version": 99, "id": "x",
                                "viewers": ["a@b.c"], "description": "ok"})
    assert art["owner"] == OWNER
    assert art["current_version"] == 1
    assert art["viewers"] == []
    assert art["description"] == "ok"


def test_update_appends_versions_and_keeps_history(store):
    art = _new(store)
    store.update_artifact(art["id"], "<p>v2</p>", author=OWNER)
    art = store.update_artifact(art["id"], "<p>v3</p>", author=OWNER)
    assert art["current_version"] == 3
    assert [v["n"] for v in store.list_versions(art["id"])] == [1, 2, 3]
    assert store.get_version_body(art["id"], 1) == "<p>v1</p>"
    assert store.get_version_body(art["id"]) == "<p>v3</p>"


def test_revert_creates_a_new_version(store):
    art = _new(store)
    store.update_artifact(art["id"], "<p>v2</p>", author=OWNER)
    art = store.revert(art["id"], 1, author=OWNER)
    assert art["current_version"] == 3
    assert store.get_version_body(art["id"]) == "<p>v1</p>"
    assert len(store.list_versions(art["id"])) == 3


def test_revert_to_missing_version(store):
    art = _new(store)
    with pytest.raises(ArtifactNotFound):
        store.revert(art["id"], 7, author=OWNER)


def test_rename_does_not_create_a_version(store):
    art = _new(store)
    art = store.rename(art["id"], "  New title ")
    assert art["title"] == "New title"
    assert art["current_version"] == 1
    with pytest.raises(ArtifactError):
        store.rename(art["id"], "   ")


def test_body_cap_is_bytes_and_exact(store):
    _new(store, body="a" * MAX_BODY_BYTES)  # exactly at the cap: accepted
    with pytest.raises(ArtifactTooLarge):
        _new(store, body="a" * (MAX_BODY_BYTES + 1))
    with pytest.raises(ArtifactTooLarge):
        _new(store, body="é" * (MAX_BODY_BYTES // 2 + 1))  # 2 bytes per char


def test_over_cap_update_writes_nothing(store):
    art = _new(store)
    with pytest.raises(ArtifactTooLarge):
        store.update_artifact(art["id"], "a" * (MAX_BODY_BYTES + 1), author=OWNER)
    assert store.get_artifact(art["id"])["current_version"] == 1
    assert len(store.list_versions(art["id"])) == 1


@pytest.mark.parametrize("over", [{"kind": "pdf"}, {"visibility": "public"}, {"title": ""}, {"body": ""}])
def test_invalid_fields_rejected(store, over):
    with pytest.raises(ArtifactError):
        _new(store, **over)


def test_shared_with_normalized_and_bounded(store):
    art = _new(store)
    art = store.set_shared_with(art["id"], ["Bob@Example.com", "bob@example.com", OWNER, "c@example.com"])
    assert art["shared_with"] == ["bob@example.com", "c@example.com"]  # deduped, owner dropped
    with pytest.raises(ArtifactError):
        store.set_shared_with(art["id"], [f"u{i}@example.com" for i in range(101)])
    with pytest.raises(ArtifactError):
        store.set_shared_with(art["id"], ["not-an-email"])


def test_record_view_distinct_and_never_owner(store):
    art = _new(store)
    store.record_view(art["id"], OWNER)
    store.record_view(art["id"], "v@example.com")
    art = store.record_view(art["id"], "v@example.com")
    assert art["viewers"] == ["v@example.com"]
    assert art["view_count"] == 2
    assert art["updated_at"] == art["created_at"]  # an open is not an edit


def test_delete_removes_versions(store):
    art = _new(store)
    store.update_artifact(art["id"], "<p>v2</p>", author=OWNER)
    store.delete_artifact(art["id"])
    assert store.get_artifact(art["id"]) is None
    assert store.list_versions(art["id"]) == []


def test_queries(store):
    a = _new(store)
    b = _new(store, owner="other@example.com", visibility="shared")
    c = _new(store, owner="other@example.com", shared_with=[OWNER])
    assert {x["id"] for x in store.list_by_owner(OWNER)} == {a["id"]}
    assert {x["id"] for x in store.list_org_shared()} == {b["id"]}
    assert {x["id"] for x in store.list_shared_with(OWNER)} == {c["id"]}


def test_embedding_only_from_title_description_question(store):
    art = _new(store, body="<p>SECRETWORD</p>", metadata={"description": "d", "source_question": "q"})
    raw = store._get(art["id"])
    assert raw["embedding"] and raw["embedding_model"] == store.embedder.model_id
    before = raw["search_hash"]
    store.update_artifact(art["id"], "<p>other body</p>", author=OWNER)
    assert store._get(art["id"])["search_hash"] == before  # body change: no re-embed
    store.rename(art["id"], "Renamed")
    assert store._get(art["id"])["search_hash"] != before  # title change: re-embed


def test_concurrent_updates_never_lose_a_version(store):
    """Every successful update gets its own retained version; an update that loses
    the race fails cleanly (ArtifactConflict) and writes nothing."""
    from artifact_hub.store.base import ArtifactConflict

    art = _new(store)
    ok, conflicts, other = [], [], []

    def worker(i):
        try:
            store.update_artifact(art["id"], f"<p>{i}</p>", author=OWNER)
            ok.append(i)
        except ArtifactConflict:
            conflicts.append(i)
        except Exception as e:  # pragma: no cover
            other.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not other
    assert ok, "at least one concurrent update must succeed"
    n = 1 + len(ok)
    assert store.get_artifact(art["id"])["current_version"] == n
    assert [v["n"] for v in store.list_versions(art["id"])] == list(range(1, n + 1))
    bodies = {store.get_version_body(art["id"], k) for k in range(2, n + 1)}
    assert bodies == {f"<p>{i}</p>" for i in ok}


def test_people_directory(store):
    store.touch_user("Zoe.Martin@example.com", "Zoe Martin")
    store.touch_user("zack@example.com", "")
    assert [u["email"] for u in store.search_users("zo")] == ["zoe.martin@example.com"]
    assert [u["email"] for u in store.search_users("martin")] == ["zoe.martin@example.com"]
    assert store.search_users("z") == []  # 2-char floor
