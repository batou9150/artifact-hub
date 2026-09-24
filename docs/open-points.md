# Open points

## 1. MCP client registration: DCR or CIMD (decision needed)

The MCP endpoint is a spec-compliant **resource server**: it publishes RFC 9728 protected
resource metadata (`/.well-known/oauth-protected-resource/mcp`), answers `401` with
`WWW-Authenticate: Bearer resource_metadata=...`, and validates bearer tokens issued by the
organisation's IdP (issuer, audience, JWKS). The metadata names the IdP as the
authorization server.

What it does **not** do is act as an authorization server. MCP clients discover the
authorization server and then need a client registration:

* **Dynamic Client Registration (RFC 7591)**: Entra ID does not support it; Okta supports
  it only with specific configuration and admin rights. Rarely acceptable in an enterprise
  tenant.
* **Client ID Metadata Documents (CIMD)**: the client identifies itself with an HTTPS URL
  pointing to its metadata. Newer; support on both the MCP client side and the IdP side must
  be verified for the target client.
* **Pre-registered client**: the MCP client is registered once in the IdP; works when the
  client lets an administrator configure a client id (and possibly a secret).

Recommended path: reuse the **authorization-server facade** of the separate MCP server
project (it implements DCR / CIMD towards MCP clients, delegates user login to the IdP and
issues its own short-lived tokens). The hub then accepts that facade's tokens: set
`OIDC_ISSUER` / `OIDC_AUDIENCES` to the facade's issuer and audience, and the
authorization server in the metadata follows automatically. Until then, validate the flow
with a pre-registered client on a test tenant.

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
