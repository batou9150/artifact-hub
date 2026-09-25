"""Administrators: ADMIN_EMAILS, listing, stats and moderation."""
import pytest
from fastapi.testclient import TestClient

from artifact_hub.api import create_app
from artifact_hub.auth.identity import build_principal
from conftest import ALICE, BOB, CAROL, DAVE, auth, create, make_settings

ERIN = "erin@example.com"  # admin by email only, no group


@pytest.fixture
def client(store):
    _, root = create_app(make_settings(admin_emails=(ERIN,)), store=store, embedder=store.embedder)
    with TestClient(root) as c:
        yield c


def test_admin_emails_grant_admin_and_publisher():
    s = make_settings(admin_emails=("ops@example.com",))
    p = build_principal(s, email="OPS@example.com")
    assert p.is_admin and p.is_publisher
    assert not build_principal(s, email="other@example.com").is_admin


def test_admin_emails_from_env(monkeypatch):
    from artifact_hub.config import Settings
    monkeypatch.setenv("ADMIN_EMAILS", " Ops@Example.com , b@example.com")
    assert Settings.from_env().admin_emails == ("ops@example.com", "b@example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "not-an-email")
    with pytest.raises(ValueError, match="ADMIN_EMAILS"):
        Settings.from_env()


def test_me_reports_email_admin(client):
    assert client.get("/api/me", headers=auth(ERIN)).json()["is_admin"] is True


@pytest.mark.parametrize("who", [ALICE, BOB])
def test_admin_endpoints_refuse_non_admins(client, who):
    a = create(client)
    assert client.get("/api/admin/stats", headers=auth(who)).status_code == 403
    assert client.get("/api/admin/artifacts", headers=auth(who)).status_code == 403
    assert client.post(f"/api/admin/artifacts/{a['id']}/moderate", json={"withdraw_org": True},
                       headers=auth(who)).status_code == 403
    assert client.delete(f"/api/admin/artifacts/{a['id']}", headers=auth(who)).status_code == 403
    assert client.get("/api/admin/stats").status_code == 401


def test_list_shows_private_metadata_but_never_bodies_or_viewers(client):
    a = create(client, title="Private numbers", shared_with=[DAVE])
    client.post(f"/api/artifacts/{a['id']}/views", headers=auth(DAVE))
    r = client.get("/api/admin/artifacts", headers=auth(ERIN)).json()
    assert r["total"] == 1
    row = r["artifacts"][0]
    assert row["title"] == "Private numbers" and row["owner"] == ALICE and row["shared_with_count"] == 1
    assert not {"body", "viewers", "shared_with", "embedding"} & row.keys()
    # Listing is not opening: the private body stays out of reach.
    assert client.get(f"/api/artifacts/{a['id']}/body", headers=auth(ERIN)).status_code == 404


def test_list_filters(client):
    create(client, title="Sales A", visibility="shared")
    create(client, who=BOB, title="Budget B")
    s = create(client, title="Payroll", sensitive=True)
    get = lambda **q: client.get("/api/admin/artifacts", params=q, headers=auth(CAROL)).json()  # noqa: E731
    assert get()["total"] == 3
    assert [a["title"] for a in get(q="sales")["artifacts"]] == ["Sales A"]
    assert [a["title"] for a in get(owner=BOB)["artifacts"]] == ["Budget B"]
    assert [a["title"] for a in get(visibility="shared")["artifacts"]] == ["Sales A"]
    assert [a["id"] for a in get(sensitive="true")["artifacts"]] == [s["id"]]


def test_stats(client):
    create(client, visibility="shared")
    create(client, kind="markdown", body="# x")
    create(client, who=BOB, sensitive=True)
    st = client.get("/api/admin/stats", headers=auth(ERIN)).json()
    assert st["artifacts"] == 3 and st["owners"] == 2 and st["sensitive"] == 1
    assert st["by_visibility"] == {"shared": 1, "private": 2}
    assert st["by_kind"] == {"html": 2, "markdown": 1}
    assert st["top_owners"][0] == {"email": ALICE, "artifacts": 2}


def test_withdraw_org_share_and_clear_invites(client):
    a = create(client, visibility="shared", shared_with=[DAVE])
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 200
    r = client.post(f"/api/admin/artifacts/{a['id']}/moderate", headers=auth(ERIN),
                    json={"withdraw_org": True, "clear_invites": True, "note": "contains customer names"})
    assert r.status_code == 200, r.text
    assert r.json()["artifact"]["visibility"] == "private"
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 404
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(DAVE)).status_code == 404
    # The owner sees what happened, and why.
    mine = client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()["artifact"]
    assert mine["moderation"]["by"] == ERIN
    assert mine["moderation"]["action"] == "withdrew_org_share,cleared_invites"
    assert mine["moderation"]["note"] == "contains customer names"
    assert isinstance(mine["moderation"]["at"], str)


def test_flag_sensitive_withdraws_org_link_and_blocks_republishing(client):
    a = create(client, visibility="shared")
    r = client.post(f"/api/admin/artifacts/{a['id']}/moderate", json={"sensitive": True}, headers=auth(CAROL))
    art = r.json()["artifact"]
    assert art["sensitive"] is True and art["visibility"] == "private"
    assert client.put(f"/api/artifacts/{a['id']}/sharing", json={"visibility": "shared"},
                      headers=auth(ALICE)).status_code == 403


def test_moderate_nothing_to_change_and_unknown(client):
    a = create(client)
    assert client.post(f"/api/admin/artifacts/{a['id']}/moderate", json={"withdraw_org": True},
                       headers=auth(ERIN)).status_code == 400
    assert client.post("/api/admin/artifacts/nope/moderate", json={"withdraw_org": True},
                       headers=auth(ERIN)).status_code == 404


def test_moderation_is_hidden_from_non_owners(client):
    a = create(client, shared_with=[BOB])
    client.post(f"/api/admin/artifacts/{a['id']}/moderate", json={"sensitive": True}, headers=auth(ERIN))
    seen = client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).json()["artifact"]
    assert seen["sensitive"] is True and "moderation" not in seen


def test_admin_delete_private_artifact(client):
    a = create(client, who=BOB)
    assert client.delete(f"/api/admin/artifacts/{a['id']}", params={"note": "spam"},
                         headers=auth(ERIN)).status_code == 200
    assert client.get(f"/api/artifacts/{a['id']}", headers=auth(BOB)).status_code == 404
    assert client.get("/api/admin/stats", headers=auth(ERIN)).json()["artifacts"] == 0
