"""Use cases shared by the REST API and the MCP tools. Every method takes the
verified Principal first; nothing here trusts a client-supplied identity.

Error contract:
  NotFound   absent OR not openable by the caller (never distinguish the two)
  Forbidden  the caller can see the artifact but may not perform this action
  ArtifactError / ArtifactTooLarge  invalid input
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime

from . import access, sandbox
from .access import Forbidden
from .auth.identity import Principal
from .search.service import SearchService
from .store.base import ArtifactError, ArtifactNotFound, ArtifactStore

log = logging.getLogger("artifact_hub.events")

HIDDEN_FIELDS = {"embedding", "embedding_model", "search_hash"}
OWNER_ONLY_FIELDS = {"viewers", "view_count", "last_viewed_at", "shared_with", "moderation"}
# What an administrator sees of any artifact: enough to moderate, not who read it.
ADMIN_FIELDS = ("id", "owner", "owner_name", "title", "kind", "visibility", "current_version", "size",
                "description", "tags", "sensitive", "created_at", "updated_at", "created_via",
                "view_count", "moderation")


class NotFound(LookupError):
    pass


def _iso(v):
    if isinstance(v, dict):
        return {k: _iso(x) for k, x in v.items()}
    return v.isoformat() if isinstance(v, datetime) else v


class ArtifactService:
    def __init__(self, settings, store: ArtifactStore, search: SearchService):
        self.settings = settings
        self.store = store
        self.search_service = search

    # ── helpers ─────────────────────────────────────────────────────────────
    def link(self, doc_id: str) -> str:
        return f"{self.settings.app_origin}/artifacts/{doc_id}"

    def view(self, p: Principal, art: dict) -> dict:
        """The artifact as `p` may see it: no vectors ever; viewer list, counts and
        the invite list only for the owner."""
        owner = access.is_owner(p, art)
        out = {}
        for k, v in art.items():
            if k in HIDDEN_FIELDS or (k in OWNER_ONLY_FIELDS and not owner):
                continue
            out[k] = _iso(v)
        if owner:
            out["viewers_count"] = len(art.get("viewers") or [])
            out["shared_with"] = list(art.get("shared_with") or [])
        out["url"] = self.link(art["id"])
        out["access"] = access.access_level(p, art)
        out["can_manage"] = access.can_manage(p, art)
        out["can_delete"] = access.can_delete(p, art)
        return out

    def _openable(self, p: Principal, doc_id: str) -> dict:
        art = self.store.get_artifact(doc_id)
        if not access.can_open(p, art):
            raise NotFound(doc_id)
        return art

    def _owned(self, p: Principal, doc_id: str) -> dict:
        art = self._openable(p, doc_id)
        if not access.can_manage(p, art):
            raise Forbidden("only the owner can change this artifact")
        return art

    def _event(self, name: str, p: Principal, art_id: str, **extra) -> None:
        # One structured line per event: Cloud Logging picks it up as jsonPayload and
        # a log sink to BigQuery feeds the usage dashboard (opens, publications).
        log.info(json.dumps({"event": name, "artifact_id": art_id, "actor": p.email, **extra}))

    # ── writes ──────────────────────────────────────────────────────────────
    def create(self, p: Principal, *, title: str, kind: str, body: str, visibility: str = "private",
               shared_with=None, metadata: dict | None = None, via: str = "web") -> dict:
        if visibility == "shared":
            access.check_org_share(self.settings, p, {"sensitive": bool((metadata or {}).get("sensitive"))})
        if shared_with:
            access.check_invitees(self.settings, shared_with)
        art = self.store.create_artifact(owner=p.email, owner_name=p.name, title=title, kind=kind, body=body,
                                         visibility=visibility, shared_with=shared_with, metadata=metadata,
                                         created_via=via)
        self._event("artifact_created", p, art["id"], via=via, kind=kind, visibility=visibility)
        return self.view(p, art) | {"warnings": sandbox.external_reference_warnings(kind, body)}

    def update(self, p: Principal, doc_id: str, *, body: str | None = None, title: str | None = None,
               metadata: dict | None = None) -> dict:
        art = self._owned(p, doc_id)
        warnings: list[str] = []
        if body is not None:
            art = self.store.update_artifact(doc_id, body, author=p.email)
            warnings = sandbox.external_reference_warnings(art["kind"], body)
            self._event("artifact_updated", p, doc_id, version=art["current_version"])
        if title is not None:
            art = self.store.rename(doc_id, title)
        if metadata:
            if metadata.get("sensitive") and art.get("visibility") == "shared" \
                    and self.settings.sensitive_org_share == "deny":
                # Flagging a published artifact as sensitive withdraws the org-wide link.
                self.store.set_visibility(doc_id, "private")
                warnings.append("Flagged sensitive: organisation-wide sharing was withdrawn.")
            art = self.store.update_metadata(doc_id, metadata)
        return self.view(p, art) | {"warnings": warnings}

    def share(self, p: Principal, doc_id: str, *, visibility: str | None = None, shared_with=None,
              add=None, remove=None) -> dict:
        art = self._owned(p, doc_id)
        if visibility == "shared" and art.get("visibility") != "shared":
            access.check_org_share(self.settings, p, art)
        grants = None
        if shared_with is not None:
            grants = list(shared_with)
        if add or remove:
            current = list(grants if grants is not None else art.get("shared_with") or [])
            rm = {e.strip().lower() for e in (remove or [])}
            current = [e for e in current if e.lower() not in rm]
            current += [e for e in (add or []) if e.strip().lower() not in {c.lower() for c in current}]
            grants = current
        if grants is not None:
            access.check_invitees(self.settings, grants)
            art = self.store.set_shared_with(doc_id, grants)
        if visibility is not None:
            art = self.store.set_visibility(doc_id, visibility)
        self._event("artifact_shared", p, doc_id, visibility=art["visibility"],
                    grants=len(art.get("shared_with") or []))
        return self.view(p, art)

    def revert(self, p: Principal, doc_id: str, n: int) -> dict:
        self._owned(p, doc_id)
        try:
            art = self.store.revert(doc_id, n, author=p.email)
        except ArtifactNotFound:
            raise ArtifactError(f"version {n} does not exist")
        self._event("artifact_reverted", p, doc_id, to=n, version=art["current_version"])
        return self.view(p, art)

    def delete(self, p: Principal, doc_id: str, *, note: str = "") -> None:
        art = self.store.get_artifact(doc_id)
        if art is None:
            raise NotFound(doc_id)
        if not access.can_delete(p, art):
            # An admin may delete without opening; anyone else learns nothing.
            if access.can_open(p, art):
                raise Forbidden("only the owner or an administrator can delete this artifact")
            raise NotFound(doc_id)
        self.store.delete_artifact(doc_id)
        by_admin = not access.is_owner(p, art)
        self._event("artifact_deleted", p, doc_id, by_admin=by_admin,
                    **({"owner": art["owner"], "note": note} if by_admin else {}))

    def record_view(self, p: Principal, doc_id: str) -> dict:
        art = self._openable(p, doc_id)
        if not access.is_owner(p, art):
            art = self.store.record_view(doc_id, p.email)
            self._event("artifact_viewed", p, doc_id, owner=art["owner"], access=access.access_level(p, art))
        return {"ok": True}

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, p: Principal, doc_id: str) -> dict:
        return self.view(p, self._openable(p, doc_id))

    def get_with_body(self, p: Principal, doc_id: str, version: int | None = None) -> dict:
        art = self._openable(p, doc_id)
        if not access.can_open_version(p, art, version):
            raise NotFound(doc_id)
        body = self.store.get_version_body(doc_id, version)
        if body is None:
            raise NotFound(doc_id)
        return self.view(p, art) | {"body": body, "version": version or art["current_version"]}

    def versions(self, p: Principal, doc_id: str) -> list[dict]:
        self._owned(p, doc_id)
        return [{**v, "created_at": _iso(v["created_at"])} for v in self.store.list_versions(doc_id)]

    def list_mine(self, p: Principal) -> list[dict]:
        arts = sorted(self.store.list_by_owner(p.email), key=lambda a: a.get("updated_at") or 0, reverse=True)
        return [self.view(p, a) for a in arts]

    def list_shared_with_me(self, p: Principal) -> list[dict]:
        by_id = {}
        for a in self.store.list_shared_with(p.email) + self.store.list_org_shared():
            if not access.is_owner(p, a) and access.can_open(p, a):
                by_id[a["id"]] = a
        arts = sorted(by_id.values(), key=lambda a: a.get("updated_at") or 0, reverse=True)
        return [self.view(p, a) for a in arts]

    def search(self, p: Principal, query: str, limit: int = 10) -> list[dict]:
        hits = self.search_service.search(p, query, limit)
        return [{**{k: _iso(v) for k, v in h.items()}, "url": self.link(h["id"])} for h in hits]

    def render_url(self, p: Principal, doc_id: str, version: int | None = None) -> str:
        """Absolute sandbox URL with a fresh render ticket (access checked here and
        again by the sandbox endpoint)."""
        art = self._openable(p, doc_id)
        if not access.can_open_version(p, art, version):
            raise NotFound(doc_id)
        ticket = sandbox.mint_ticket(self.settings.render_ticket_secret, doc_id, p.email, version,
                                     self.settings.render_ticket_ttl_seconds)
        path = f"/a/{doc_id}" if version is None else f"/a/{doc_id}/v/{int(version)}"
        return f"{self.settings.public_base_url}{path}?t={ticket}"

    # ── administration (ADMIN_GROUP / ADMIN_EMAILS) ─────────────────────────
    def admin_view(self, art: dict) -> dict:
        out = {k: _iso(art.get(k)) for k in ADMIN_FIELDS if k in art}
        out["shared_with_count"] = len(art.get("shared_with") or [])
        out["url"] = self.link(art["id"])
        return out

    def admin_list(self, p: Principal, *, q: str = "", owner: str = "", visibility: str = "",
                   sensitive: bool | None = None, limit: int = 200) -> dict:
        access.require_admin(p)
        needle, owner = q.strip().lower(), owner.strip().lower()
        arts = []
        for a in self.store.iter_all():
            if needle and needle not in (a.get("title") or "").lower() and needle not in (a.get("owner") or ""):
                continue
            if owner and a.get("owner") != owner:
                continue
            if visibility and a.get("visibility") != visibility:
                continue
            if sensitive is not None and bool(a.get("sensitive")) != sensitive:
                continue
            arts.append(a)
        arts.sort(key=lambda a: a.get("updated_at") or 0, reverse=True)
        return {"artifacts": [self.admin_view(a) for a in arts[:max(1, min(limit, 1000))]], "total": len(arts)}

    def admin_stats(self, p: Principal) -> dict:
        access.require_admin(p)
        arts = self.store.iter_all()
        count = lambda key: dict(Counter(str(a.get(key) or "") for a in arts))  # noqa: E731
        owners = Counter(a.get("owner") for a in arts)
        return {
            "artifacts": len(arts),
            "bytes": sum(int(a.get("size") or 0) for a in arts),
            "owners": len(owners),
            "sensitive": sum(1 for a in arts if a.get("sensitive")),
            "by_visibility": count("visibility"),
            "by_kind": count("kind"),
            "by_via": count("created_via"),
            "top_owners": [{"email": e, "artifacts": n} for e, n in owners.most_common(5)],
        }

    def admin_moderate(self, p: Principal, doc_id: str, *, withdraw_org: bool = False,
                       clear_invites: bool = False, sensitive: bool | None = None, note: str = "") -> dict:
        """Take sharing back or flag an artifact without being its owner. The body is
        never changed; the owner sees the last action and its note."""
        access.require_admin(p)
        art = self.store.get_artifact(doc_id)
        if art is None:
            raise NotFound(doc_id)
        actions = []
        if sensitive is not None and bool(art.get("sensitive")) != sensitive:
            self.store.update_metadata(doc_id, {"sensitive": sensitive})
            actions.append("flagged_sensitive" if sensitive else "unflagged_sensitive")
            if sensitive and self.settings.sensitive_org_share == "deny":
                withdraw_org = True
        if withdraw_org and art.get("visibility") == "shared":
            self.store.set_visibility(doc_id, "private")
            actions.append("withdrew_org_share")
        if clear_invites and art.get("shared_with"):
            self.store.set_shared_with(doc_id, [])
            actions.append("cleared_invites")
        if not actions:
            raise ArtifactError("nothing to change")
        art = self.store.record_moderation(doc_id, by=p.email, action=",".join(actions), note=note.strip())
        self._event("artifact_moderated", p, doc_id, owner=art["owner"], actions=actions, note=note.strip())
        return self.admin_view(art)
