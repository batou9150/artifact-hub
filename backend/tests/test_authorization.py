"""Authorization matrix across REST, sandbox and search."""
import pytest

from conftest import ALICE, BOB, CAROL, DAVE, PUBLISHERS, auth, create, make_settings, sandbox_path


def test_private_artifact_is_404_for_non_owner_everywhere(client):
    a = create(client, who=ALICE)
    for path in (f"/api/artifacts/{a['id']}", f"/api/artifacts/{a['id']}/body",
                 f"/api/artifacts/{a['id']}/versions"):
        r = client.get(path, headers=auth(BOB))
        assert r.status_code == 404, path
    assert client.post(f"/api/artifacts/{a['id']}/views", headers=auth(BOB)).status_code == 404
    assert client.patch(f"/api/artifacts/{a['id']}", json={"title": "x"}, headers=auth(BOB)).status_code == 404
    assert client.delete(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 404
    # indistinguishable from an absent id
    absent = client.get("/api/artifacts/doesnotexist", headers=auth(BOB))
    private = client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB))
    assert absent.status_code == private.status_code == 404
    assert absent.json() == private.json()


def test_shared_with_grants_open_but_not_manage(client):
    a = create(client, who=ALICE)
    r = client.put(f"/api/artifacts/{a['id']}/sharing", json={"shared_with": [BOB]}, headers=auth(ALICE))
    assert r.status_code == 200 and r.json()["artifact"]["shared_with"] == [BOB]
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(DAVE)).status_code == 404
    # grantee: 403 on writes (existence already known to them)
    assert client.patch(f"/api/artifacts/{a['id']}", json={"body": "x"}, headers=auth(BOB)).status_code == 403
    assert client.put(f"/api/artifacts/{a['id']}/sharing", json={"shared_with": [DAVE]},
                      headers=auth(BOB)).status_code == 403
    assert client.delete(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 403
    assert client.get(f"/api/artifacts/{a['id']}/versions", headers=auth(BOB)).status_code == 403


def test_shared_with_me_lists_invited_and_org(client):
    invited = create(client, who=ALICE, title="Invited", shared_with=[BOB])
    org = create(client, who=ALICE, title="Org", visibility="shared")
    create(client, who=ALICE, title="Private")
    create(client, who=BOB, title="Bob own", visibility="private")
    items = client.get("/api/artifacts/shared", headers=auth(BOB)).json()["artifacts"]
    assert {i["id"]: i["access"] for i in items} == {invited["id"]: "invited", org["id"]: "organisation"}


def test_org_wide_sharing_restricted_to_publisher_group(client):
    # Bob is not in the publisher group
    r = client.post("/api/artifacts", headers=auth(BOB), json={"title": "T", "kind": "text", "body": "x",
                                                                "visibility": "shared"})
    assert r.status_code == 403 and PUBLISHERS in r.json()["detail"]
    b = create(client, who=BOB)
    r = client.put(f"/api/artifacts/{b['id']}/sharing", json={"visibility": "shared"}, headers=auth(BOB))
    assert r.status_code == 403
    # Bob can still share with specific people
    r = client.put(f"/api/artifacts/{b['id']}/sharing", json={"shared_with": [DAVE]}, headers=auth(BOB))
    assert r.status_code == 200
    # Alice (publisher) and Carol (admin implies publisher) can publish org-wide
    a = create(client, who=ALICE)
    assert client.put(f"/api/artifacts/{a['id']}/sharing", json={"visibility": "shared"},
                      headers=auth(ALICE)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(DAVE)).status_code == 200
    c = create(client, who=CAROL)
    assert client.put(f"/api/artifacts/{c['id']}/sharing", json={"visibility": "shared"},
                      headers=auth(CAROL)).status_code == 200


def test_any_owner_can_make_private_again(client):
    a = create(client, who=ALICE, visibility="shared")
    r = client.put(f"/api/artifacts/{a['id']}/sharing", json={"visibility": "private"}, headers=auth(ALICE))
    assert r.status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 404


def test_sensitive_artifact_cannot_go_org_wide(client):
    a = create(client, who=ALICE, sensitive=True)
    r = client.put(f"/api/artifacts/{a['id']}/sharing", json={"visibility": "shared"}, headers=auth(ALICE))
    assert r.status_code == 403 and "sensitive" in r.json()["detail"]
    s = create(client, who=ALICE, visibility="shared")
    r = client.patch(f"/api/artifacts/{s['id']}", json={"sensitive": True}, headers=auth(ALICE))
    assert r.json()["artifact"]["visibility"] == "private"  # flagging withdraws the org link
    assert r.json()["artifact"]["warnings"]


def test_invitees_restricted_to_allowed_domains(store):
    from fastapi.testclient import TestClient

    from artifact_hub.api import create_app
    _, root = create_app(make_settings(allowed_email_domains=("example.com",)), store=store,
                         embedder=store.embedder)
    with TestClient(root) as c:
        a = create(c, who=ALICE)
        r = c.put(f"/api/artifacts/{a['id']}/sharing", json={"shared_with": ["x@outside.test"]},
                  headers=auth(ALICE))
        assert r.status_code == 403
        assert c.get("/api/me", headers=auth("eve@outside.test")).status_code == 401


def test_prior_versions_owner_only(client):
    a = create(client, who=ALICE, shared_with=[BOB], body="<p>v1</p>")
    client.patch(f"/api/artifacts/{a['id']}", json={"body": "<p>v2</p>"}, headers=auth(ALICE))
    assert client.get(f"/api/artifacts/{a['id']}/body?version=1", headers=auth(BOB)).status_code == 404
    assert client.get(f"/api/artifacts/{a['id']}/body?version=1", headers=auth(ALICE)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}/versions/1/render", headers=auth(BOB)).status_code == 404
    r = client.get(f"/api/artifacts/{a['id']}/versions/1/render", headers=auth(ALICE))
    assert client.get(sandbox_path(r.json()["render_url"])).text == "<p>v1</p>"


def test_revoked_grant_invalidates_outstanding_ticket(client):
    a = create(client, who=ALICE, shared_with=[BOB])
    url = client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).json()["render_url"]
    assert client.get(sandbox_path(url)).status_code == 200
    client.put(f"/api/artifacts/{a['id']}/sharing", json={"shared_with": []}, headers=auth(ALICE))
    assert client.get(sandbox_path(url)).status_code == 404


@pytest.mark.parametrize("query", ["margin by store", "store margin"])
def test_search_never_leaks_unopenable_artifacts(client, query):
    create(client, who=BOB, title="Margin by store", description="gross margin per store")      # private
    create(client, who=BOB, title="Store margin trend", shared_with=[DAVE])                     # not Alice
    visible_invited = create(client, who=BOB, title="Margin by store (invited)", shared_with=[ALICE])
    visible_org = create(client, who=CAROL, title="Store margin overview", visibility="shared")
    mine = create(client, who=ALICE, title="My store margin draft")
    ids = {r["id"] for r in client.get(f"/api/artifacts/search?q={query}", headers=auth(ALICE)).json()["results"]}
    assert ids == {visible_invited["id"], visible_org["id"], mine["id"]}
    dave_ids = {r["id"] for r in client.get(f"/api/artifacts/search?q={query}", headers=auth(DAVE)).json()["results"]}
    assert visible_invited["id"] not in dave_ids and mine["id"] not in dave_ids
