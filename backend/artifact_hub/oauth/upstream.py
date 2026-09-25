"""Sign-in of the human at the upstream IdP (OIDC_ISSUER), on behalf of the
authorization server: authorization code + PKCE + nonce, confidential client
(OIDC_CLIENT_ID / OIDC_CLIENT_SECRET, server side only). The ID token is verified
with the same OIDC validator as the REST API, with the client id as audience.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    pass


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class UpstreamLogin:
    def __init__(self, settings, verifier, client: httpx.Client | None = None):
        self.settings = settings
        self.verifier = verifier
        self._client = client or httpx.Client(timeout=10)
        self._discovery: dict | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.public_base_url}/oauth/callback"

    def _endpoints(self) -> dict:
        if self._discovery is None:
            url = f"{self.settings.oidc_issuer}/.well-known/openid-configuration"
            self._discovery = self._client.get(url).raise_for_status().json()
        return self._discovery

    def authorize_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": s256(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if len(self.settings.allowed_email_domains) == 1:  # Google: preselect the workspace domain
            params["hd"] = self.settings.allowed_email_domains[0]
        return f"{self._endpoints()['authorization_endpoint']}?{urlencode(params)}"

    def exchange(self, *, code: str, code_verifier: str, nonce: str) -> dict:
        """Returns the verified ID token claims."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": self.redirect_uri,
            "client_id": self.settings.oidc_client_id,
        }
        if self.settings.oidc_client_secret:
            form["client_secret"] = self.settings.oidc_client_secret
        try:
            res = self._client.post(self._endpoints()["token_endpoint"], data=form,
                                    headers={"Accept": "application/json"})
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError("identity provider unreachable") from exc
        if res.status_code != 200 or not isinstance(body.get("id_token"), str):
            log.info("upstream code exchange refused: %s", body.get("error"))
            raise UpstreamError("sign-in was refused by the identity provider")
        claims = self.verifier.verify_claims(body["id_token"], audience=self.settings.oidc_client_id)
        if claims is None or claims.get("nonce") != nonce:
            raise UpstreamError("invalid ID token")
        return claims
