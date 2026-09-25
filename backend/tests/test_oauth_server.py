"""Authorization server for MCP clients: CIMD client resolution, upstream sign-in,
consent, token issuance, refresh rotation and replay handling. The upstream IdP
and the client metadata host are faked; no network."""
import base64
import hashlib
import json
import re
import secrets
import socket
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from artifact_hub.api import create_app
from artifact_hub.auth import Authenticator
from artifact_hub.auth.oidc import OIDCVerifier
from artifact_hub.oauth.cimd import (ClientMetadataResolver, InvalidClient, _public_addresses, redirect_matches,
                                     validate_client_id_url)
from artifact_hub.oauth.grants import FirestoreGrantStore, MemoryGrantStore
from artifact_hub.oauth.keys import SigningKey
from artifact_hub.oauth.server import AuthorizationServer
from artifact_hub.oauth.upstream import UpstreamLogin
from conftest import BASE, _backends, make_settings

IDP = "https://idp.test"
IDP_CLIENT = "hub-web-client"
CLIENT_ID = "https://client.example/oauth/metadata.json"
REDIRECT = "http://localhost:53999/callback"
IDP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
HUB_KEY = SigningKey.generate()
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def client_doc(**over):
    doc = {"client_id": CLIENT_ID, "client_name": "Test <b>Agent</b>", "redirect_uris": ["http://localhost/callback"],
           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
           "token_endpoint_auth_method": "none"}
    doc.update(over)
    return {k: v for k, v in doc.items() if v is not None}


class FakeIdP:
    """Discovery + token endpoint of the upstream IdP. The ID token echoes the
    nonce of the last authorize redirect."""

    def __init__(self, email="jane@corp.example"):
        self.email = email
        self.nonce = None
        self.token_requests = []

    def handler(self, request: httpx.Request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"authorization_endpoint": f"{IDP}/authorize",
                                             "token_endpoint": f"{IDP}/token"})
        form = dict(httpx.QueryParams(request.content.decode()))
        self.token_requests.append(form)
        if form.get("code") != "upstream-code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        id_token = jwt.encode({"iss": IDP, "aud": IDP_CLIENT, "sub": "idp-42", "iat": now, "exp": now + 600,
                               "email": self.email, "email_verified": True, "name": "Jane",
                               "nonce": self.nonce}, IDP_KEY, algorithm="RS256")
        return httpx.Response(200, json={"id_token": id_token, "access_token": "opaque"})


def build(doc=None, grants=None, email="jane@corp.example", **over):
    s = make_settings(auth_mode="oidc", oidc_issuer=IDP, oidc_audiences=(IDP_CLIENT,), oidc_client_id=IDP_CLIENT,
                      oidc_client_secret="upstream-secret", render_ticket_secret="t" * 40, oauth_server=True,
                      oauth_signing_key=HUB_KEY.private_pem(), allowed_email_domains=("corp.example",), **over)
    idp = FakeIdP(email)
    verifier = OIDCVerifier(s, jwk_resolver=lambda _t: IDP_KEY.public_key())
    body = json.dumps(doc if doc is not None else client_doc()).encode()
    resolver = ClientMetadataResolver(fetch=lambda url: (200, {"cache-control": "max-age=600"}, body))
    upstream = UpstreamLogin(s, verifier, client=httpx.Client(transport=httpx.MockTransport(idp.handler)))
    server = AuthorizationServer(s, grants or MemoryGrantStore(), HUB_KEY, resolver, upstream)
    _, root = create_app(s, authenticator=Authenticator(s, verifier), oauth_server=server)
    return TestClient(root, follow_redirects=False), idp, server


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(challenge, **over):
    params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "state": "st-1",
              "code_challenge": challenge, "code_challenge_method": "S256", "scope": "artifacts",
              "resource": f"{BASE}/mcp"}
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def sign_in(c, idp, challenge, **over):
    """authorize -> upstream -> callback. Returns the consent page response."""
    r = c.get("/oauth/authorize", params=authorize_params(challenge, **over))
    assert r.status_code == 302 and r.headers["location"].startswith(f"{IDP}/authorize?"), r.text
    up = query(r.headers["location"])
    assert up["client_id"] == IDP_CLIENT and up["redirect_uri"] == f"{BASE}/oauth/callback"
    assert up["code_challenge_method"] == "S256" and up["hd"] == "corp.example"
    idp.nonce = up["nonce"]
    return c.get("/oauth/callback", params={"state": up["state"], "code": "upstream-code"})


