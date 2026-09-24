"""HTTP surface of the artifact hub, one service:

  /api/*          REST API for the web app (bearer token: OIDC, or dev identities)
  /a/{id}         sandbox content endpoint (render ticket), isolating CSP headers
  /mcp            MCP endpoint (streamable HTTP), plus its RFC 9728 metadata
  /healthz        liveness
  /*              the built SPA, when STATIC_DIR is set

Run locally:  uvicorn artifact_hub.api:create_root_app --factory --port 8080
"""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import access, sandbox
from .access import Forbidden
from .auth import Authenticator, bearer
from .auth.dev import dev_directory
from .auth.identity import Principal
from .config import Settings
from .mcp_server import build_mcp, mcp_http_app
from .search.embeddings import build_embedder
from .search.service import SearchService
from .service import ArtifactService, NotFound
from .store import MAX_BODY_BYTES, MAX_SHARED_WITH, build_store
from .store.base import ArtifactConflict, ArtifactError, ArtifactTooLarge

log = logging.getLogger("artifact_hub")


# ── request models ──────────────────────────────────────────────────────────

class MetadataFields(BaseModel):
    description: str | None = None
    source_question: str | None = None
    tags: list[str] | None = None
    sources: list[str] | None = None
    data_as_of: str | None = None
    sensitive: bool | None = None

    def meta(self) -> dict:
        return {k: v for k, v in self.model_dump().items()
                if k in MetadataFields.model_fields and v is not None}


class CreateRequest(MetadataFields):
    title: str
    kind: str = "html"
    body: str
    visibility: str = "private"
    shared_with: list[str] = Field(default_factory=list)


class UpdateRequest(MetadataFields):
    body: str | None = None   # new version
    title: str | None = None  # rename, no new version


class SharingRequest(BaseModel):
    visibility: str | None = None
    shared_with: list[str] | None = None


class RevertRequest(BaseModel):
    version: int


# ── app factory ─────────────────────────────────────────────────────────────

