"""Authorization policy: the one place that decides who may open, manage, share or
delete an artifact. The REST API, the sandbox content endpoint, the MCP tools and
search all call these functions, so the rules cannot diverge between surfaces.

  open     owner, anyone in shared_with, or any authenticated member when
           visibility == 'shared' (organisation-wide, by link)
  version  a prior version is owner-only; grantees only ever see the current one
  manage   edit / rename / share / revert: owner only
  delete   owner, or a member of the admin group (moderation backstop)
  org-wide publishing (visibility 'shared') requires the publisher group, and is
           refused for an artifact flagged sensitive when SENSITIVE_ORG_SHARE=deny
"""
from __future__ import annotations

from .auth.identity import Principal


class Forbidden(PermissionError):
    """Maps to HTTP 403 (only where revealing existence is acceptable: the caller
    can already open the artifact, or it is a write on an artifact they own)."""


def is_owner(p: Principal | None, art: dict | None) -> bool:
    return bool(p and art and (art.get("owner") or "").lower() == p.email)


def can_open(p: Principal | None, art: dict | None) -> bool:
    if p is None or art is None:
        return False
    if is_owner(p, art):
        return True
    if art.get("visibility") == "shared":
        return True
    return p.email in {e.lower() for e in (art.get("shared_with") or []) if isinstance(e, str)}


def can_open_version(p: Principal | None, art: dict | None, n: int | None) -> bool:
    if not can_open(p, art):
        return False
    if n is None or int(n) == int(art.get("current_version") or 0):
        return True
    return is_owner(p, art)


def can_manage(p: Principal | None, art: dict | None) -> bool:
    return is_owner(p, art)


def can_delete(p: Principal | None, art: dict | None) -> bool:
    return is_owner(p, art) or bool(p and p.is_admin and art is not None)


def check_org_share(settings, p: Principal, art: dict | None) -> None:
    """Raise Forbidden unless `p` may make `art` visible to the whole organisation."""
    if not p.is_publisher:
        raise Forbidden(
            f"sharing with the whole organisation is restricted to the '{settings.publisher_group}' group"
        )
    if art is not None and art.get("sensitive") and settings.sensitive_org_share == "deny":
        raise Forbidden("this artifact is flagged sensitive and cannot be shared with the whole organisation")


def check_invitees(settings, emails) -> None:
    """Per-person grants must stay inside the allowed identity domains, when set."""
    if not settings.allowed_email_domains:
        return
    for e in emails or []:
        domain = str(e).strip().lower().rsplit("@", 1)[-1]
        if domain not in settings.allowed_email_domains:
            raise Forbidden(f"{e} is outside the allowed domains ({', '.join(settings.allowed_email_domains)})")


def access_level(p: Principal, art: dict) -> str:
    """How the caller reaches the artifact (for UI badges and search results)."""
    if is_owner(p, art):
        return "owner"
    if p.email in (art.get("shared_with") or []):
        return "invited"
    if art.get("visibility") == "shared":
        return "organisation"
    return "none"