def consent_form(page):
    tx = re.search(r'name="tx" value="([^"]+)"', page.text).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return {"tx": tx, "csrf": csrf}


def full_flow(c, idp):
    verifier, challenge = pkce()
    page = sign_in(c, idp, challenge)
    r = c.post("/oauth/consent", data={**consent_form(page), "decision": "allow"})
    assert r.status_code == 303
    back = query(r.headers["location"])
    assert r.headers["location"].startswith(REDIRECT) and back["state"] == "st-1" and back["iss"] == BASE
    tok = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": back["code"],
                                       "redirect_uri": REDIRECT, "client_id": CLIENT_ID, "code_verifier": verifier,
                                       "resource": f"{BASE}/mcp"})
    assert tok.status_code == 200, tok.text
    return back["code"], verifier, tok.json()


def mcp_list(c, access_token):
    return c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {access_token}"},
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})


# ── discovery ───────────────────────────────────────────────────────────────

def test_metadata_documents():
    c, _, _ = build()
    with c:
        asm = c.get("/.well-known/oauth-authorization-server").json()
        assert asm["issuer"] == BASE and asm["client_id_metadata_document_supported"] is True
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert asm["token_endpoint_auth_methods_supported"] == ["none"]
        assert asm["authorization_response_iss_parameter_supported"] is True
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["authorization_servers"] == [BASE] and prm["resource"] == f"{BASE}/mcp"
        assert c.get("/oauth/jwks").json()["keys"][0]["kid"] == HUB_KEY.kid
        r = c.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]


# ── happy path ──────────────────────────────────────────────────────────────

def test_full_flow_issues_audience_bound_token_usable_on_mcp_only():
    c, idp, _ = build()
    with c:
        verifier, challenge = pkce()
        page = sign_in(c, idp, challenge)
        assert page.status_code == 200 and "jane@corp.example" in page.text
        assert "Test &lt;b&gt;Agent&lt;/b&gt;" in page.text and "<b>Agent</b>" not in page.text  # escaped
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert idp.token_requests[-1]["client_secret"] == "upstream-secret"
        _, _, tokens = full_flow(c, idp)
        claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
        assert claims["aud"] == f"{BASE}/mcp" and claims["iss"] == BASE and claims["email"] == "jane@corp.example"
        assert claims["client_id"] == CLIENT_ID and tokens["scope"] == "artifacts" and tokens["refresh_token"]
        assert mcp_list(c, tokens["access_token"]).status_code == 200
        # The REST API does not accept tokens minted for the MCP resource.
        assert c.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401


def test_refresh_rotates_and_replay_revokes_family():
    c, idp, _ = build()
    with c:
        _, _, first = full_flow(c, idp)
        refresh = lambda rt: c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": rt,
                                                          "client_id": CLIENT_ID})
        second = refresh(first["refresh_token"])
        assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
        assert mcp_list(c, second.json()["access_token"]).status_code == 200
        replay = refresh(first["refresh_token"])
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        assert refresh(second.json()["refresh_token"]).status_code == 400  # whole sign-in revoked


def test_code_is_single_use_and_replay_revokes():
    c, idp, _ = build()
    with c:
        code, verifier, tokens = full_flow(c, idp)
        again = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                             "redirect_uri": REDIRECT, "client_id": CLIENT_ID,
                                             "code_verifier": verifier})
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
        r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                         "client_id": CLIENT_ID})
        assert r.status_code == 400


