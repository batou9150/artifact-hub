"""Confidential-client code exchange for IdPs that demand a client secret.

Google "Web application" OAuth clients refuse the authorization-code exchange
without `client_secret`, even with PKCE. The secret must never reach the browser,
so when OIDC_CLIENT_SECRET is set the SPA posts its code + PKCE verifier to
POST /api/auth/token and this module forwards it to the IdP token endpoint with
the secret added. The browser still drives the flow and keeps its tokens; the
server only adds the secret.

Only the authorization_code grant is relayed, only for the configured client and
only with the app's own redirect URI, so the endpoint cannot be used to redeem
codes issued to another client or another redirect.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class TokenExchangeError(Exception):
    def __init__(self, status: int, error: str, description: str = ""):
        super().__init__(description or error)
        self.status = status
        self.body = {"error": error, "error_description": description} if description else {"error": error}


class TokenExchange:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        self._client = client or httpx.Client(timeout=10)
        self._token_endpoint: str | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.app_origin}/callback"

    def token_endpoint(self) -> str:
        if self._token_endpoint is None:
            url = f"{self.settings.oidc_issuer}/.well-known/openid-configuration"
            self._token_endpoint = self._client.get(url).raise_for_status().json()["token_endpoint"]
        return self._token_endpoint

    def exchange(self, form: dict[str, str]) -> tuple[int, dict]:
        if form.get("grant_type") != "authorization_code":
            raise TokenExchangeError(400, "unsupported_grant_type")
        if form.get("client_id") != self.settings.oidc_client_id:
            raise TokenExchangeError(400, "invalid_client")
        if form.get("redirect_uri") != self.redirect_uri:
            raise TokenExchangeError(400, "invalid_grant", "redirect_uri mismatch")
        if not form.get("code") or not form.get("code_verifier"):
            raise TokenExchangeError(400, "invalid_request", "code and code_verifier are required")
        upstream = {
            "grant_type": "authorization_code",
            "code": form["code"],
            "code_verifier": form["code_verifier"],
            "redirect_uri": self.redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "client_secret": self.settings.oidc_client_secret,
        }
        try:
            res = self._client.post(self.token_endpoint(), data=upstream,
                                    headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            log.warning("token exchange failed: %s", type(exc).__name__)
            raise TokenExchangeError(502, "temporarily_unavailable") from exc
        try:
            body = res.json()
        except ValueError:
            body = {"error": "server_error"}
        if res.status_code >= 400:
            log.info("token exchange refused by the IdP: %s", body.get("error"))
        return res.status_code, body
