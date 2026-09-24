"""The artifact store: the single writer for artifacts, their versions and the
people directory. The REST API and the MCP tools both land here, so attribution,
timestamps, versioning, limits and id generation cannot be bypassed by either door.

Data model (same shape in every backend):

  artifacts/{id}                 metadata, never the body
    owner, owner_name            verified identity of the creator (never client-supplied)
    title, kind                  kind in {html, markdown, text}
    visibility                   private | shared  (shared = whole organisation, by link)
    shared_with                  per-person grants (lowercased emails, max 100)
    current_version, size
    description, source_question, tags, sources, data_as_of, sensitive
    viewers, view_count, last_viewed_at   distinct non-owner openers
    embedding, embedding_model, search_hash   search vector (title + description + question)
    created_at, updated_at, created_via
  artifacts/{id}/versions/{n}    n, body, size, author, created_at

Backends implement a handful of primitives (`_get`, `_create`, `_append_version`,
`_patch`, ...); every rule lives in this base class so the in-memory and Firestore
backends cannot drift.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from abc import ABC, abstractmethod
from datetime import datetime, timezone

VALID_KINDS = ("html", "markdown", "text")
VALID_VISIBILITY = ("private", "shared")

# Firestore caps a document at 1 MiB and the body lives inline on the version doc,
# so the cap stays well under it. Bytes, not characters: a multibyte paste counts
# against the real ceiling. Larger bodies are rejected, never truncated (see
# docs/architecture.md for the Cloud Storage extension).
MAX_BODY_BYTES = 800_000
MAX_SHARED_WITH = 100
MAX_TITLE = 200
MAX_DESCRIPTION = 2_000
MAX_QUESTION = 2_000
MAX_TAGS = 20
MAX_TAG_LEN = 50
MAX_SOURCES = 50

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Fields a caller may never set directly; the store owns them.
SERVER_FIELDS = {
    "id", "owner", "owner_name", "current_version", "size", "viewers", "view_count",
    "last_viewed_at", "embedding", "embedding_model", "search_hash", "created_at",
    "updated_at", "created_via",
}


class ArtifactError(ValueError):
    """Domain error mapped to HTTP 400 / an MCP tool error."""


class ArtifactTooLarge(ArtifactError):
    """Body over MAX_BODY_BYTES (HTTP 413)."""


class ArtifactConflict(ArtifactError):
    """Concurrent writers exhausted the transaction retries (HTTP 409, safe to retry)."""


class ArtifactNotFound(KeyError):
    """Absent artifact or version (HTTP 404)."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    """192 bits of entropy (24 random bytes, 32 url-safe chars). The id is part of
    the shareable link, so it must not be enumerable: guessing one is infeasible."""
    return secrets.token_urlsafe(24)


def body_size(body: str) -> int:
    return len(body.encode("utf-8"))


def check_body(body: str) -> int:
    if not isinstance(body, str) or body == "":
        raise ArtifactError("missing 'body'")
    size = body_size(body)
    if size > MAX_BODY_BYTES:
        raise ArtifactTooLarge(f"body is {size} bytes; the limit is {MAX_BODY_BYTES} bytes")
    return size


def check_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        raise ArtifactError("missing 'title'")
    if len(t) > MAX_TITLE:
        raise ArtifactError(f"title longer than {MAX_TITLE} characters")
    return t


def normalize_emails(emails) -> list[str]:
    out, seen = [], set()
    for e in emails or []:
        if not isinstance(e, str) or not _EMAIL.match(e.strip()):
            raise ArtifactError(f"invalid email in shared_with: {e!r}")
        low = e.strip().lower()
        if low not in seen:
            seen.add(low)
            out.append(low)
    if len(out) > MAX_SHARED_WITH:
        raise ArtifactError(f"shared_with holds {len(out)} emails; the limit is {MAX_SHARED_WITH}")
    return out


def clean_metadata(meta: dict) -> dict:
    """Validate the optional analysis metadata. Unknown keys are dropped."""
    out: dict = {}
    if "description" in meta and meta["description"] is not None:
        d = str(meta["description"]).strip()
        if len(d) > MAX_DESCRIPTION:
            raise ArtifactError(f"description longer than {MAX_DESCRIPTION} characters")
        out["description"] = d
    if "source_question" in meta and meta["source_question"] is not None:
        q = str(meta["source_question"]).strip()
        if len(q) > MAX_QUESTION:
            raise ArtifactError(f"source_question longer than {MAX_QUESTION} characters")
        out["source_question"] = q
    if "tags" in meta and meta["tags"] is not None:
        tags, seen = [], set()
        for t in meta["tags"]:
            t = str(t).strip().lower()
            if not t or t in seen:
                continue
            if len(t) > MAX_TAG_LEN:
                raise ArtifactError(f"tag longer than {MAX_TAG_LEN} characters")
            seen.add(t)
            tags.append(t)
        if len(tags) > MAX_TAGS:
            raise ArtifactError(f"more than {MAX_TAGS} tags")
        out["tags"] = tags
    if "sources" in meta and meta["sources"] is not None:
        sources = [str(s).strip() for s in meta["sources"] if str(s).strip()]
        if len(sources) > MAX_SOURCES:
            raise ArtifactError(f"more than {MAX_SOURCES} sources")
        out["sources"] = sources
    if "data_as_of" in meta and meta["data_as_of"] is not None:
        out["data_as_of"] = str(meta["data_as_of"]).strip()[:64]
    if "sensitive" in meta and meta["sensitive"] is not None:
        out["sensitive"] = bool(meta["sensitive"])
    return out


def search_text(art: dict) -> str:
    """What the search index sees: title, description and source question. Never
    the body (it may embed data the searcher is not allowed to see)."""
    parts = [art.get("title") or "", art.get("description") or "", art.get("source_question") or ""]
    return "\n".join(p for p in parts if p).strip()


def search_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ArtifactStore(ABC):
    """Public API. `embedder` (optional) computes the search vector whenever the
    searchable text changes; the vector is stored on the artifact doc so every
    instance (and the Firestore/BigQuery vector options) can read it."""

    def __init__(self, embedder=None):
        self.embedder = embedder

    # ── primitives each backend implements ──────────────────────────────────
    @abstractmethod
    def _get(self, doc_id: str) -> dict | None: ...
    @abstractmethod
    def _create(self, doc_id: str, meta: dict, version: dict) -> None: ...
    @abstractmethod
    def _append_version(self, doc_id: str, body: str, size: int, author: str, ts) -> None:
        """Atomically: read current_version N, write version N+1, advance the pointer."""
    @abstractmethod
    def _patch(self, doc_id: str, fields: dict) -> None:
        """Merge fields into the artifact doc. Raises ArtifactNotFound if absent."""
    @abstractmethod
    def _add_viewer(self, doc_id: str, viewer: str, ts) -> None:
        """Add viewer to the distinct set and bump view_count/last_viewed_at."""
    @abstractmethod
    def _delete(self, doc_id: str) -> None: ...
    @abstractmethod
    def _get_version(self, doc_id: str, n: int) -> dict | None: ...
    @abstractmethod
    def _list_versions(self, doc_id: str) -> list[dict]: ...
    @abstractmethod
    def _query(self, field: str, op: str, value) -> list[dict]:
        """op in {'==', 'array_contains'}; returns metadata dicts with 'id'."""
    @abstractmethod
    def _iter_all(self) -> list[dict]: ...
    @abstractmethod
    def _upsert_user(self, email: str, name: str, ts) -> None: ...
    @abstractmethod
    def _list_users(self) -> list[dict]: ...

    # ── search vector maintenance ───────────────────────────────────────────
    def _embedding_fields(self, art: dict) -> dict:
        if self.embedder is None:
            return {}
        text = search_text(art)
        h = search_hash(text)
        if art.get("search_hash") == h and art.get("embedding"):
            return {}
        return {
            "embedding": self.embedder.embed([text])[0] if text else [],
            "embedding_model": self.embedder.model_id,
            "search_hash": h,
        }

    def reindex(self, doc_id: str) -> None:
        art = self._get(doc_id)
        if art is None:
            raise ArtifactNotFound(doc_id)
        fields = self._embedding_fields(art)
        if fields:
            self._patch(doc_id, fields)

    # ── writes ──────────────────────────────────────────────────────────────
    def create_artifact(self, *, owner: str, owner_name: str = "", title: str, kind: str,
                        body: str, visibility: str = "private", shared_with=None,
                        metadata: dict | None = None, created_via: str = "web") -> dict:
        """Create an artifact at version 1. `owner` is the verified caller identity
        the surface asserts; nothing in `metadata` can override server fields."""
        t = check_title(title)
        if kind not in VALID_KINDS:
            raise ArtifactError(f"kind {kind!r} not in {list(VALID_KINDS)}")
        if visibility not in VALID_VISIBILITY:
            raise ArtifactError(f"visibility {visibility!r} not in {list(VALID_VISIBILITY)}")
        if not owner or "@" not in owner:
            raise ArtifactError("owner must be a verified email")
        size = check_body(body)
        grants = normalize_emails(shared_with or [])
        meta = clean_metadata({k: v for k, v in (metadata or {}).items() if k not in SERVER_FIELDS})
        ts = now()
        doc = {
            "owner": owner.lower(),
            "owner_name": owner_name or "",
            "title": t,
            "kind": kind,
            "visibility": visibility,
            "shared_with": [g for g in grants if g != owner.lower()],
            "current_version": 1,
            "size": size,
            "description": "",
            "source_question": "",
            "tags": [],
            "sources": [],
            "data_as_of": "",
            "sensitive": False,
            "viewers": [],
            "view_count": 0,
            "last_viewed_at": None,
            "created_at": ts,
            "updated_at": ts,
            "created_via": created_via,
            **meta,
        }
        doc.update(self._embedding_fields(doc))
        doc_id = new_id()
        self._create(doc_id, doc, {"n": 1, "body": body, "size": size, "author": owner.lower(), "created_at": ts})
        return self.get_artifact(doc_id)

    def update_artifact(self, doc_id: str, body: str, *, author: str) -> dict:
        """Append a new version and advance current_version; history is retained."""
        size = check_body(body)
        self._append_version(doc_id, body, size, author.lower(), now())
        return self.get_artifact(doc_id)

    def revert(self, doc_id: str, n: int, *, author: str) -> dict:
        """Copy version n into a NEW current version; history is never deleted."""
        ver = self._get_version(doc_id, int(n))
        if ver is None:
            raise ArtifactNotFound(f"{doc_id}@{n}")
        return self.update_artifact(doc_id, ver["body"], author=author)

    def rename(self, doc_id: str, title: str) -> dict:
        """Metadata only: no new version."""
        t = check_title(title)
        art = self._require(doc_id)
        fields = {"title": t, "updated_at": now()}
        fields.update(self._embedding_fields({**art, **fields}))
        self._patch(doc_id, fields)
        return self.get_artifact(doc_id)

    def update_metadata(self, doc_id: str, metadata: dict) -> dict:
        art = self._require(doc_id)
        fields = clean_metadata({k: v for k, v in metadata.items() if k not in SERVER_FIELDS})
        if not fields:
            return art
        fields["updated_at"] = now()
        fields.update(self._embedding_fields({**art, **fields}))
        self._patch(doc_id, fields)
        return self.get_artifact(doc_id)

    def set_visibility(self, doc_id: str, visibility: str) -> dict:
        if visibility not in VALID_VISIBILITY:
            raise ArtifactError(f"visibility {visibility!r} not in {list(VALID_VISIBILITY)}")
        self._require(doc_id)
        self._patch(doc_id, {"visibility": visibility, "updated_at": now()})
        return self.get_artifact(doc_id)

    def set_shared_with(self, doc_id: str, emails) -> dict:
        art = self._require(doc_id)
        grants = [e for e in normalize_emails(emails) if e != art["owner"]]
        self._patch(doc_id, {"shared_with": grants, "updated_at": now()})
        return self.get_artifact(doc_id)

    def record_view(self, doc_id: str, viewer: str) -> dict:
        """Record an open. Never counts the owner; leaves updated_at untouched (an
        open is not an edit). Authorization is the caller's job (access.can_open)."""
        art = self._require(doc_id)
        viewer = (viewer or "").lower()
        if viewer and viewer != art["owner"]:
            self._add_viewer(doc_id, viewer, now())
        return self.get_artifact(doc_id)

    def delete_artifact(self, doc_id: str) -> None:
        self._require(doc_id)
        self._delete(doc_id)

    # ── reads ───────────────────────────────────────────────────────────────
    def _require(self, doc_id: str) -> dict:
        art = self._get(doc_id)
        if art is None:
            raise ArtifactNotFound(doc_id)
        return art

    def get_artifact(self, doc_id: str) -> dict | None:
        art = self._get(doc_id)
        return {"id": doc_id, **art} if art is not None else None

    def get_version_body(self, doc_id: str, n: int | None = None) -> str | None:
        art = self._get(doc_id)
        if art is None:
            return None
        ver = self._get_version(doc_id, int(n if n is not None else art["current_version"]))
        return ver["body"] if ver else None

    def list_versions(self, doc_id: str) -> list[dict]:
        return sorted(
            ({"n": v["n"], "size": v["size"], "author": v.get("author", ""), "created_at": v["created_at"]}
             for v in self._list_versions(doc_id)),
            key=lambda v: v["n"],
        )

    def list_by_owner(self, owner: str) -> list[dict]:
        return self._query("owner", "==", owner.lower())

    def list_shared_with(self, email: str) -> list[dict]:
        return self._query("shared_with", "array_contains", email.lower())

    def list_org_shared(self) -> list[dict]:
        return self._query("visibility", "==", "shared")

    def iter_all(self) -> list[dict]:
        return self._iter_all()

    # ── people directory (autocomplete in the share dialog) ─────────────────
    def touch_user(self, email: str, name: str = "") -> None:
        self._upsert_user(email.lower(), name or "", now())

    def search_users(self, q: str, limit: int = 8) -> list[dict]:
        needle = (q or "").strip().lower()
        if len(needle) < 2:
            return []
        hits = [u for u in self._list_users()
                if needle in u["email"] or needle in (u.get("name") or "").lower()]
        hits.sort(key=lambda u: (not u["email"].startswith(needle), u["email"]))
        return [{"email": u["email"], "name": u.get("name") or ""} for u in hits[:max(1, min(limit, 20))]]
