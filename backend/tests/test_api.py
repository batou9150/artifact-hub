from artifact_hub.store.base import MAX_BODY_BYTES
from conftest import ALICE, BOB, CAROL, auth, create


def test_requires_authentication(client):
    assert client.get("/api/artifacts/mine").status_code == 401
    assert client.get("/api/artifacts/mine", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/api/artifacts/mine")
    assert r.headers["www-authenticate"] == "Bearer"


def test_config_is_public_and_flags_dev_mode(client):
    cfg = client.get("/api/config").json()
    assert cfg["auth_mode"] == "dev"
    assert any(u["email"] == ALICE for u in cfg["dev_users"])
    assert cfg["max_body_bytes"] == MAX_BODY_BYTES


def test_me_reports_groups(client):
    me = client.get("/api/me", headers=auth(ALICE)).json()
    assert me == {"email": ALICE, "name": "Alice Analyst", "is_publisher": True, "is_admin": False,
                  "auth_mode": "dev"}
    assert client.get("/api/me", headers=auth(BOB)).json()["is_publisher"] is False


def test_create_attributes_to_caller_and_ignores_client_owner(client):
    r = client.post("/api/artifacts", headers=auth(BOB),
                    json={"title": "T", "kind": "text", "body": "x", "owner": ALICE, "current_version": 9})
    art = r.json()["artifact"]
    assert art["owner"] == BOB
    assert art["current_version"] == 1
    assert art["url"].endswith(f"/artifacts/{art['id']}")
    assert "embedding" not in art


def test_list_mine_and_get(client):
    a = create(client, title="Mine")
    create(client, who=BOB, title="Not mine")
    mine = client.get("/api/artifacts/mine", headers=auth(ALICE)).json()["artifacts"]
    assert [x["id"] for x in mine] == [a["id"]]
    got = client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()
    assert got["artifact"]["title"] == "Mine"
    assert got["render_url"].startswith(f"http://testserver/a/{a['id']}?t=")


def test_edit_creates_version_rename_does_not(client):
    a = create(client)
    h = auth(ALICE)
    art = client.patch(f"/api/artifacts/{a['id']}", json={"body": "<p>v2</p>"}, headers=h).json()["artifact"]
    assert art["current_version"] == 2
    art = client.patch(f"/api/artifacts/{a['id']}", json={"title": "Renamed"}, headers=h).json()["artifact"]
    assert art["current_version"] == 2 and art["title"] == "Renamed"
    body = client.get(f"/api/artifacts/{a['id']}/body", headers=h).json()
    assert body == {"body": "<p>v2</p>", "kind": "html", "version": 2}
    assert client.patch(f"/api/artifacts/{a['id']}", json={}, headers=h).status_code == 400


def test_metadata_update(client):
    a = create(client)
    art = client.patch(f"/api/artifacts/{a['id']}", headers=auth(ALICE),
                       json={"description": "Revenue by store", "tags": ["Sales", "sales", "weekly"],
                             "source_question": "What were sales last week?"}).json()["artifact"]
    assert art["tags"] == ["sales", "weekly"]
    assert art["current_version"] == 1


def test_versions_and_revert(client):
    a = create(client, body="<p>v1</p>")
    h = auth(ALICE)
    client.patch(f"/api/artifacts/{a['id']}", json={"body": "<p>v2</p>"}, headers=h)
    vs = client.get(f"/api/artifacts/{a['id']}/versions", headers=h).json()["versions"]
    assert [v["n"] for v in vs] == [1, 2]
    assert vs[0]["author"] == ALICE
    art = client.post(f"/api/artifacts/{a['id']}/revert", json={"version": 1}, headers=h).json()["artifact"]
    assert art["current_version"] == 3
    assert client.get(f"/api/artifacts/{a['id']}/body", headers=h).json()["body"] == "<p>v1</p>"
    assert client.post(f"/api/artifacts/{a['id']}/revert", json={"version": 42}, headers=h).status_code == 400


def test_over_cap_rejected_with_reason_and_nothing_written(client):
    r = client.post("/api/artifacts", headers=auth(ALICE),
                    json={"title": "Big", "kind": "text", "body": "a" * (MAX_BODY_BYTES + 1)})
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]
    assert client.get("/api/artifacts/mine", headers=auth(ALICE)).json()["artifacts"] == []
    a = create(client)
    r = client.patch(f"/api/artifacts/{a['id']}", json={"body": "a" * (MAX_BODY_BYTES + 1)}, headers=auth(ALICE))
    assert r.status_code == 413
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()["artifact"]["current_version"] == 1


def test_invalid_kind_is_400(client):
    r = client.post("/api/artifacts", headers=auth(ALICE), json={"title": "T", "kind": "exe", "body": "x"})
    assert r.status_code == 400


def test_delete_by_owner(client):
    a = create(client)
    assert client.delete(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).status_code == 404


def test_admin_can_delete_any_artifact(client):
    a = create(client, who=BOB)
    assert client.delete(f"/api/artifacts/{a['id']}", headers=auth(CAROL)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 404


def test_views_tracked_and_count_visible_to_owner_only(client):
    a = create(client, shared_with=[BOB])
    for _ in range(2):
        assert client.post(f"/api/artifacts/{a['id']}/views", headers=auth(BOB)).status_code == 200
    client.post(f"/api/artifacts/{a['id']}/views", headers=auth(ALICE))  # owner: not counted
    owner_view = client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()["artifact"]
    assert owner_view["viewers_count"] == 1
    assert owner_view["view_count"] == 2
    guest_view = client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).json()["artifact"]
    assert "viewers" not in guest_view and "viewers_count" not in guest_view
    assert "shared_with" not in guest_view


def test_cdn_reference_warns_at_publish(client):
    a = create(client, body='<script src="https://cdn.example.net/chart.js"></script>')
    assert a["warnings"] and "network" in a["warnings"][0]
    assert create(client, body="<script>1+1</script>")["warnings"] == []


def test_people_autocomplete_from_known_users(client):
    client.get("/api/me", headers=auth(BOB))
    client.get("/api/me", headers=auth(ALICE))
    people = client.get("/api/people?q=bo", headers=auth(ALICE)).json()["people"]
    assert people == [{"email": BOB, "name": "Bob Builder"}]
    assert client.get("/api/people?q=al", headers=auth(ALICE)).json()["people"] == []  # never yourself


def test_app_responses_are_not_frameable(client):
    r = client.get("/api/config")
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_health_routes(client):
    # /healthz for container probes; /api/health from outside (Cloud Run reserves /healthz).
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/api/health").json() == {"ok": True}
