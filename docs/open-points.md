# Open points

## 1. MCP client registration: CIMD (implemented)

With `OAUTH_SERVER=true` this service is also the **authorization server** of its MCP
endpoint (`artifact_hub/oauth`, MCP authorization spec 2025-11-25): clients register
through **Client ID Metadata Documents**, the user signs in at the IdP (confidential
client, server side), approves the client on a consent page, and the service issues its
own tokens (JWT, `aud` = the `/mcp` URL, 1 h; rotating refresh tokens, 30 days). The
protected resource metadata then names this service as the authorization server. See
docs/security.md, "MCP authorization server".

Still open:
* **DCR (RFC 7591)** is not implemented; clients that support neither CIMD nor
  pre-registration cannot connect.
* **Signing key rotation**: one key (`OAUTH_SIGNING_KEY`); rotating it invalidates the
  access tokens in flight (at most 1 h). A second, verify-only key would make it seamless.
* **Grant management UI**: users cannot yet list or revoke connected clients themselves
  (deleting the `family:*` documents in `oauth_grants` revokes a sign-in).
* Without OAUTH_SERVER, IdP-issued tokens are accepted as before (pre-registered clients).

Also to confirm with the chosen IdP: tokens with the right **audience** for this resource
(RFC 8707 resource indicators are not honoured by every IdP; `validate_token_resource` is
disabled and the audience check lives in the OIDC verifier).

## 2. IAP vs application-level OIDC

The reference implementation sat behind Identity-Aware Proxy with Google identities.
This hub uses application-level OIDC instead, because:

* IAP only speaks Google identities (or workforce identity federation for external IdPs,
  which adds configuration and still yields a Google-shaped principal);
* MCP clients call from outside the organisation with bearer tokens, which IAP does not
  pass through for arbitrary OAuth clients;
* the application needs the IdP groups (publisher and admin groups) in every request.

IAP remains an option in front of the **web app only** (defense in depth) if the
organisation already runs it with workforce identity federation; the MCP path must then
bypass IAP (separate backend or path rule), and the app must still validate its own
tokens. Consequence of the OIDC choice: the sandbox iframe cannot rely on an edge-injected
identity header, hence the short-lived render tickets.

## 3. Large bodies (Cloud Storage)

Bodies are stored inline in Firestore under an 800,000-byte cap. An HTML artifact that
embeds a large dataset can exceed it. Extension plan (not built):

* store `versions/{n}.body_ref = gs://<bucket>/<id>/<n>` above a threshold (or always);
* the store keeps the same API; `get_version_body` reads from GCS; delete removes objects;
* the Terraform sketch already creates an optional private bucket (`enable_gcs_bodies`);
* keep a (higher) cap anyway, for rendering performance and cost.

Decide once the real size distribution of artifacts is known.

## 4. Other points

* **Frame self-navigation** is not preventable by CSP (see security.md). Decide whether the
  viewer warning is enough or whether HTML artifacts should be further constrained.
* **Sensitive data guard**: the `sensitive` flag is set by the author or the publishing
  agent. Setting it automatically (for example when the agent's session touched columns
  under a data-classification policy) belongs to the publishing agent / MCP server that
  knows the queried columns; the hub only enforces the flag.
* **Directory for autocomplete**: only people who already signed in are suggested (plus any
  typed address). A Microsoft Graph or Okta Users lookup would cover everyone.
* **Search at scale**: switch `BruteForceIndex` for Firestore vector search or BigQuery
  `VECTOR_SEARCH` past a few thousand artifacts (see architecture.md).
* **Group changes** take effect at the next token (no server-side session); revocation of
  access to a single artifact is immediate (tickets re-check access).
* **Per-user quotas** (artifact count, total bytes) are not enforced.
* **Rate limiting** is not built in; use Cloud Armor or an API gateway.
* **Retention / expiry** of artifacts is not implemented.