# ── token endpoint refusals ─────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"code_verifier": "x" * 43},
    {"code_verifier": None},
    {"client_id": "https://other.example/cimd.json"},
    {"redirect_uri": "http://localhost:1/callback"},
    {"resource": "https://elsewhere.example/mcp"},
    {"grant_type": "client_credentials"},
])
def test_token_endpoint_refusals(bad):
    c, idp, _ = build()
    with c:
        verifier, challenge = pkce()
        page = sign_in(c, idp, challenge)
        code = query(c.post("/oauth/consent", data={**consent_form(page), "decision": "allow"})
                     .headers["location"])["code"]
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                "client_id": CLIENT_ID, "code_verifier": verifier, **bad}
        r = c.post("/oauth/token", data={k: v for k, v in form.items() if v is not None})
        assert r.status_code == 400 and "access_token" not in r.json()


def test_token_endpoint_refuses_client_authentication():
    c, _, _ = build()
    with c:
        r = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x"},
                   headers={"Authorization": "Basic Zm9vOmJhcg=="})
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_forged_access_tokens_rejected_on_mcp():
    c, _, _ = build()
    with c:
        now = int(time.time())
        base = {"iss": BASE, "aud": f"{BASE}/mcp", "sub": "x", "email": "jane@corp.example",
                "client_id": CLIENT_ID, "scope": "artifacts", "iat": now, "exp": now + 600}
        other = SigningKey.generate()
        assert mcp_list(c, other.sign(base)).status_code == 401                       # wrong key
        assert mcp_list(c, HUB_KEY.sign({**base, "aud": f"{BASE}/api"})).status_code == 401  # wrong audience
        assert mcp_list(c, HUB_KEY.sign({**base, "exp": now - 120})).status_code == 401      # expired
        assert mcp_list(c, HUB_KEY.sign({**base, "email": "eve@evil.example"})).status_code == 401  # domain policy


# ── authorize refusals ──────────────────────────────────────────────────────

@pytest.mark.parametrize("over", [
    {"client_id": "plain-client-id"},
    {"client_id": "http://client.example/meta.json"},
    {"redirect_uri": "https://attacker.example/callback"},
    {"redirect_uri": "http://127.0.0.1:53999/callback"},  # loopback host must match the registered one
    {"redirect_uri": None},
])
def test_bad_client_or_redirect_is_never_redirected(over):
    c, _, _ = build()
    with c:
        _, challenge = pkce()
        r = c.get("/oauth/authorize", params=authorize_params(challenge, **over))
        assert r.status_code == 400 and "location" not in r.headers


@pytest.mark.parametrize("doc", [
    client_doc(client_id="https://client.example/other.json"),
    client_doc(token_endpoint_auth_method="private_key_jwt"),
    client_doc(client_secret="leaked"),
    client_doc(redirect_uris=["javascript:alert(1)"]),
    client_doc(redirect_uris=["http://client.example/callback"]),
    client_doc(redirect_uris=None),
])
def test_invalid_metadata_documents(doc):
    c, _, _ = build(doc=doc)
    with c:
        _, challenge = pkce()
        r = c.get("/oauth/authorize", params=authorize_params(challenge))
        assert r.status_code == 400 and "location" not in r.headers


@pytest.mark.parametrize("over,error", [
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge": None}, "invalid_request"),
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"scope": "artifacts admin"}, "invalid_scope"),
    ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
])
def test_request_errors_redirect_back_with_error(over, error):
    c, _, _ = build()
    with c:
        _, challenge = pkce()
        r = c.get("/oauth/authorize", params=authorize_params(challenge, **over))
        assert r.status_code == 303
        back = query(r.headers["location"])
        assert back["error"] == error and back["state"] == "st-1" and back["iss"] == BASE


def test_common_extra_scopes_are_tolerated():
    c, idp, _ = build()
    with c:
        _, challenge = pkce()
        assert sign_in(c, idp, challenge, scope="openid offline_access artifacts").status_code == 200


# ── browser binding, consent, account policy ────────────────────────────────

def test_callback_in_another_browser_is_refused():
    c, idp, _ = build()
    with c:
        _, challenge = pkce()
        r = c.get("/oauth/authorize", params=authorize_params(challenge))
        up = query(r.headers["location"])
        idp.nonce = up["nonce"]
        c.cookies.clear()
        victim = c.get("/oauth/callback", params={"state": up["state"], "code": "upstream-code"})
        assert victim.status_code == 400 and not idp.token_requests


