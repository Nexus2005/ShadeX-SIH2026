"""FastAPI application entrypoint."""
import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import SessionFactory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

from app.routers.application_routes import (  # noqa: E402
    applications_router,
    dashboard_router,
    document_router,
    documents_router,
    institutions_router,
)
from app.routers.workflow_routes import (  # noqa: E402
    audit_router,
    fields_router,
    findings_router,
    reports_router,
    rules_router,
    system_router,
    verification_router,
)
from app.routers.auth import router as auth_router  # noqa: E402

settings = get_settings()

app = FastAPI(
    title="Resilient AI",
    version="0.1.0",
    description="AICTE application validation, document authentication and compliance assistance (SIH 2026).",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request_id_ctx.set(request_id)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(
            {"detail": exc.detail, "request_id": request_id},
            status_code=exc.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("resilient.api").exception("unhandled error on %s", request.url.path)
        response = JSONResponse(
            {"detail": "Internal server error; see logs", "request_id": request_id},
            status_code=500,
        )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/api/health", tags=["system"])
def root_health():
    return {"ok": True, "service": "resilient-ai", "version": app.version}


prefix = "/api/v1"
for router in (
    auth_router,
    institutions_router,
    applications_router,
    documents_router,
    document_router,
    dashboard_router,
    findings_router,
    fields_router,
    rules_router,
    verification_router,
    reports_router,
    audit_router,
    system_router,
):
    app.include_router(router, prefix=prefix)
