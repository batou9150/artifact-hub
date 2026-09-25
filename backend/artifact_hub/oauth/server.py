"""OAuth 2.1 authorization server for MCP clients (MCP authorization spec 2025-11-25).

  GET  /.well-known/oauth-authorization-server   RFC 8414 metadata
  GET  /oauth/jwks                               signing key (public)
  GET  /oauth/authorize                          client (CIMD) + PKCE checks, then upstream sign-in
  GET  /oauth/callback                           upstream IdP returns here; consent page
  POST /oauth/consent                            the user allows or denies the client
  POST /oauth/token                              authorization_code and refresh_token grants

Clients register through Client ID Metadata Documents only (see cimd.py): public
clients, PKCE S256 mandatory, exact redirect match (loopback port free). The human
signs in at the upstream IdP; this server then shows who is asking (the client_id
host, the redirect target) and issues its own tokens:

  * access token: JWT (ES256, typ at+jwt), iss = this service, aud = the MCP
    resource URL (RFC 8707), 1 h; the MCP endpoint verifies it locally;
  * refresh token: opaque, single use, rotated, bound to the client; a replayed
    code or refresh token revokes the whole sign-in (family); 30 days absolute.

The pending request is bound to the browser that started it (cookie), so a link
to someone else's pending request cannot be completed by a victim, and the
consent form carries a per-request CSRF token.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..auth.identity import Principal, build_principal
from .cimd import LOOPBACK_HOSTS, ClientMetadataResolver, InvalidClient, is_cimd_client_id
from .grants import GrantStore
from .keys import SigningKey
from .upstream import UpstreamError, UpstreamLogin, s256

log = logging.getLogger(__name__)

SCOPES = ("artifacts",)
# Requested by some clients out of habit; accepted and ignored (this server issues
# no ID tokens; refresh tokens are always issued).
IGNORED_SCOPES = {"openid", "profile", "email", "offline_access"}
TX_TTL = 600
CODE_TTL = 60
PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, MCP-Protocol-Version"}
VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


class OAuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(description or error)
        self.error, self.description, self.status = error, description, status

    def response(self) -> JSONResponse:
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        return JSONResponse(body, status_code=self.status, headers={**NO_STORE, **CORS})


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _with_params(uri: str, params: dict) -> str:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light dark;--fg:#1c1c1e;--muted:#6b6b70;--bg:#f6f6f4;--card:#fff;--line:#deded9;--accent:#1f5eff}}
@media (prefers-color-scheme:dark){{:root{{--fg:#ececec;--muted:#a0a0a6;--bg:#141416;--card:#1d1d20;--line:#333338;--accent:#7aa2ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:440px;margin:10vh auto;padding:0 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}}
h1{{font-size:20px;margin:0 0 12px}} p{{margin:8px 0}} .muted{{color:var(--muted);font-size:13px}}
dl{{margin:16px 0;display:grid;grid-template-columns:auto 1fr;gap:6px 12px}} dt{{color:var(--muted)}} dd{{margin:0;word-break:break-all}}
code{{font-size:13px}} .actions{{display:flex;gap:8px;margin-top:20px}}
button{{flex:1;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--fg);cursor:pointer}}
button.primary{{background:var(--accent);border-color:var(--accent);color:#fff}}
</style></head><body><main><div class="card">{body}</div></main></body></html>"""
    return HTMLResponse(doc, status_code=status,
                        headers={**NO_STORE, "Content-Security-Policy": PAGE_CSP, "X-Frame-Options": "DENY"})


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    return _page("Sign-in failed", f"<h1>Cannot connect this application</h1><p>{html.escape(message)}</p>"
                                   "<p class='muted'>Close this window and start again from the application.</p>",
                 status)


