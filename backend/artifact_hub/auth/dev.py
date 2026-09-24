"""DEV AUTH MODE: fake identities for local runs and tests. NEVER enable in a shared
environment (Settings.check refuses it when ENVIRONMENT=prod, and the UI shows a
permanent banner).

A dev bearer token is `dev:<email>` or `dev:<email>;groups=<g1>,<g2>`. A bare email
found in DEV_DIRECTORY takes that entry's name and groups; any other email is
accepted with no groups, so tests can mint arbitrary callers.
"""
from __future__ import annotations

from .identity import Principal, build_principal

DEV_PREFIX = "dev:"


def dev_directory(settings) -> list[dict]:
    """The fake people shown in the dev login picker."""
    people = [
        {"email": "alice@example.com", "name": "Alice Analyst", "groups": [settings.publisher_group]},
        {"email": "bob@example.com", "name": "Bob Builder", "groups": []},
        {"email": "carol@example.com", "name": "Carol Admin", "groups": [settings.admin_group]},
        {"email": "dave@example.com", "name": "Dave Director", "groups": []},
    ]
    for extra in settings.dev_users:
        people.append({"email": extra.lower(), "name": extra.split("@")[0].title(), "groups": []})
    return people


def authenticate_dev(settings, token: str) -> Principal | None:
    if not token.startswith(DEV_PREFIX):
        return None
    raw = token[len(DEV_PREFIX):]
    email, _, rest = raw.partition(";")
    email = email.strip().lower()
    entry = next((p for p in dev_directory(settings) if p["email"] == email), None)
    groups = list(entry["groups"]) if entry else []
    name = entry["name"] if entry else ""
    if rest.startswith("groups="):
        groups = [g for g in rest[len("groups="):].split(",") if g]
    return build_principal(settings, email=email, name=name, subject=f"dev|{email}", groups=groups, via="dev")
