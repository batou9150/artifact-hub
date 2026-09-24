"""Runtime configuration, read once from environment variables.

Every knob has a safe default for local development. Production deployments set
the OIDC_* variables and AUTH_MODE=oidc; see docs/configuration.md for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # "dev" accepts fake identities (Bearer dev:<email>) and MUST never run in
    # production; "oidc" validates real IdP tokens against issuer + audience + JWKS.
    auth_mode: str = "dev"
    environment: str = "local"  # local | test | dev | prod

    # Public URL of this service (scheme + host, no trailing slash). Used for share
    # links, the MCP resource identifier and the CSP frame-ancestors directive.
    public_base_url: str = "http://localhost:8080"
    # Origin of the web app that frames the sandbox. Same as public_base_url when the
    # SPA is served by this service; the Vite dev server in local development.
    app_origin: str = "http://localhost:5173"
    # Extra origins allowed by CORS (the Vite dev server in local development).
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

    # OIDC (Entra ID / Okta / Google). The SPA uses auth code + PKCE with
    # oidc_client_id; the API accepts tokens whose `aud` is in oidc_audiences.
    oidc_issuer: str = ""
    oidc_audiences: tuple[str, ...] = ()
    oidc_client_id: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_jwks_uri: str = ""  # optional override; discovered from the issuer otherwise
    oidc_email_claims: tuple[str, ...] = ("email", "preferred_username", "upn")
    oidc_groups_claim: str = "groups"
    oidc_name_claim: str = "name"
    # Which token the SPA sends to the API: "access" when the IdP issues JWT access
    # tokens for this API (Entra ID custom API scope, Okta custom authorization
    # server), "id" when it does not (Google issues opaque access tokens; its ID
    # token has aud = client id).
    oidc_ui_token: str = "access"
    # Scopes the MCP endpoint requires on the access token (empty = none required).
    mcp_required_scopes: tuple[str, ...] = ()

    # Authorization policy.
    publisher_group: str = "artifact-publishers"  # may share org-wide
    admin_group: str = "artifact-admins"          # may delete any artifact
    allowed_email_domains: tuple[str, ...] = ()   # empty = any verified identity
    # "deny": an artifact flagged sensitive can never be shared org-wide.
    # "allow": the flag is informational only.
    sensitive_org_share: str = "deny"

    # Storage.
    store_backend: str = "memory"  # memory | firestore
    gcp_project: str = "demo-artifact-hub"
    firestore_database: str = "(default)"

    # Search.
    embedding_provider: str = "fake"  # fake | vertex
    vertex_location: str = "europe-west1"
    vertex_embedding_model: str = "text-embedding-005"
    search_min_score: float = 0.15

    # Sandbox render tickets (HMAC key). Must be set to a random secret in any
    # shared environment; the dev default is only acceptable locally.
    render_ticket_secret: str = "dev-only-render-ticket-secret-change-me"
    render_ticket_ttl_seconds: int = 300

    # Directory holding the built SPA (served at / when present).
    static_dir: str = ""

    dev_users: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mcp_resource_url(self) -> str:
        return f"{self.public_base_url}/mcp"

    @property
    def public_host(self) -> str:
        return urlparse(self.public_base_url).netloc

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        env = os.environ.get
        values = dict(
            auth_mode=env("AUTH_MODE", "dev"),
            environment=env("ENVIRONMENT", "local"),
            public_base_url=env("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
            app_origin=env("APP_ORIGIN", "http://localhost:5173").rstrip("/"),
            cors_origins=_csv(env("CORS_ORIGINS", "http://localhost:5173")),
            oidc_issuer=env("OIDC_ISSUER", "").rstrip("/"),
            oidc_audiences=_csv(env("OIDC_AUDIENCES", "")),
            oidc_client_id=env("OIDC_CLIENT_ID", ""),
            oidc_scopes=env("OIDC_SCOPES", "openid profile email"),
            oidc_jwks_uri=env("OIDC_JWKS_URI", ""),
            oidc_email_claims=_csv(env("OIDC_EMAIL_CLAIMS", "email,preferred_username,upn")),
            oidc_groups_claim=env("OIDC_GROUPS_CLAIM", "groups"),
            oidc_name_claim=env("OIDC_NAME_CLAIM", "name"),
            oidc_ui_token=env("OIDC_UI_TOKEN", "access"),
            mcp_required_scopes=_csv(env("MCP_REQUIRED_SCOPES", "")),
            publisher_group=env("PUBLISHER_GROUP", "artifact-publishers"),
            admin_group=env("ADMIN_GROUP", "artifact-admins"),
            allowed_email_domains=tuple(d.lower().lstrip("@") for d in _csv(env("ALLOWED_EMAIL_DOMAINS", ""))),
            sensitive_org_share=env("SENSITIVE_ORG_SHARE", "deny"),
            store_backend=env("STORE_BACKEND", "memory"),
            gcp_project=env("GOOGLE_CLOUD_PROJECT", "demo-artifact-hub"),
            firestore_database=env("FIRESTORE_DATABASE", "(default)"),
            embedding_provider=env("EMBEDDING_PROVIDER", "fake"),
            vertex_location=env("VERTEX_LOCATION", "europe-west1"),
            vertex_embedding_model=env("VERTEX_EMBEDDING_MODEL", "text-embedding-005"),
            search_min_score=float(env("SEARCH_MIN_SCORE", "0.15")),
            render_ticket_secret=env("RENDER_TICKET_SECRET", "dev-only-render-ticket-secret-change-me"),
            render_ticket_ttl_seconds=int(env("RENDER_TICKET_TTL_SECONDS", "300")),
            static_dir=env("STATIC_DIR", ""),
            dev_users=_csv(env("DEV_USERS", "")),
        )
        values.update(overrides)
        settings = cls(**values)
        settings.check()
        return settings

    def check(self) -> None:
        """Refuse configurations that would be unsafe outside a laptop."""
        if self.auth_mode not in {"dev", "oidc"}:
            raise ValueError(f"AUTH_MODE must be 'dev' or 'oidc', got {self.auth_mode!r}")
        if self.auth_mode == "dev" and self.environment == "prod":
            raise ValueError("AUTH_MODE=dev is refused when ENVIRONMENT=prod")
        if self.auth_mode == "oidc":
            if not self.oidc_issuer or not self.oidc_audiences:
                raise ValueError("AUTH_MODE=oidc requires OIDC_ISSUER and OIDC_AUDIENCES")
            if self.render_ticket_secret.startswith("dev-only"):
                raise ValueError("AUTH_MODE=oidc requires a real RENDER_TICKET_SECRET")
        if self.sensitive_org_share not in {"deny", "allow"}:
            raise ValueError("SENSITIVE_ORG_SHARE must be 'deny' or 'allow'")
