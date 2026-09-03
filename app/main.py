from __future__ import annotations

"""
PriceIntel API.

Customer docs: /docs (Swagger) and /redoc
Markdown copy for customers: docs/customer-api.md
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import (
    routes_admin,
    routes_alerts,
    routes_comparisons,
    routes_matches,
    routes_products,
)
from app.config import get_settings
from app.db import ensure_indexes
from app.services.tenants import seed_default_tenant

logging.basicConfig(level=logging.INFO)
settings = get_settings()

OPENAPI_DESCRIPTION = """
PriceIntel compares a marketplace's catalog against competitor listings,
tracks prices over time, and alerts when someone is cheaper.

**Auth:** send your tenant API key as `Authorization: Bearer <key>` or `X-API-Key: <key>`.

Every response is scoped to your tenant. You cannot see another customer's data.

Interactive docs stay at `/docs`. The customer-facing reference is `docs/customer-api.md`.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await ensure_indexes()
    await seed_default_tenant()
    yield


app = FastAPI(
    title="PriceIntel API",
    description=OPENAPI_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    contact={"name": "Sadiq.ai", "email": "dev@sadiq.ai", "url": "https://www.sadiq.ai"},
    license_info={"name": "Proprietary"},
    servers=[
        {"url": "http://localhost:8000", "description": "Local"},
        {"url": "https://api.priceintel.sadiq.ai", "description": "Production"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    code = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
    }.get(exc.status_code, "error")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": exc.detail}},
    )


v1 = "/v1"
app.include_router(routes_products.router, prefix=v1)
app.include_router(routes_matches.router, prefix=v1)
app.include_router(routes_comparisons.router, prefix=v1)
app.include_router(routes_alerts.router, prefix=v1)
app.include_router(routes_admin.router, prefix=v1)


@app.get("/health", tags=["meta"], summary="Liveness probe")
async def health():
    return {"status": "ok", "service": "priceintel"}


@app.get("/v1/health", tags=["meta"], summary="Authenticated health")
async def health_v1():
    return {"status": "ok", "version": "1.0.0"}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "BearerAuth": {"type": "http", "scheme": "bearer"},
    }
    schema["security"] = [{"ApiKeyAuth": []}, {"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
