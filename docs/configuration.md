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
| `OIDC_SCOPES` | `openid profile email` | Scopes requested by the SPA. |
| `OIDC_UI_TOKEN` | `access` | Token the SPA sends: `access` (JWT access token for this API) or `id`. |
| `OIDC_JWKS_URI` | discovered | Override of the discovered `jwks_uri`. |
| `OIDC_EMAIL_CLAIMS` | `email,preferred_username,upn` | First claim holding an email becomes the identity key. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim holding group names or ids (`roles` works too). |
| `OIDC_NAME_CLAIM` | `name` | Display name. |
| `MCP_REQUIRED_SCOPES` | | Scopes the MCP endpoint requires (advertised in the protected resource metadata). |
| `PUBLISHER_GROUP` | `artifact-publishers` | May share with the whole organisation. |
| `ADMIN_GROUP` | `artifact-admins` | May delete any artifact (implies publisher). |
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

* OAuth client of type "Web application". To verify: whether Google accepts the code
  exchange without a client secret for your client type; if it does not, use an IdP broker
  (or Entra ID / Okta) so the SPA stays a public client with no secret in the browser.
* Google access tokens are opaque: set `OIDC_UI_TOKEN=id`, `OIDC_AUDIENCES=<client id>`,
  `OIDC_ISSUER=https://accounts.google.com`.
* Google ID tokens carry no groups: groups need the Cloud Identity Groups API or a broker;
  until then use `ALLOWED_EMAIL_DOMAINS` and assign publishers through an IdP broker.