def create_app(settings: Settings | None = None, *, store=None, embedder=None,
               authenticator: Authenticator | None = None):
    """Returns (fastapi_app, root_asgi_app). The root app dispatches /mcp and the
    MCP metadata to the SDK app and everything else to FastAPI."""
    settings = settings or Settings.from_env()
    embedder = embedder or build_embedder(settings)
    store = store or build_store(settings, embedder)
    if store.embedder is None:
        store.embedder = embedder
    authenticator = authenticator or Authenticator(settings)
    search = SearchService(store, embedder, min_score=settings.search_min_score)
    service = ArtifactService(settings, store, search)
    mcp = build_mcp(settings, service, authenticator)
    mcp_app = mcp_http_app(settings, mcp)

    if settings.auth_mode == "dev":
        log.warning("DEV AUTH MODE: fake identities accepted (Bearer dev:<email>). Never use in a shared environment.")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Artifact Hub", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.mcp = mcp

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def baseline_headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/a/"):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            # The app itself is never framed; only /a/* is (by the app).
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    # ── errors ──────────────────────────────────────────────────────────────
    @app.exception_handler(NotFound)
    async def _nf(_r, _e):
        return JSONResponse({"detail": "artifact not found"}, status_code=404)

    @app.exception_handler(Forbidden)
    async def _fb(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(ArtifactTooLarge)
    async def _tl(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=413)

    @app.exception_handler(ArtifactConflict)
    async def _cf(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ArtifactError)
    async def _ae(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # ── auth dependency ─────────────────────────────────────────────────────
    def principal(authorization: str | None = Header(default=None)) -> Principal:
        p = authenticator.authenticate(bearer(authorization))
        if p is None:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        return p

    # ── public ──────────────────────────────────────────────────────────────
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/api/config", tags=["meta"])
    def config():
        cfg = {
            "auth_mode": settings.auth_mode,
            "publisher_group": settings.publisher_group,
            "max_body_bytes": MAX_BODY_BYTES,
            "max_shared_with": MAX_SHARED_WITH,
            "api_base": settings.public_base_url,
            "mcp_url": settings.mcp_resource_url,
        }
        if settings.auth_mode == "oidc":
            cfg["oidc"] = {"issuer": settings.oidc_issuer, "client_id": settings.oidc_client_id,
                           "scopes": settings.oidc_scopes, "ui_token": settings.oidc_ui_token}
        else:
            cfg["dev_users"] = [{"email": u["email"], "name": u["name"], "groups": u["groups"]}
                                for u in dev_directory(settings)]
        return cfg

    # ── identity & directory ────────────────────────────────────────────────
    @app.get("/api/me", tags=["meta"])
    def me(p: Principal = Depends(principal)):
        store.touch_user(p.email, p.name)  # feeds the share-dialog autocomplete
        return p.public()

    @app.get("/api/people", tags=["meta"])
    def people(q: str = Query(""), limit: int = 8, p: Principal = Depends(principal)):
        return {"people": [u for u in store.search_users(q, limit) if u["email"] != p.email]}

    # ── artifacts ───────────────────────────────────────────────────────────
    @app.get("/api/artifacts/mine", tags=["artifacts"])
    def mine(p: Principal = Depends(principal)):
        return {"artifacts": service.list_mine(p)}

    @app.get("/api/artifacts/shared", tags=["artifacts"])
    def shared_with_me(p: Principal = Depends(principal)):
        return {"artifacts": service.list_shared_with_me(p)}

    @app.get("/api/artifacts/search", tags=["artifacts"])
    def search_route(q: str = Query(..., min_length=1), limit: int = 10, p: Principal = Depends(principal)):
        return {"results": service.search(p, q, limit)}

    @app.post("/api/artifacts", tags=["artifacts"], status_code=201)
    def create(req: CreateRequest, p: Principal = Depends(principal)):
        art = service.create(p, title=req.title, kind=req.kind, body=req.body, visibility=req.visibility,
                             shared_with=req.shared_with, metadata=req.meta(), via="web")
        return {"artifact": art}

    @app.get("/api/artifacts/{artifact_id}", tags=["artifacts"])
    def get_meta(artifact_id: str, p: Principal = Depends(principal)):
        return {"artifact": service.get(p, artifact_id), "render_url": service.render_url(p, artifact_id)}

    @app.get("/api/artifacts/{artifact_id}/body", tags=["artifacts"])
    def get_body(artifact_id: str, version: int | None = None, p: Principal = Depends(principal)):
        art = service.get_with_body(p, artifact_id, version)
        return {"body": art["body"], "kind": art["kind"], "version": art["version"]}

    @app.patch("/api/artifacts/{artifact_id}", tags=["artifacts"])
    def update(artifact_id: str, req: UpdateRequest, p: Principal = Depends(principal)):
        if req.body is None and req.title is None and not req.meta():
            raise HTTPException(400, "nothing to update")
        return {"artifact": service.update(p, artifact_id, body=req.body, title=req.title, metadata=req.meta())}

    @app.put("/api/artifacts/{artifact_id}/sharing", tags=["artifacts"])
    def sharing(artifact_id: str, req: SharingRequest, p: Principal = Depends(principal)):
        return {"artifact": service.share(p, artifact_id, visibility=req.visibility, shared_with=req.shared_with)}

    @app.get("/api/artifacts/{artifact_id}/versions", tags=["artifacts"])
    def versions(artifact_id: str, p: Principal = Depends(principal)):
        return {"versions": service.versions(p, artifact_id)}

    @app.get("/api/artifacts/{artifact_id}/versions/{n}/render", tags=["artifacts"])
    def version_render(artifact_id: str, n: int, p: Principal = Depends(principal)):
        return {"render_url": service.render_url(p, artifact_id, n)}

    @app.post("/api/artifacts/{artifact_id}/revert", tags=["artifacts"])
    def revert(artifact_id: str, req: RevertRequest, p: Principal = Depends(principal)):
        return {"artifact": service.revert(p, artifact_id, req.version)}

    @app.delete("/api/artifacts/{artifact_id}", tags=["artifacts"])
    def delete(artifact_id: str, p: Principal = Depends(principal)):
        service.delete(p, artifact_id)
        return {"ok": True, "id": artifact_id}

    @app.post("/api/artifacts/{artifact_id}/views", tags=["artifacts"])
    def record_view(artifact_id: str, p: Principal = Depends(principal)):
        return service.record_view(p, artifact_id)

    # ── sandbox content endpoint ────────────────────────────────────────────
    headers = sandbox.security_headers(settings.app_origin)

    def refused() -> Response:
        # One shape for absent, private, expired ticket and unauthenticated.
        return PlainTextResponse("Not available", status_code=404, headers=headers)

    def render(artifact_id: str, n: int | None, t: str, authorization: str | None) -> Response:
        email = sandbox.verify_ticket(settings.render_ticket_secret, t, artifact_id, n) if t else None
        p: Principal | None = Principal(email=email) if email else authenticator.authenticate(bearer(authorization))
        art = store.get_artifact(artifact_id)
        if p is None or not access.can_open_version(p, art, n):
            return refused()
        body = store.get_version_body(artifact_id, n)
        if body is None:
            return refused()
        return Response(content=sandbox.document(art.get("kind", "text"), body),
                        media_type="text/html; charset=utf-8", headers=headers)

    @app.get("/a/{artifact_id}", include_in_schema=False)
    def sandbox_current(artifact_id: str, t: str = "", authorization: str | None = Header(default=None)):
        return render(artifact_id, None, t, authorization)

    @app.get("/a/{artifact_id}/v/{n}", include_in_schema=False)
    def sandbox_version(artifact_id: str, n: int, t: str = "", authorization: str | None = Header(default=None)):
        return render(artifact_id, n, t, authorization)

    # ── SPA (production image) ──────────────────────────────────────────────
    static = Path(settings.static_dir) if settings.static_dir else None
    if static and (static / "index.html").exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "a/")):
                raise HTTPException(404)
            candidate = (static / path).resolve()
            if path and candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    async def root(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path in ("/mcp", "/mcp/") or path.startswith("/.well-known/oauth-protected-resource"):
                if path == "/mcp/":
                    scope = {**scope, "path": "/mcp"}
                await mcp_app(scope, receive, send)
                return
        await app(scope, receive, send)

    return app, root


def create_root_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    return create_app()[1]
