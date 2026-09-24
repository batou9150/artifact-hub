"""MCP endpoint over streamable HTTP (stateless, JSON responses), end to end."""
import itertools
import json

from conftest import ALICE, BASE, BOB, DAVE, auth, create

_ids = itertools.count(1)
ACCEPT = {"Accept": "application/json, text/event-stream"}


def rpc(client, method, params=None, who=ALICE):
    headers = {**ACCEPT, **(auth(who) if who else {})}
    return client.post("/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}})


def call(client, tool, who=ALICE, **arguments):
    r = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, who=who)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    if result.get("isError"):
        return None, result["content"][0]["text"]
    sc = result.get("structuredContent")
    data = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return data, None


def test_unauthenticated_gets_401_with_resource_metadata(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}}, who=None)
    assert r.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    bad = client.post("/mcp", headers={**ACCEPT, "Authorization": "Bearer forged"}, json={})
    assert bad.status_code == 401


def test_protected_resource_metadata(client):
    doc = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert doc["resource"] == f"{BASE}/mcp"
    assert doc["authorization_servers"]
    assert "header" in doc["bearer_methods_supported"]


def test_tools_list(client):
    r = rpc(client, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"create_artifact", "update_artifact", "search_artifacts", "get_artifact", "share_artifact"}
    create_tool = next(t for t in r.json()["result"]["tools"] if t["name"] == "create_artifact")
    assert "owner" not in create_tool["inputSchema"]["properties"]


def test_create_is_attributed_to_the_verified_human(client):
    data, err = call(client, "create_artifact", who=BOB, title="From agent", kind="html",
                     body="<p>x</p>", source_question="How many?", owner=ALICE)
    assert err is None
    meta = client.get(f"/api/artifacts/{data['id']}", headers=auth(BOB)).json()["artifact"]
    assert meta["owner"] == BOB
    assert meta["created_via"] == "mcp"
    assert meta["source_question"] == "How many?"
    assert data["url"].endswith(data["id"])
    assert client.get(f"/api/artifacts/{data['id']}", headers=auth(ALICE)).status_code == 404


def test_create_over_cap_is_a_tool_error(client):
    _, err = call(client, "create_artifact", title="Big", kind="text", body="a" * 800_001)
    assert err and "limit" in err


def test_update_owner_only(client):
    data, _ = call(client, "create_artifact", title="T", kind="text", body="v1")
    upd, err = call(client, "update_artifact", id=data["id"], body="v2", title="T2")
    assert err is None and upd["version"] == 2 and upd["title"] == "T2"
    _, err = call(client, "update_artifact", who=BOB, id=data["id"], body="hijack")
    assert err.endswith("artifact not found or not accessible")


def test_get_artifact_returns_body_only_when_openable(client):
    a = create(client, who=ALICE, body="<p>secret</p>")
    data, err = call(client, "get_artifact", who=ALICE, id=a["id"])
    assert data["body"] == "<p>secret</p>"
    data, err = call(client, "get_artifact", who=BOB, id=a["id"])
    assert data is None and "not found" in err


def test_share_artifact_and_org_restriction(client):
    data, _ = call(client, "create_artifact", who=BOB, title="T", kind="text", body="x")
    _, err = call(client, "share_artifact", who=BOB, id=data["id"], visibility="shared")
    assert err and "artifact-publishers" in err
    shared, err = call(client, "share_artifact", who=BOB, id=data["id"], add=[DAVE])
    assert err is None and shared["shared_with"] == [DAVE]
    got, _ = call(client, "get_artifact", who=DAVE, id=data["id"])
    assert got["body"] == "x"
    _, err = call(client, "create_artifact", who=BOB, title="T", kind="text", body="x", visibility="shared")
    assert err and "artifact-publishers" in err


def test_search_artifacts_filtered_metadata_only(client):
    create(client, who=BOB, title="Stock turnover by category", body="<p>BODY</p>")          # private to Bob
    visible = create(client, who=BOB, title="Stock turnover per category", shared_with=[ALICE])
    results, err = call(client, "search_artifacts", who=ALICE, query="category stock turnover")
    assert err is None
    assert [r["id"] for r in results] == [visible["id"]]
    assert "body" not in results[0] and results[0]["url"].endswith(visible["id"])
    assert json.dumps(results).find("BODY") == -1
