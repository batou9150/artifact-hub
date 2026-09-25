"""Authorization server for MCP clients (Client ID Metadata Documents). See server.py."""
from __future__ import annotations

from .cimd import ClientMetadataResolver
from .grants import build_grant_store
from .keys import SigningKey
from .server import AuthorizationServer
from .upstream import UpstreamLogin


def build_authorization_server(settings, oidc_verifier) -> AuthorizationServer:
    return AuthorizationServer(
        settings,
        grants=build_grant_store(settings),
        key=SigningKey.from_pem(settings.oauth_signing_key),
        resolver=ClientMetadataResolver(allowed_hosts=settings.oauth_allowed_client_hosts),
        upstream=UpstreamLogin(settings, oidc_verifier),
    )


__all__ = ["AuthorizationServer", "build_authorization_server"]
