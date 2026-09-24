"""Generic OIDC bearer-token validation (Entra ID, Okta, Google, any compliant IdP).

Checks performed on every token:
  * signature against the IdP JWKS (discovered from the issuer, cached, rotated
    keys picked up automatically by PyJWKClient),
  * `iss` equals the configured issuer exactly,
  * `aud` contains one of the configured audiences,
  * `exp` / `nbf` / `iat` with a small leeway,
  * an explicit `email_verified: false` is refused (Google); IdPs that do not emit
    the claim (Entra ID) are trusted on the tenant's own verification.

The identity key is the first present claim among OIDC_EMAIL_CLAIMS (Entra ID often
carries the address in `preferred_username` or `upn` rather than `email`). Groups
come from OIDC_GROUPS_CLAIM. Known limitation: Entra ID replaces the groups claim by
an overage marker (`_claim_names`) past ~200 groups; restrict the app registration
to "groups assigned to the application" so the claim stays complete.
"""
from __future__ import annotations

import logging

import httpx
import jwt

from .identity import Principal, build_principal

log = logging.getLogger(__name__)

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class OIDCVerifier:
    def __init__(self, settings, jwk_resolver=None):
        """`jwk_resolver(token) -> key` is injectable for tests; production uses a
        PyJWKClient pointed at the discovered (or configured) JWKS URI."""
        self.settings = settings
        self._resolver = jwk_resolver
        self._jwks_client: jwt.PyJWKClient | None = None

    def _jwks_uri(self) -> str:
        if self.settings.oidc_jwks_uri:
            return self.settings.oidc_jwks_uri
        url = f"{self.settings.oidc_issuer}/.well-known/openid-configuration"
        doc = httpx.get(url, timeout=10).raise_for_status().json()
        return doc["jwks_uri"]

    def _signing_key(self, token: str):
        if self._resolver is not None:
            return self._resolver(token)
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(self._jwks_uri(), cache_keys=True, lifespan=3600)
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def verify_claims(self, token: str) -> dict | None:
        try:
            key = self._signing_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=list(self.settings.oidc_audiences),
                issuer=self.settings.oidc_issuer,
                leeway=30,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:  # any failure is an authentication failure
            log.info("oidc token rejected: %s", type(exc).__name__)
            return None
        if claims.get("email_verified") is False:
            return None
        return claims

    def principal_from_claims(self, claims: dict) -> Principal | None:
        email = ""
        for name in self.settings.oidc_email_claims:
            value = claims.get(name)
            if isinstance(value, str) and "@" in value:
                email = value
                break
        groups = claims.get(self.settings.oidc_groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        return build_principal(
            self.settings,
            email=email,
            name=str(claims.get(self.settings.oidc_name_claim) or ""),
            subject=str(claims.get("sub") or ""),
            groups=groups,
            via="oidc",
        )

    def authenticate(self, token: str) -> Principal | None:
        claims = self.verify_claims(token)
        return self.principal_from_claims(claims) if claims else None