def test_consent_requires_csrf_and_is_single_use():
    c, idp, _ = build()
    with c:
        _, challenge = pkce()
        form = consent_form(sign_in(c, idp, challenge))
        assert c.post("/oauth/consent", data={**form, "csrf": "wrong", "decision": "allow"}).status_code == 400
        assert c.post("/oauth/consent", data={**form, "decision": "allow"}).status_code == 400  # burnt


def test_deny_returns_access_denied():
    c, idp, _ = build()
    with c:
        _, challenge = pkce()
        r = c.post("/oauth/consent", data={**consent_form(sign_in(c, idp, challenge)), "decision": "deny"})
        assert query(r.headers["location"])["error"] == "access_denied"


def test_account_outside_allowed_domains_is_denied():
    c, idp, _ = build(email="eve@evil.example")
    with c:
        _, challenge = pkce()
        r = sign_in(c, idp, challenge)
        assert r.status_code == 303 and query(r.headers["location"])["error"] == "access_denied"


# ── CIMD building blocks ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://client.example/meta.json", "https://client.example", "https://client.example/",
    "https://user:pw@client.example/m.json", "https://client.example/m.json#x",
    "https://client.example:8443/m.json", "https://client.example/a/../m.json",
])
def test_client_id_url_rules(url):
    with pytest.raises(InvalidClient):
        validate_client_id_url(url)


def test_redirect_matching():
    assert redirect_matches("http://localhost/callback", "http://localhost:8765/callback")
    assert redirect_matches("http://127.0.0.1:1/cb", "http://127.0.0.1:2/cb")
    assert not redirect_matches("http://localhost/callback", "http://localhost:8765/other")
    assert not redirect_matches("https://app.example/cb", "https://app.example:444/cb")
    assert not redirect_matches("https://app.example/cb", "https://app.example/cb?x=1")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"])
def test_non_public_hosts_refused(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(InvalidClient):
        _public_addresses("client.example")


def test_metadata_is_cached_and_size_capped():
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, {"Cache-Control": "max-age=900"}, json.dumps(client_doc()).encode()

    resolver = ClientMetadataResolver(fetch=fetch)
    assert resolver.resolve(CLIENT_ID).client_name == "Test <b>Agent</b>"
    resolver.resolve(CLIENT_ID)
    assert len(calls) == 1
    big = ClientMetadataResolver(fetch=lambda u: (200, {}, json.dumps(client_doc(pad="x" * 6000)).encode()))
    with pytest.raises(InvalidClient):
        big.resolve(CLIENT_ID)


def test_client_host_allow_list():
    resolver = ClientMetadataResolver(fetch=lambda u: (200, {}, json.dumps(client_doc()).encode()),
                                      allowed_hosts=("claude.ai",))
    with pytest.raises(InvalidClient):
        resolver.resolve(CLIENT_ID)


# ── grant store semantics (memory, and Firestore emulator when available) ───

@pytest.fixture(params=_backends())
def grant_store(request):
    if request.param == "memory":
        return MemoryGrantStore()
    import uuid
    from google.cloud import firestore
    return FirestoreGrantStore(firestore.Client(project=f"test-{uuid.uuid4().hex[:8]}"))


def test_grant_store_use_is_first_then_replay(grant_store):
    grant_store.put("code", "abc", {"family": "f"}, 60)
    assert grant_store.use("code", "abc") == ({"family": "f", "used": True}, True)
    assert grant_store.use("code", "abc")[1] is False
    assert grant_store.use("code", "missing") is None
    grant_store.put("rt", "old", {"x": 1}, -1)  # already expired
    assert grant_store.get("rt", "old") is None and grant_store.use("rt", "old") is None


def test_full_flow_on_each_grant_backend(grant_store):
    c, idp, _ = build(grants=grant_store)
    with c:
        _, _, tokens = full_flow(c, idp)
        assert mcp_list(c, tokens["access_token"]).status_code == 200
