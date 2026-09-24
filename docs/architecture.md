# Architecture

## Components

```
Browser (web app, OIDC session)            MCP client (agent acting for a user)
   |  /api/* bearer token                      |  /mcp bearer token
   |  <iframe sandbox="allow-scripts"          |
   |     src="/a/{id}?t=<render ticket>">      |
   v                                           v
+---------------------------- Cloud Run service ----------------------------+
|  api.py        REST API, SPA static files, sandbox endpoint /a/*          |
|  mcp_server.py MCP (streamable HTTP, stateless) + RFC 9728 metadata       |
|        \                         /                                        |
|         service.py  (use cases, one per action, shared by both doors)     |
|         access.py   (who may open / manage / delete / share org-wide)     |
|         store/      (single writer: validation, versions, caps, ids)      |
|         search/     (embeddings + vector index, filtered by access)       |
+---------------------------------------------------------------------------+
        |                                   |
   Firestore (artifacts, versions,     Vertex AI text embeddings
   artifact_users)                     (fake provider locally)
```

Every request is resolved to a `Principal` (verified email, name, groups) before it
touches the store. The REST API and the MCP tools call the same `ArtifactService`
methods, so the rules (attribution, caps, org-wide restriction, 404 for private
artifacts) cannot diverge between the two doors.

## Data model

```
artifacts/{id}                      id = 24 random bytes, base64url (192 bits)
  owner, owner_name                 verified identity of the creator, never client-supplied
  title, kind                       kind in {html, markdown, text}
  visibility                        private | shared   (shared = whole organisation)
  shared_with[]                     per-person grants, lowercased, max 100
  current_version, size
  description, source_question, tags[], sources[], data_as_of, sensitive
  viewers[], view_count, last_viewed_at
  embedding[], embedding_model, search_hash
  created_at, updated_at, created_via (web | mcp)
artifacts/{id}/versions/{n}         n, body (<= 800,000 bytes), size, author, created_at
artifact_users/{email}              email, name, last_seen_at   (share autocomplete)
```

* **Versioning**: an update appends version N+1 in a transaction and moves the pointer; a
  revert copies version k into a new version (history is never rewritten); a rename or a
  metadata change never creates a version.
* **Body cap**: 800,000 bytes (UTF-8), under Firestore's 1 MiB document limit. Over-cap
  writes are rejected with HTTP 413 / an MCP tool error; nothing is written.
* **Concurrency**: concurrent updates never lose or duplicate a version; a writer that
  exhausts the transaction retries gets HTTP 409 (safe to retry).
* **Views**: an open by anyone other than the owner adds them to `viewers` (distinct) and
  increments `view_count`; it does not touch `updated_at`. Each open is also emitted as an
  `artifact_viewed` JSON log line for the usage dashboard (see below).

## Request flows

**Open an artifact in the web app**

1. `GET /api/artifacts/{id}` with the OIDC bearer token. The API checks `can_open`; if the
   caller may not open it, the answer is the same 404 as for an absent id.
2. The response carries the metadata and a `render_url` =
   `/a/{id}?t=<ticket>`. The ticket is an HMAC over (artifact id, caller email, version,
   expiry, default 5 minutes).
3. The viewer embeds `render_url` in `<iframe sandbox="allow-scripts">`. An iframe request
   cannot carry the bearer token, hence the ticket.
4. `/a/{id}` verifies the ticket **and re-checks access against current state** (a revoked
   grant stops working immediately), then serves the body with the isolating headers
   (see [security.md](security.md)). Markdown and text are rendered server-side.
5. The viewer calls `POST /api/artifacts/{id}/views` (ignored for the owner).

**Publish from an agent (MCP)**

1. The MCP client calls `POST /mcp` with the user's bearer token (same IdP and validator as
   the REST API). Without a token: `401` + `WWW-Authenticate: Bearer resource_metadata=...`.
2. `create_artifact(title, kind, body, ..., source_question)` stores the artifact with
   `owner` = the verified human; the tool has no owner parameter.
3. The tool returns the link, the version and any warning (for instance external resources
   that the sandbox will block).

MCP tools: `create_artifact`, `update_artifact`, `search_artifacts` (metadata and links
only), `get_artifact` (body only if the caller may open it), `share_artifact`.

## Why one service (API + sandbox + MCP + SPA)

The reference design first put the sandbox on a separate host, then moved it under `/a/*`
on the application's own origin. The isolation does not come from the host name: it comes
from the **response header** `Content-Security-Policy: ... sandbox allow-scripts` (no
`allow-same-origin`), which makes the browser run the document in an **opaque origin**
even when it is opened directly, plus the iframe `sandbox="allow-scripts"` attribute. An
opaque-origin document cannot read the app origin's cookies, `localStorage`,
`sessionStorage` (where the OIDC tokens live) or DOM, and cannot call the API
(`connect-src 'none'`; the API also requires a bearer token, never a cookie).

We keep that choice, as a single Cloud Run service: one image, one URL, no extra DNS or
certificate, no cross-service trust. The browser test in the repository history confirmed
that cookies, `localStorage`, parent DOM access, `fetch` and image beacons are all blocked
inside the frame.

When a dedicated host is available (for example `usercontent.<domain>` with its own
certificate), serving `/a/*` there adds defense in depth against browser bugs in sandbox
enforcement: set `PUBLIC_BASE_URL` for the sandbox host and keep `APP_ORIGIN` for the web
app (the code already distinguishes the two; `frame-ancestors` is pinned to `APP_ORIGIN`).

## Search

* Indexed text: title + description + source question. **Never the body**, which may
  contain data the searcher is not allowed to see.
* The vector is computed by the store whenever that text changes (hash-guarded) and stored
  on the artifact document, so every instance and every index option can read it.
* Providers: `FakeEmbeddingProvider` (deterministic hashed word and trigram features, dev
  and tests), `VertexEmbeddingProvider` (`text-embedding-005` by default,
  `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task types).
* Index: `BruteForceIndex` pre-filters the corpus with `access.can_open`, then ranks by
  cosine. Stateless and correct across instances; fine up to a few thousand artifacts.
* `SearchService` re-checks `can_open` on every hit whatever the index (an index bug can
  drop a result, never leak one).

Production options behind the same `VectorIndex` protocol:

| Option | How | When |
|---|---|---|
| Firestore vector search | store `embedding` as a `Vector`, create a vector index, run `find_nearest` with three pre-filters the caller is entitled to (`owner == me`, `shared_with array-contains me`, `visibility == 'shared'`) and merge | single datastore, up to tens of thousands of artifacts |
| BigQuery `VECTOR_SEARCH` | export artifacts + vectors to a table (Firestore export or write-through), query `VECTOR_SEARCH(TABLE t, 'embedding', (SELECT @q AS embedding), top_k => 50)` with a `WHERE` on owner / shared_with / visibility | large corpora, joins with usage analytics |

Switching the embedding model changes `embedding_model`; run `store.reindex(id)` over the
corpus (vectors from another model are ignored by the index, never mixed).

## Usage events

`service.py` emits one JSON line per event on stdout (`artifact_created`,
`artifact_updated`, `artifact_shared`, `artifact_reverted`, `artifact_deleted`,
`artifact_viewed`). Cloud Logging stores them as `jsonPayload`; the optional Terraform log
sink routes `jsonPayload.event=~"^artifact_"` to a partitioned BigQuery dataset that a
dashboard can query (publications per week, opens per artifact, reuse by people other than
the author).