class AuthorizationServer:
    def __init__(self, settings, grants: GrantStore, key: SigningKey, resolver: ClientMetadataResolver,
                 upstream: UpstreamLogin):
        self.settings = settings
        self.grants = grants
        self.key = key
        self.resolver = resolver
        self.upstream = upstream
        self.issuer = settings.public_base_url
        self.audience = settings.mcp_resource_url
        self.secure_cookie = self.issuer.startswith("https://")
        self.cookie = "__Host-ah_oauth" if self.secure_cookie else "ah_oauth"

    # ── metadata & verification ─────────────────────────────────────────────
    def metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/oauth/authorize",
            "token_endpoint": f"{self.issuer}/oauth/token",
            "jwks_uri": f"{self.issuer}/oauth/jwks",
            "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    def verify_access_token(self, token: str) -> dict | None:
        try:
            return self.key.verify(token, issuer=self.issuer, audience=self.audience)
        except jwt.PyJWTError:
            return None

    def principal_from_claims(self, claims: dict) -> Principal | None:
        return build_principal(self.settings, email=claims.get("email", ""), name=claims.get("name", ""),
                               subject=claims.get("sub", ""), groups=claims.get("groups") or [], via="oidc")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _browser_ok(self, request: Request, tx: dict) -> bool:
        value = request.cookies.get(self.cookie, "")
        return bool(value) and secrets.compare_digest(_hash(value), tx["browser"])

    def _client_redirect(self, tx: dict, **params) -> RedirectResponse:
        target = _with_params(tx["redirect_uri"], {**params, "state": tx.get("state"), "iss": self.issuer})
        return RedirectResponse(target, status_code=303, headers=NO_STORE)

    def _resource_ok(self, values: list[str]) -> bool:
        return all(v.rstrip("/") == self.audience.rstrip("/") for v in values)

    def _issue(self, grant: dict) -> JSONResponse:
        now = int(time.time())
        p = grant["principal"]
        access = self.key.sign({
            "iss": self.issuer, "aud": self.audience, "sub": p.get("subject") or p["email"],
            "email": p["email"], "name": p.get("name", ""), "groups": p.get("groups", []),
            "client_id": grant["client_id"], "scope": grant["scope"],
            "iat": now, "exp": now + self.settings.oauth_access_token_ttl, "jti": uuid.uuid4().hex,
        })
        refresh = secrets.token_urlsafe(32)
        self.grants.put("rt", refresh, {k: grant[k] for k in ("client_id", "scope", "principal", "family")},
                        self.settings.oauth_refresh_token_ttl)
        return JSONResponse({"access_token": access, "token_type": "Bearer",
                             "expires_in": self.settings.oauth_access_token_ttl,
                             "refresh_token": refresh, "scope": grant["scope"]},
                            headers={**NO_STORE, **CORS})

    # ── routes ──────────────────────────────────────────────────────────────
    def router(self) -> APIRouter:
        r = APIRouter(include_in_schema=False)

        @r.get("/.well-known/oauth-authorization-server")
        def as_metadata():
            return JSONResponse(self.metadata(), headers={**CORS, "Cache-Control": "public, max-age=3600"})

        @r.get("/oauth/jwks")
        def jwks():
            return JSONResponse(self.key.jwks(), headers={**CORS, "Cache-Control": "public, max-age=3600"})

        @r.options("/oauth/token")
        @r.options("/.well-known/oauth-authorization-server")
        def preflight():
            return Response(status_code=204, headers={**CORS, "Access-Control-Max-Age": "86400"})

        @r.get("/oauth/authorize")
        async def authorize(request: Request):
            q = request.query_params
            client_id, redirect_uri = q.get("client_id", ""), q.get("redirect_uri", "")
            # Until client and redirect are validated, errors are shown here, never redirected.
            if not is_cimd_client_id(client_id):
                return _error_page("This server only accepts clients identified by a Client ID Metadata "
                                   "Document (an https client_id).")
            try:
                meta = await run_in_threadpool(self.resolver.resolve, client_id)
            except InvalidClient as exc:
                return _error_page(f"Client rejected: {exc}.")
            if not redirect_uri or not meta.accepts_redirect(redirect_uri):
                return _error_page("The redirect URI is not registered for this client.")
            state = q.get("state")
            tx = {"client_id": client_id, "client_name": meta.client_name, "redirect_uri": redirect_uri,
                  "state": state}

            def fail(error, description):
                return self._client_redirect(tx, error=error, error_description=description)

            if state is not None and len(state) > 1024:
                return fail("invalid_request", "state is too long")
            if q.get("response_type") != "code":
                return fail("unsupported_response_type", "only the code response type is supported")
            challenge = q.get("code_challenge", "")
            if q.get("code_challenge_method") != "S256" or not CHALLENGE_RE.fullmatch(challenge):
                return fail("invalid_request", "PKCE with S256 is required")
            requested = set(q.get("scope", "").split())
            unknown = requested - set(SCOPES) - IGNORED_SCOPES
            if unknown:
                return fail("invalid_scope", f"unknown scope: {' '.join(sorted(unknown))}")
            if not self._resource_ok(q.getlist("resource")):
                return fail("invalid_target", f"this server only issues tokens for {self.audience}")

            browser = request.cookies.get(self.cookie) or secrets.token_urlsafe(32)
            tx_id, upstream_verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(24)
            tx.update(browser=_hash(browser), code_challenge=challenge, scope=" ".join(SCOPES),
                      upstream_verifier=upstream_verifier, nonce=nonce)
            try:
                target = await run_in_threadpool(self.upstream.authorize_url, state=tx_id, nonce=nonce,
                                                 code_verifier=upstream_verifier)
            except Exception:  # discovery failure
                log.exception("upstream discovery failed")
                return _error_page("The identity provider is unreachable, try again later.", 502)
            self.grants.put("tx", tx_id, tx, TX_TTL)
            res = RedirectResponse(target, status_code=302, headers=NO_STORE)
            if request.cookies.get(self.cookie) != browser:
                res.set_cookie(self.cookie, browser, max_age=TX_TTL, path="/", secure=self.secure_cookie,
                               httponly=True, samesite="lax")
            return res

        @r.get("/oauth/callback")
        async def callback(request: Request):
            q = request.query_params
            tx_id = q.get("state", "")
            tx = self.grants.get("tx", tx_id) if tx_id else None
            if not tx or tx.get("principal") or not self._browser_ok(request, tx):
                return _error_page("This sign-in request expired or was started in another browser.")
            if q.get("error") or not q.get("code"):
                self.grants.delete("tx", tx_id)
                return self._client_redirect(tx, error="access_denied", error_description="sign-in cancelled")
            try:
                claims = await run_in_threadpool(self.upstream.exchange, code=q["code"],
                                                 code_verifier=tx["upstream_verifier"], nonce=tx["nonce"])
            except UpstreamError as exc:
                self.grants.delete("tx", tx_id)
                return _error_page(str(exc))
            principal = self.upstream.verifier.principal_from_claims(claims)
            if principal is None:
                self.grants.delete("tx", tx_id)
                return self._client_redirect(tx, error="access_denied",
                                             error_description="this account is not allowed on this server")
            csrf = secrets.token_urlsafe(32)
            tx = {k: v for k, v in tx.items() if k != "used"}
            tx.update(csrf=csrf, principal={"email": principal.email, "name": principal.name,
                                            "subject": principal.subject, "groups": sorted(principal.groups)})
            self.grants.put("tx", tx_id, tx, TX_TTL)
            return self._consent_page(tx_id, tx)

        @r.post("/oauth/consent")
        async def consent(request: Request):
            form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
            tx_id = form.get("tx", "")
            used = self.grants.use("tx", tx_id) if tx_id else None
            if not used or not used[1]:
                return _error_page("This sign-in request expired or was already answered.")
            tx = used[0]
            if (not tx.get("principal") or not self._browser_ok(request, tx)
                    or not secrets.compare_digest(form.get("csrf", ""), tx.get("csrf", ""))):
                return _error_page("This sign-in request could not be verified.")
            self.grants.delete("tx", tx_id)
            if form.get("decision") != "allow":
                return self._client_redirect(tx, error="access_denied", error_description="access denied by the user")
            family, code = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
            self.grants.put("family", family, {"client_id": tx["client_id"], "email": tx["principal"]["email"]},
                            self.settings.oauth_refresh_token_ttl)
            self.grants.put("code", code, {"client_id": tx["client_id"], "redirect_uri": tx["redirect_uri"],
                                           "code_challenge": tx["code_challenge"], "scope": tx["scope"],
                                           "principal": tx["principal"], "family": family}, CODE_TTL)
            log.info("oauth grant: client=%s user=%s", tx["client_id"], tx["principal"]["email"])
            return self._client_redirect(tx, code=code)

        @r.post("/oauth/token")
        async def token(request: Request):
            form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
            try:
                if request.headers.get("authorization"):
                    raise OAuthError("invalid_client", "public clients only: no client authentication", 401)
                grant_type = form.get("grant_type")
                if grant_type == "authorization_code":
                    grant = self._redeem_code(form)
                elif grant_type == "refresh_token":
                    grant = self._redeem_refresh(form)
                else:
                    raise OAuthError("unsupported_grant_type")
            except OAuthError as exc:
                return exc.response()
            return self._issue(grant)

        return r

    def _consent_page(self, tx_id: str, tx: dict) -> HTMLResponse:
        e = html.escape
        client_host = urlsplit(tx["client_id"]).hostname or ""
        redirect = urlsplit(tx["redirect_uri"])
        target = ("an application on this computer" if redirect.hostname in LOOPBACK_HOSTS
                  else (redirect.hostname or f"{redirect.scheme}: app link"))
        name = tx.get("client_name") or client_host
        body = f"""<h1>Allow {e(name)} to use Artifact Hub?</h1>
<p>It will be able to search, read, create and share artifacts <strong>as you</strong>.</p>
<dl><dt>Signed in as</dt><dd>{e(tx['principal']['email'])}</dd>
<dt>Client</dt><dd><code>{e(tx['client_id'])}</code></dd>
<dt>Returns to</dt><dd>{e(target)}</dd></dl>
<p class="muted">Only allow if you started this connection yourself just now. The client is identified by
the domain <strong>{e(client_host)}</strong>, which published its description.</p>
<form method="post" action="/oauth/consent">
<input type="hidden" name="tx" value="{e(tx_id)}"><input type="hidden" name="csrf" value="{e(tx['csrf'])}">
<div class="actions"><button name="decision" value="deny">Deny</button>
<button class="primary" name="decision" value="allow">Allow</button></div></form>"""
        return _page("Allow access?", body)

    # ── grants ──────────────────────────────────────────────────────────────
    def _redeem_code(self, form: dict) -> dict:
        code = form.get("code", "")
        used = self.grants.use("code", code) if code else None
        if used is None:
            raise OAuthError("invalid_grant", "unknown or expired code")
        grant, first = used
        if not first:  # replay: revoke everything issued from this code
            self.grants.delete("family", grant["family"])
            log.warning("oauth: authorization code replayed, family revoked (client=%s)", grant["client_id"])
            raise OAuthError("invalid_grant", "code already used")
        if form.get("client_id") != grant["client_id"] or form.get("redirect_uri") != grant["redirect_uri"]:
            raise OAuthError("invalid_grant", "client_id or redirect_uri mismatch")
        verifier = form.get("code_verifier", "")
        if not VERIFIER_RE.fullmatch(verifier) or not secrets.compare_digest(s256(verifier), grant["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed")
        if not self._resource_ok([form["resource"]] if form.get("resource") else []):
            raise OAuthError("invalid_target")
        if self.grants.get("family", grant["family"]) is None:
            raise OAuthError("invalid_grant", "grant revoked")
        return grant

    def _redeem_refresh(self, form: dict) -> dict:
        token = form.get("refresh_token", "")
        used = self.grants.use("rt", token) if token else None
        if used is None:
            raise OAuthError("invalid_grant", "unknown or expired refresh token")
        grant, first = used
        if not first:  # replay of a rotated refresh token: assume theft
            self.grants.delete("family", grant["family"])
            log.warning("oauth: refresh token replayed, family revoked (client=%s)", grant["client_id"])
            raise OAuthError("invalid_grant", "refresh token already used")
        if form.get("client_id") != grant["client_id"]:
            raise OAuthError("invalid_grant", "client_id mismatch")
        if self.grants.get("family", grant["family"]) is None:
            raise OAuthError("invalid_grant", "grant revoked")
        if form.get("scope") and not set(form["scope"].split()) <= set(grant["scope"].split()) | IGNORED_SCOPES:
            raise OAuthError("invalid_scope")
        if not self._resource_ok([form["resource"]] if form.get("resource") else []):
            raise OAuthError("invalid_target")
        p = grant["principal"]
        if build_principal(self.settings, email=p["email"]) is None:  # policy changed since sign-in
            self.grants.delete("family", grant["family"])
            raise OAuthError("invalid_grant", "account no longer allowed")
        return grant
