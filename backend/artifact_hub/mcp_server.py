"""MCP endpoint (official `mcp` Python SDK, streamable HTTP, stateless, JSON responses).

Authorization follows the MCP authorization spec as a RESOURCE SERVER:
  * GET /.well-known/oauth-protected-resource/mcp  (RFC 9728 metadata, served by
    the SDK) names the IdP as the authorization server;
  * POST /mcp without a valid bearer token gets
    `401 WWW-Authenticate: Bearer resource_metadata="..."`;
  * tokens are verified with the same OIDC validator as the REST API (issuer,
    audience, JWKS). In DEV AUTH MODE, `dev:<email>` tokens are accepted.

The owner of anything created here is the verified human behind the token, never
the agent: tools take no owner/author argument and read the identity from the
request's access token.

With OAUTH_SERVER on, the metadata names THIS service as the authorization
server instead (artifact_hub/oauth: Client ID Metadata Documents, upstream sign-in
at the IdP) and the tokens it issues (aud = the /mcp URL) are verified locally.
IdP tokens are still accepted, for clients pre-registered in the IdP.
"""
from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from .access import Forbidden
from .auth import Authenticator
from .auth.identity import Principal
from .service import ArtifactService, NotFound
from .store.base import ArtifactError

INSTRUCTIONS = """Artifact hub: publish self-contained HTML, Markdown or text artifacts and share them.
Before producing a new analysis, call search_artifacts with the user's question: a colleague may
already have published it. HTML artifacts render in a sandbox with NO network access: inline every
script, style and dataset (no CDN). New artifacts are private; share them with share_artifact."""


class HubTokenVerifier:
    """Bridges the SDK's TokenVerifier protocol to the hub Authenticator. The
    Principal rides in AccessToken.claims so tools can rebuild it."""

    def __init__(self, authenticator: Authenticator, settings, oauth_server=None):
        self.authenticator = authenticator
        self.settings = settings
        self.oauth_server = oauth_server

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = self.oauth_server.verify_access_token(token) if self.oauth_server else None
        if claims is not None:
            p = self.oauth_server.principal_from_claims(claims)
            client_id, scopes = claims["client_id"], str(claims.get("scope", "")).split()
        else:
            p = self.authenticator.authenticate(token)
            client_id, scopes = "artifact-hub-mcp", list(self.settings.mcp_required_scopes)
        if p is None:
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            subject=p.subject or p.email,
            resource=self.settings.mcp_resource_url,
            claims={"principal": {
                "email": p.email, "name": p.name, "subject": p.subject, "groups": sorted(p.groups),
                "is_publisher": p.is_publisher, "is_admin": p.is_admin, "via": p.via,
            }},
        )


def _principal() -> Principal:
    tok = get_access_token()
    data = (tok.claims or {}).get("principal") if tok else None
    if not data:
        raise ToolError("not authenticated")
    return Principal(email=data["email"], name=data.get("name", ""), subject=data.get("subject", ""),
                     groups=frozenset(data.get("groups", [])), is_publisher=data.get("is_publisher", False),
                     is_admin=data.get("is_admin", False), via=data.get("via", "oidc"))


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFound:
        raise ToolError("artifact not found or not accessible")
    except (Forbidden, ArtifactError) as e:
        raise ToolError(str(e))


def _meta(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


def build_mcp(settings, service: ArtifactService, authenticator: Authenticator, oauth_server=None) -> MCPServer:
    mcp = MCPServer(
        name="artifact-hub",
        instructions=INSTRUCTIONS,
        token_verifier=HubTokenVerifier(authenticator, settings, oauth_server),
        auth=AuthSettings(
            issuer_url=settings.public_base_url if oauth_server else (settings.oidc_issuer or settings.public_base_url),
            resource_server_url=settings.mcp_resource_url,
            required_scopes=list(settings.mcp_required_scopes) or None,
            validate_token_resource=False,  # audience is checked by the OIDC verifier
        ),
    )

    @mcp.tool()
    def create_artifact(title: str, kind: str, body: str, visibility: str = "private",
                        description: str | None = None, source_question: str | None = None,
                        tags: list[str] | None = None, sources: list[str] | None = None,
                        data_as_of: str | None = None, sensitive: bool | None = None) -> dict[str, Any]:
        """Publish an artifact and return its link. kind: 'html' (self-contained, inline JS allowed,
        rendered in a sandbox with no network access), 'markdown' or 'text'. visibility: 'private'
        (default) or 'shared' (whole organisation, publisher group only). The owner is the
        authenticated user; do not pass one. Body limit about 800 KB. source_question: the user's
        original question (improves search). Returns id, url, version and warnings."""
        p = _principal()
        art = _call(service.create, p, title=title, kind=kind, body=body, visibility=visibility,
                    metadata=_meta(description=description, source_question=source_question, tags=tags,
                                   sources=sources, data_as_of=data_as_of, sensitive=sensitive),
                    via="mcp")
        return {"id": art["id"], "url": art["url"], "title": art["title"], "version": art["current_version"],
                "visibility": art["visibility"], "warnings": art["warnings"]}

    @mcp.tool()
    def update_artifact(id: str, body: str | None = None, title: str | None = None,
                        description: str | None = None, source_question: str | None = None,
                        tags: list[str] | None = None, sources: list[str] | None = None,
                        data_as_of: str | None = None, sensitive: bool | None = None) -> dict[str, Any]:
        """Update an artifact you own. body creates a new retained version (the link then shows it);
        title renames without a new version; metadata fields update in place."""
        p = _principal()
        meta = _meta(description=description, source_question=source_question, tags=tags, sources=sources,
                     data_as_of=data_as_of, sensitive=sensitive)
        if body is None and title is None and not meta:
            raise ToolError("pass body, title or a metadata field")
        art = _call(service.update, p, id, body=body, title=title, metadata=meta)
        return {"id": art["id"], "url": art["url"], "title": art["title"], "version": art["current_version"],
                "warnings": art["warnings"]}

    @mcp.tool()
    def search_artifacts(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find existing artifacts similar to a question (semantic search on title, description and
        original question). Returns metadata and links only, limited to artifacts you may open."""
        p = _principal()
        return _call(service.search, p, query, limit)

    @mcp.tool()
    def get_artifact(id: str) -> dict[str, Any]:
        """Return an artifact's metadata and current body, only if you may open it (to rerun or adapt
        an existing analysis)."""
        p = _principal()
        art = _call(service.get_with_body, p, id)
        keep = ("id", "title", "kind", "owner", "owner_name", "description", "source_question", "tags",
                "sources", "data_as_of", "visibility", "current_version", "updated_at", "url", "access", "body")
        return {k: art.get(k) for k in keep}

    @mcp.tool()
    def share_artifact(id: str, visibility: str | None = None, add: list[str] | None = None,
                       remove: list[str] | None = None) -> dict[str, Any]:
        """Change who can open an artifact you own. visibility: 'private' or 'shared' (whole
        organisation, publisher group only). add / remove: emails of specific people (max 100)."""
        p = _principal()
        art = _call(service.share, p, id, visibility=visibility, add=add, remove=remove)
        return {"id": art["id"], "url": art["url"], "visibility": art["visibility"],
                "shared_with": art.get("shared_with", [])}

    return mcp


def mcp_http_app(settings, mcp: MCPServer):
    hosts = [settings.public_host, "localhost:*", "127.0.0.1:*", "testserver"]
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[settings.public_base_url, settings.app_origin, "http://localhost:*",
                             "http://127.0.0.1:*"],
        ),
    )
