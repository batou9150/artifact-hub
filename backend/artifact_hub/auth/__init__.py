"""Authentication entry point shared by the REST API, the sandbox and the MCP."""
from __future__ import annotations

from .dev import authenticate_dev
from .identity import Principal
from .oidc import OIDCVerifier


class Authenticator:
    def __init__(self, settings, oidc_verifier: OIDCVerifier | None = None):
        self.settings = settings
        self.oidc = oidc_verifier or (OIDCVerifier(settings) if settings.auth_mode == "oidc" else None)

    def authenticate(self, token: str | None) -> Principal | None:
        token = (token or "").strip()
        if not token:
            return None
        if self.settings.auth_mode == "dev":
            return authenticate_dev(self.settings, token)
        return self.oidc.authenticate(token) if self.oidc else None


def bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


__all__ = ["Authenticator", "Principal", "bearer"]
