"""OIDC validation with a locally generated RSA key (no network, no real IdP)."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from artifact_hub.api import create_app
from artifact_hub.auth import Authenticator
from artifact_hub.auth.oidc import OIDCVerifier
from artifact_hub.config import Settings
from conftest import BASE, make_settings

ISSUER = "https://login.example-idp.test/tenant/v2.0"
AUD = "api://artifact-hub"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_settings(**over):
    return make_settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audiences=(AUD,),
                         render_ticket_secret="test-secret-" + "x" * 20, **over)


def verifier(settings):
    return OIDCVerifier(settings, jwk_resolver=lambda _t: KEY.public_key())


def token(key=KEY, **claims):
    now = int(time.time())
    base = {"iss": ISSUER, "aud": AUD, "sub": "u-123", "iat": now, "exp": now + 600,
            "email": "Jane.Doe@corp.example", "name": "Jane Doe", "groups": ["artifact-publishers"]}
    base.update(claims)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, key, algorithm="RS256")


def test_valid_token_yields_principal():
    s = oidc_settings()
    p = verifier(s).authenticate(token())
    assert p.email == "jane.doe@corp.example" and p.name == "Jane Doe" and p.subject == "u-123"
    assert p.is_publisher and not p.is_admin


@pytest.mark.parametrize("bad", [
    {"aud": "api://someone-else"},
    {"iss": "https://evil.test"},
    {"exp": int(time.time()) - 3600},
    {"email_verified": False},
    {"exp": None},
])
def test_invalid_claims_rejected(bad):
    assert verifier(oidc_settings()).authenticate(token(**bad)) is None


def test_bad_signature_rejected():
    assert verifier(oidc_settings()).authenticate(token(key=OTHER_KEY)) is None


def test_alg_none_rejected():
    unsigned = jwt.encode({"iss": ISSUER, "aud": AUD, "exp": int(time.time()) + 60, "email": "a@b.co"},
                          key=None, algorithm="none")
    assert verifier(oidc_settings()).authenticate(unsigned) is None


def test_entra_style_preferred_username_and_admin_group():
    p = verifier(oidc_settings()).authenticate(token(email=None, preferred_username="ops@corp.example",
                                                     groups=["artifact-admins"]))
    assert p.email == "ops@corp.example" and p.is_admin and p.is_publisher


def test_custom_groups_claim():
    s = oidc_settings(oidc_groups_claim="roles")
    p = verifier(s).authenticate(token(groups=None, roles=["artifact-publishers"]))
    assert p.is_publisher


def test_domain_allow_list():
    s = oidc_settings(allowed_email_domains=("other.example",))
    assert verifier(s).authenticate(token()) is None


def test_api_and_mcp_in_oidc_mode():
    s = oidc_settings()
    _, root = create_app(s, authenticator=Authenticator(s, verifier(s)))
    with TestClient(root) as c:
        h = {"Authorization": f"Bearer {token()}"}
        assert c.get("/api/me", headers=h).json()["email"] == "jane.doe@corp.example"
        assert c.get("/api/me", headers={"Authorization": "Bearer dev:alice@example.com"}).status_code == 401
        cfg = c.get("/api/config").json()
        assert cfg["auth_mode"] == "oidc" and "dev_users" not in cfg and cfg["oidc"]["issuer"] == ISSUER
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["authorization_servers"] == [ISSUER]
        r = c.post("/mcp", headers={**h, "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 200


def test_settings_guards():
    with pytest.raises(ValueError):
        Settings(auth_mode="dev", environment="prod").check()
    with pytest.raises(ValueError):
        Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audiences=(AUD,)).check()  # dev ticket secret
    with pytest.raises(ValueError):
        Settings(auth_mode="oidc").check()
    Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audiences=(AUD,), render_ticket_secret="s" * 32).check()
    for weak in ("", "short"):
        with pytest.raises(ValueError):
            Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audiences=(AUD,), render_ticket_secret=weak).check()


# ── confidential code exchange (Google "Web application" clients) ────────────

def exchange_app(upstream_calls):
    import httpx
    from artifact_hub.auth.token_exchange import TokenExchange

    def handler(request: httpx.Request):
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "https://idp.test/token"})
        upstream_calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"id_token": "id", "access_token": "at", "token_type": "Bearer"})

    s = oidc_settings(oidc_client_id="spa-client", oidc_client_secret="s3cret")
    tx = TokenExchange(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    fastapi_app, _root = create_app(s, authenticator=Authenticator(s, verifier(s)), token_exchange=tx)
    return fastapi_app


def exchange_form(**over):
    form = {"grant_type": "authorization_code", "code": "abc", "code_verifier": "v" * 43,
            "redirect_uri": f"{BASE}/callback", "client_id": "spa-client"}
    form.update(over)
    return {k: v for k, v in form.items() if v is not None}


def test_token_exchange_adds_secret_server_side():
    calls = []
    with TestClient(exchange_app(calls)) as c:
        cfg = c.get("/api/config").json()
        assert cfg["oidc"]["token_endpoint"] == f"{BASE}/api/auth/token"
        assert "s3cret" not in str(cfg)
        r = c.post("/api/auth/token", data=exchange_form())
    assert r.status_code == 200 and r.json()["id_token"] == "id"
    assert r.headers["cache-control"] == "no-store"
    assert calls == [{**exchange_form(), "client_secret": "s3cret"}]


@pytest.mark.parametrize("bad", [
    {"grant_type": "refresh_token"},
    {"grant_type": "client_credentials"},
    {"client_id": "another-client"},
    {"redirect_uri": "https://evil.test/callback"},
    {"code_verifier": None},
    {"code": None},
])
def test_token_exchange_refuses_anything_else(bad):
    calls = []
    with TestClient(exchange_app(calls)) as c:
        r = c.post("/api/auth/token", data=exchange_form(**bad))
    assert r.status_code == 400 and "error" in r.json()
    assert calls == []


def test_no_exchange_endpoint_without_secret():
    s = oidc_settings(oidc_client_id="spa-client")
    fastapi_app, _root = create_app(s, authenticator=Authenticator(s, verifier(s)))
    with TestClient(fastapi_app) as c:
        assert "token_endpoint" not in c.get("/api/config").json()["oidc"]
        assert c.post("/api/auth/token", data=exchange_form()).status_code in (404, 405)


# ── opaque access tokens (Google, sent by MCP clients) ──────────────────────

def opaque_verifier(info=None, status=200, calls=None):
    import httpx

    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=info or {})

    s = oidc_settings(oidc_tokeninfo_url="https://idp.test/tokeninfo")
    return OIDCVerifier(s, jwk_resolver=lambda _t: KEY.public_key(),
                        http=httpx.Client(transport=httpx.MockTransport(handler)))


def google_info(**over):
    info = {"aud": AUD, "azp": AUD, "sub": "g-1", "email": "Jane@corp.example", "email_verified": "true",
            "exp": str(int(time.time()) + 600), "scope": "openid https://www.googleapis.com/auth/userinfo.email"}
    info.update(over)
    return info


def test_opaque_token_via_tokeninfo_and_cached():
    calls = []
    v = opaque_verifier(google_info(), calls=calls)
    p = v.authenticate("ya29.opaque-token")
    assert p.email == "jane@corp.example" and p.subject == "g-1"
    assert v.authenticate("ya29.opaque-token").email == "jane@corp.example"
    assert len(calls) == 1 and b"ya29.opaque-token" in calls[0].content  # POST body, not the URL


@pytest.mark.parametrize("bad", [
    {"aud": "another-app.apps.googleusercontent.com"},
    {"exp": str(int(time.time()) - 10)},
    {"email_verified": "false"},
])
def test_opaque_token_rejected(bad):
    assert opaque_verifier(google_info(**bad)).authenticate("ya29.x") is None


def test_opaque_token_rejected_on_introspection_error():
    assert opaque_verifier({"error": "invalid_token"}, status=400).authenticate("ya29.x") is None


def test_opaque_token_ignored_without_tokeninfo_url():
    assert verifier(oidc_settings()).authenticate("ya29.opaque") is None
