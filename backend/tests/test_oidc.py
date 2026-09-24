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
