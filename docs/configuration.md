# Configuration

All settings are environment variables read once at startup (`backend/artifact_hub/config.py`).

| Variable | Default | Notes |
|---|---|---|
| `AUTH_MODE` | `dev` | `dev` (fake identities) or `oidc`. `dev` is refused when `ENVIRONMENT=prod`. |
| `ENVIRONMENT` | `local` | `local`, `test`, `dev`, `prod`. |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Public URL of this service: sandbox URLs, MCP resource identifier. |
| `APP_ORIGIN` | `http://localhost:5173` | Origin of the web app: share links and CSP `frame-ancestors`. Same as `PUBLIC_BASE_URL` in the container. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma list; empty in production (same origin). |
| `OIDC_ISSUER` | | Exact `iss` value (no trailing slash). |
| `OIDC_AUDIENCES` | | Comma list of accepted `aud` values. |
| `OIDC_CLIENT_ID` | | Public SPA client (no secret). |
| `OIDC_CLIENT_SECRET` | | Only for clients that need a secret at the token endpoint (Google "Web application"). Enables `POST /api/auth/token`, which relays the SPA's code + PKCE verifier to the IdP with the secret added server side. From Secret Manager. |
| `OIDC_SCOPES` | `openid profile email` | Scopes requested by the SPA. |
| `OIDC_UI_TOKEN` | `access` | Token the SPA sends: `access` (JWT access token for this API) or `id`. |
| `OIDC_JWKS_URI` | discovered | Override of the discovered `jwks_uri`. |
| `OIDC_TOKENINFO_URL` | | Introspection endpoint for opaque access tokens (MCP clients). Google: `https://oauth2.googleapis.com/tokeninfo`; `aud` must be in `OIDC_AUDIENCES`. |
| `OIDC_EMAIL_CLAIMS` | `email,preferred_username,upn` | First claim holding an email becomes the identity key. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim holding group names or ids (`roles` works too). |
| `OIDC_NAME_CLAIM` | `name` | Display name. |
| `MCP_REQUIRED_SCOPES` | | Scopes the MCP endpoint requires (advertised in the protected resource metadata). |
| `OAUTH_SERVER` | `false` | Authorization server for MCP clients (CIMD). Needs `AUTH_MODE=oidc`, the IdP client (`OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET` for Google) with redirect URI `<PUBLIC_BASE_URL>/oauth/callback`. |
| `OAUTH_SIGNING_KEY` | | EC P-256 private key (PEM) signing the MCP access tokens. Secret Manager. |
| `OAUTH_ALLOWED_CLIENT_HOSTS` | | Comma list of hosts allowed to serve client metadata documents. Empty = any public host. |
| `OAUTH_ACCESS_TOKEN_TTL` / `OAUTH_REFRESH_TOKEN_TTL` | `3600` / `2592000` | Seconds. |
| `PUBLISHER_GROUP` | `artifact-publishers` | May share with the whole organisation. |
| `ADMIN_GROUP` | `artifact-admins` | Administrators: the admin console, moderation and deletion of any artifact (implies publisher). |
| `ADMIN_EMAILS` | | Comma list of administrator emails, same rights as `ADMIN_GROUP`. For IdPs whose tokens carry no groups (Google). Checked on every request, so removing an address takes effect at once, MCP tokens included. |
| `ALLOWED_EMAIL_DOMAINS` | | Comma list; restricts sign-in and invitees. Empty = any identity the IdP vouches for. |
| `SENSITIVE_ORG_SHARE` | `deny` | `deny`: a sensitive artifact can never be shared org-wide. `allow`: flag is informational. |
| `STORE_BACKEND` | `memory` | `memory` or `firestore` (emulator via `FIRESTORE_EMULATOR_HOST`). |
| `GOOGLE_CLOUD_PROJECT` | `demo-artifact-hub` | Firestore / Vertex project. |
| `FIRESTORE_DATABASE` | `(default)` | Named database if not the default one. |
| `EMBEDDING_PROVIDER` | `fake` | `fake` or `vertex` (needs `pip install ".[vertex]"`). |
| `VERTEX_LOCATION` / `VERTEX_EMBEDDING_MODEL` | `europe-west1` / `text-embedding-005` | |
| `SEARCH_MIN_SCORE` | `0.15` | Cosine floor for search results (tune per embedding model; Vertex scores are higher on average). |
| `RENDER_TICKET_SECRET` | dev value | HMAC key for sandbox tickets. Required and random in `oidc` mode (Secret Manager). |
| `RENDER_TICKET_TTL_SECONDS` | `300` | |
| `STATIC_DIR` | | Built SPA directory served at `/` (set in the container). |
| `DEV_USERS` | | Extra fake identities for the dev login picker. |

Frontend build variable: `VITE_API_BASE` (empty = same origin; defaults to
`http://localhost:8080` under `vite dev`). All other frontend settings come from
`GET /api/config` at runtime, so one image serves every environment.

## Identity provider setup

Register **one SPA application** (public client, authorization code + PKCE, redirect URI
`<APP_ORIGIN>/callback`) and make sure the token the SPA sends to the API carries the
email and the groups.

### Microsoft Entra ID

* App registration, platform "Single-page application", redirect `<APP_ORIGIN>/callback`.
* "Expose an API": application ID URI `api://artifact-hub`, scope `access`.
* Token configuration: add the `groups` claim, **restricted to groups assigned to the
  application** (Entra ID emits an overage marker instead of the claim beyond ~200 groups).
  Group values are object ids: use the ids in `PUBLISHER_GROUP` / `ADMIN_GROUP`, or emit
  app roles and set `OIDC_GROUPS_CLAIM=roles`.
* Settings: `OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0`,
  `OIDC_AUDIENCES=api://artifact-hub` (and the app's client id if v1 tokens appear),
  `OIDC_SCOPES=openid profile email api://artifact-hub/access`, `OIDC_UI_TOKEN=access`.
  The email is usually in `preferred_username`.

### Okta

* OIDC SPA app integration, PKCE, redirect `<APP_ORIGIN>/callback`.
* Custom authorization server (for example `default`) with audience `api://artifact-hub`,
  a `groups` claim in the access token (filter, for example "starts with artifact-").
* Settings: `OIDC_ISSUER=https://<org>.okta.com/oauth2/<server-id>`,
  `OIDC_AUDIENCES=api://artifact-hub`, `OIDC_UI_TOKEN=access`.

### Google (Workspace / Cloud Identity)

* OAuth client of type "Web application", authorized redirect URI `<APP_ORIGIN>/callback`.
  Google refuses the code exchange without the client secret even with PKCE, so set
  `OIDC_CLIENT_SECRET` (Terraform: `oidc_client_secret_secret`); the API performs the
  exchange and the secret never reaches the browser.
* Google access tokens are opaque: set `OIDC_UI_TOKEN=id`, `OIDC_AUDIENCES=<client id>`,
  `OIDC_ISSUER=https://accounts.google.com`.
* MCP clients: set `OAUTH_SERVER=true` and register `<PUBLIC_BASE_URL>/oauth/callback` on
  the same OAuth client. MCP clients then connect with the `/mcp` URL alone (CIMD).
  Without it, pre-registered clients can send Google's opaque access tokens if
  `OIDC_TOKENINFO_URL` is set.
* Google ID tokens carry no groups: groups need the Cloud Identity Groups API or a broker;
  until then use `ALLOWED_EMAIL_DOMAINS` and assign publishers through an IdP broker.
