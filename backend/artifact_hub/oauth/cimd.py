"""OAuth Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document,
the client registration mechanism of the MCP 2025-11-25 authorization spec).

An MCP client's `client_id` is an https URL; the document served there describes
the client. Nothing is registered in advance. The document is untrusted input
fetched on behalf of an anonymous caller, so:

  * the URL must be https on the default port, with a path, and no userinfo,
    fragment or dot segments;
  * every address the host resolves to must be publicly routable (no metadata
    server, no VPC, no loopback), and the request goes to the checked address
    with TLS verified against the host name, so a DNS rebind cannot swap it;
  * no redirects, 5 s timeout, 5 KiB cap, JSON object only;
  * the document must name itself (`client_id` equal to the URL), list
    `redirect_uris`, and be a public client (`token_endpoint_auth_method` none).

Documents are cached per Cache-Control max-age, clamped to [60 s, 1 h].
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_BYTES = 5 * 1024
TIMEOUT = 5.0
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob"}


class InvalidClient(Exception):
    pass


@dataclass(frozen=True)
class ClientMetadata:
    client_id: str
    client_name: str
    client_uri: str
    redirect_uris: tuple[str, ...]

    @property
    def host(self) -> str:
        return urlsplit(self.client_id).hostname or ""

    def accepts_redirect(self, redirect_uri: str) -> bool:
        return any(redirect_matches(r, redirect_uri) for r in self.redirect_uris)


def is_cimd_client_id(client_id: str) -> bool:
    return client_id.startswith("https://")


def validate_client_id_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidClient("client_id must be an https URL")
    if parts.username or parts.password or parts.fragment:
        raise InvalidClient("client_id must not contain credentials or a fragment")
    if parts.port not in (None, 443):
        raise InvalidClient("client_id must use the default https port")
    if not parts.path or parts.path == "/":
        raise InvalidClient("client_id must contain a path")
    if any(seg in (".", "..") for seg in parts.path.split("/")):
        raise InvalidClient("client_id must not contain dot segments")


def _valid_redirect_uri(uri: str) -> bool:
    parts = urlsplit(uri)
    if not parts.scheme or parts.fragment:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme == "http":  # RFC 8252: http only for the loopback interface
        return parts.hostname in LOOPBACK_HOSTS
    # Private-use URI schemes of native apps (RFC 8252 section 7.1).
    return re.fullmatch(r"[a-z][a-z0-9+.-]*", parts.scheme) is not None and parts.scheme not in FORBIDDEN_SCHEMES


def redirect_matches(registered: str, requested: str) -> bool:
    """Exact match, except that the port of an http loopback redirect is free
    (RFC 8252 section 7.3: native apps pick an ephemeral port at run time)."""
    if registered == requested:
        return True
    r, q = urlsplit(registered), urlsplit(requested)
    if r.scheme == q.scheme == "http" and r.hostname in LOOPBACK_HOSTS and r.hostname == q.hostname:
        return (r.path, r.query) == (q.path, q.query) and not q.fragment
    return False


def _public_addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise InvalidClient("client_id host does not resolve") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise InvalidClient("client_id host is not publicly routable")
    return addresses


def _max_age(cache_control: str | None) -> int:
    match = re.search(r"max-age=(\d+)", cache_control or "")
    age = int(match.group(1)) if match else 300
    if "no-store" in (cache_control or "") or "no-cache" in (cache_control or ""):
        age = 60
    return max(60, min(age, 3600))


class ClientMetadataResolver:
    def __init__(self, fetch=None, allowed_hosts: tuple[str, ...] = ()):
        """`fetch(url) -> (status, headers, body_bytes)` is injectable for tests."""
        self._fetch = fetch or self._fetch_pinned
        self._allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        self._cache: dict[str, tuple[float, ClientMetadata]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _fetch_pinned(url: str) -> tuple[int, dict, bytes]:
        parts = urlsplit(url)
        address = _public_addresses(parts.hostname)[0]
        netloc = f"[{address}]" if ":" in address else address
        pinned = urlunsplit(("https", netloc, parts.path, parts.query, ""))
        headers = {"Host": parts.hostname, "Accept": "application/json"}
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            with client.stream("GET", pinned, headers=headers,
                               extensions={"sni_hostname": parts.hostname}) as res:
                body = b""
                for chunk in res.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise InvalidClient("client metadata document is too large")
                return res.status_code, dict(res.headers), body

    def resolve(self, client_id: str) -> ClientMetadata:
        validate_client_id_url(client_id)
        host = urlsplit(client_id).hostname.lower()
        if self._allowed_hosts and host not in self._allowed_hosts:
            raise InvalidClient("this client is not allowed on this server")
        now = time.time()
        with self._lock:
            hit = self._cache.get(client_id)
        if hit and hit[0] > now:
            return hit[1]
        try:
            status, headers, body = self._fetch(client_id)
        except httpx.HTTPError as exc:
            raise InvalidClient("client metadata document could not be fetched") from exc
        if status != 200:
            raise InvalidClient(f"client metadata document answered {status}")
        meta = self._parse(client_id, body)
        headers = {k.lower(): v for k, v in headers.items()}
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[client_id] = (now + _max_age(headers.get("cache-control")), meta)
        return meta

    @staticmethod
    def _parse(client_id: str, body: bytes) -> ClientMetadata:
        if len(body) > MAX_BYTES:
            raise InvalidClient("client metadata document is too large")
        try:
            doc = json.loads(body)
        except ValueError as exc:
            raise InvalidClient("client metadata document is not JSON") from exc
        if not isinstance(doc, dict):
            raise InvalidClient("client metadata document is not a JSON object")
        if doc.get("client_id") != client_id:
            raise InvalidClient("client metadata document does not match its URL")
        if any(k in doc for k in ("client_secret", "client_secret_expires_at")):
            raise InvalidClient("client metadata document must not carry a secret")
        if doc.get("token_endpoint_auth_method", "none") != "none":
            raise InvalidClient("only public clients (token_endpoint_auth_method none) are supported")
        if "authorization_code" not in doc.get("grant_types", ["authorization_code"]):
            raise InvalidClient("client does not use the authorization_code grant")
        if "code" not in doc.get("response_types", ["code"]):
            raise InvalidClient("client does not use the code response type")
        uris = doc.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _valid_redirect_uri(u)
                                                            for u in uris):
            raise InvalidClient("client metadata document has invalid redirect_uris")
        name = doc.get("client_name")
        client_uri = doc.get("client_uri")
        return ClientMetadata(
            client_id=client_id,
            client_name=name.strip()[:100] if isinstance(name, str) and name.strip() else "",
            client_uri=client_uri if isinstance(client_uri, str) and client_uri.startswith("https://") else "",
            redirect_uris=tuple(uris),
        )
