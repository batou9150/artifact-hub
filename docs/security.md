# Security model

## Rendering untrusted content

Artifacts are arbitrary HTML with JavaScript. They are rendered so that the script runs
but can neither reach the application nor send data anywhere.

**Response headers on every `/a/*` response (success and refusal):**

| Header | Value | Purpose |
|---|---|---|
| `Content-Security-Policy` | `default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; frame-ancestors <APP_ORIGIN>; base-uri 'none'; child-src blob:; worker-src blob:; sandbox allow-scripts` | deny by default, no egress, framing pinned to the app, opaque origin even on direct load |
| `X-Content-Type-Options` | `nosniff` | no MIME sniffing |
| `Cross-Origin-Opener-Policy` | `same-origin` | no window handle to or from other origins |
| `Cross-Origin-Resource-Policy` | `same-origin` | cannot be embedded as a resource elsewhere |
| `X-DNS-Prefetch-Control` | `off` | closes the speculative DNS side channel |
| `Referrer-Policy` | `no-referrer` | the URL carries a render ticket |
| `Cache-Control` | `private, no-store` | no shared caching of private content |

**Iframe:** `sandbox="allow-scripts"` only (`ArtifactFrame.tsx`, unit-tested).
`allow-same-origin` is never set: combined with `allow-scripts` it lets the frame remove
its own sandbox. No `allow-popups`, `allow-forms`, `allow-top-navigation`.

**Markdown and text** are converted server-side. Everything is HTML-escaped first; links
keep an `href` only for `http(s):` and `mailto:`; images keep their alt text only.

**Publish-time linter:** HTML that loads scripts, styles, images or data from the network
gets a warning (it would render broken, since the sandbox blocks egress).

### Residual risks

* **Frame self-navigation.** No browser enforces a CSP directive for a frame navigating
  itself, so script in an HTML artifact can set `location` to an external URL and leak
  data in the query string. `fetch`, XHR, WebSocket, beacons, forms, images, scripts,
  styles and popups are blocked. Mitigations in place: the viewer detects a second `load`
  of the frame and warns the user; artifacts are private by default and org-wide sharing is
  restricted to a publisher group. Treat "no data can leave" as "no data can leave without
  navigating the frame away", and keep the author accountable (attribution is verified).
* **The body embeds data.** Sharing an artifact shares whatever data its author could see.
  Hence private by default, per-person grants, org-wide sharing restricted to the publisher
  group, the `sensitive` flag (org-wide sharing refused, and flagging a published artifact
  withdraws its org link), and admin deletion.
* **Opaque origin relies on the browser.** A browser bug in CSP `sandbox` enforcement
  would expose the app origin. Serving `/a/*` from a dedicated host removes that exposure
  (see architecture.md).

## Authentication

* **Web app:** OIDC authorization code + PKCE (`oidc-client-ts`), tokens in
  `sessionStorage` of the app origin (unreachable from the opaque-origin frame).
* **API and MCP:** bearer tokens validated locally: signature against the IdP JWKS
  (discovered, cached, rotated), exact `iss`, `aud` in the configured audiences, `exp` /
  `nbf`, allowed algorithms (RS/ES/PS; `none` rejected), explicit `email_verified: false`
  rejected, optional email domain allow-list.
* **Sandbox:** short-lived HMAC render tickets bound to (artifact, email, version, expiry),
  plus a fresh access check on every request. A bearer header is also accepted (for tools).
* **Dev auth mode:** fake `dev:<email>` tokens, for local runs and tests only. Refused
  when `ENVIRONMENT=prod`; OIDC mode refuses the default ticket secret; the UI shows a
  permanent banner.

## MCP authorization server

Enabled with `OAUTH_SERVER=true` (`artifact_hub/oauth`).

* **Client registration: CIMD only.** `client_id` is an https URL (default port, a path,
  no userinfo / fragment / dot segments). Its document is fetched with SSRF guards: every
  resolved address must be publicly routable, the request is sent to the checked address
  with TLS verified against the host name (no DNS rebinding), no redirects, 5 s, 5 KiB.
  The document must name itself, list `redirect_uris` (https, http loopback, or a
  private-use scheme), carry no secret, and be a public client. Optional host allow-list
  `OAUTH_ALLOWED_CLIENT_HOSTS`.
* **Authorize:** errors about the client or redirect URI are shown, never redirected;
  exact redirect match (loopback port free, RFC 8252); PKCE S256 required; `resource`
  must be the `/mcp` URL; responses carry `iss` (RFC 9207).
* **Sign-in and consent:** the user signs in at the IdP (code + PKCE + nonce, client
  secret server side, ID token verified like any IdP token, domain allow-list applied).
  The pending request is bound to the starting browser by an `HttpOnly`, `SameSite=Lax`,
  `__Host-` cookie. The consent page shows the account, the client_id and its host, and
  where the browser returns; it has a per-request CSRF token, a strict CSP and cannot be
  framed. Consent is asked on every authorization.
* **Tokens:** access tokens are ES256 JWTs (`typ: at+jwt`), `aud` = the `/mcp` URL, 1 h;
  the REST API does not accept them. Refresh tokens are opaque, stored hashed, single use
  and rotated; replaying a code or a rotated refresh token revokes the whole sign-in.
  Refreshing re-applies the domain allow-list. Codes live 60 s, pending requests 10 min;
  Firestore TTL cleans up.
* **Residual:** consent is the defence against a malicious client that registers the
  legitimate loopback redirect of another app; users must read what they approve.

## Authorization matrix

| Action | Owner | Invited (`shared_with`) | Organisation (`visibility=shared`) | Administrator | Anyone else |
|---|---|---|---|---|---|
| Open current version / body | yes | yes | yes | only if also granted | 404 |
| Open a prior version | yes | 404 | 404 | 404 | 404 |
| Edit, rename, metadata, revert, versions list | yes | 403 | 403 | as its other grants | 404 |
| Share with people / make private | yes | 403 | 403 | as its other grants | 404 |
| Share with the whole organisation | publisher group only, never if `sensitive` (default policy) | 403 | 403 | yes (admin implies publisher) | 404 |
| Delete | yes | 403 | 403 | yes | 404 |
| List every artifact's metadata, totals (`/api/admin/*`) | 403 | 403 | 403 | yes | 403 |
| Withdraw org-wide sharing, remove invitees, flag / unflag sensitive | as above | 403 | 403 | yes, recorded on the artifact | 403 |
| Appear in search results | yes | yes | yes | only if also granted | never |

404 is used whenever the caller cannot open the artifact, so a private artifact is
indistinguishable from an absent one; 403 only where existence is already known to the
caller. Search pre-filters by `can_open` and re-checks every hit, and never returns
bodies or vectors. Owner-only fields (`shared_with`, `viewers`, counts) are stripped for
everyone else.

Administrators are the `ADMIN_GROUP` members plus the `ADMIN_EMAILS` addresses. The admin
console lists metadata only (title, owner, kind, size, sharing summary, view count): never a
body, the invite list or who viewed it, so a private artifact stays private from
administrators too; they open it like anyone else, only when it is shared with them.
Moderation never changes a body. The last action (who, what, when, note) is stored on the
artifact and shown to its owner, and every action and admin deletion is logged as an
`artifact_moderated` / `artifact_deleted` event with `by_admin`.

## Data at rest

* Firestore is only reachable by the service account (client rules are deny-all).
* The render-ticket key lives in Secret Manager; no secret is committed.
* The runtime service account has `roles/datastore.user` (and `roles/aiplatform.user` for
  Vertex embeddings), nothing else.
