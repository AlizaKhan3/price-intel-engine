from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_admin
from app.api.serialize import dump_doc, dump_docs
from app.db import get_priceintel_db
from app.models.tenant import TenantCreate
from app.services import usage as usage_log
from app.services.tenants import create_tenant, find_tenant_by_slug

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/tenants", summary="List tenants")
async def list_tenants():
    db = get_priceintel_db()
    items = await db.tenants.find({}).to_list(length=200)
    return {"items": dump_docs(items)}


@router.post("/tenants", summary="Onboard a new marketplace customer")
async def create_tenant_route(payload: TenantCreate):
    if await find_tenant_by_slug(payload.slug):
        raise HTTPException(status_code=409, detail=f"Tenant slug '{payload.slug}' already exists")
    doc, raw_key = await create_tenant(
        slug=payload.slug,
        name=payload.name,
        catalog=payload.catalog.model_dump(mode="json"),
        competitors=payload.competitors,
        matching=payload.matching.model_dump(mode="json"),
        alerts=payload.alerts.model_dump(mode="json"),
        storefront_base_url=payload.storefront_base_url,
    )
    body = dump_doc(doc)
    body["api_key"] = raw_key
    body["note"] = "Store this API key now. PriceIntel only keeps a hash."
    return body


@router.get("/usage", summary="Who ran compares and which product links were tried")
async def admin_usage(
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
):
    return await usage_log.list_usage(limit=limit, skip=skip)
