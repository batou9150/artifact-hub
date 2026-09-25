"""The verified caller. Every surface (REST, sandbox, MCP) resolves a request to a
Principal before touching the store; the store never trusts client-supplied
attribution."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    email: str                      # lowercased; the identity key used for ownership and grants
    name: str = ""
    subject: str = ""               # IdP `sub`, kept for audit logs
    groups: frozenset[str] = field(default_factory=frozenset)
    is_publisher: bool = False      # member of the configured publisher group
    is_admin: bool = False          # in the admin group, or listed in ADMIN_EMAILS
    via: str = "oidc"               # oidc | dev

    def public(self) -> dict:
        return {
            "email": self.email,
            "name": self.name or self.email,
            "is_publisher": self.is_publisher,
            "is_admin": self.is_admin,
            "auth_mode": self.via,
        }


def build_principal(settings, *, email: str, name: str = "", subject: str = "",
                    groups=(), via: str = "oidc") -> Principal | None:
    """Normalize a verified identity and apply the domain allow-list. Returns None
    when the identity is not acceptable (no email, or outside the allowed domains)."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None
    if settings.allowed_email_domains:
        domain = email.rsplit("@", 1)[1]
        if domain not in settings.allowed_email_domains:
            return None
    groups = frozenset(str(g) for g in (groups or []))
    is_admin = settings.admin_group in groups or email in settings.admin_emails
    return Principal(
        email=email,
        name=name or "",
        subject=subject or "",
        groups=groups,
        is_publisher=is_admin or settings.publisher_group in groups,
        is_admin=is_admin,
        via=via,
    )
