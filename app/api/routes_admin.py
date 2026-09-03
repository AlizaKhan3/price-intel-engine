from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_admin
from app.api.serialize import dump_doc, dump_docs
from app.db import get_priceintel_db
from app.models.tenant import TenantCreate
from app.services.tenants import create_tenant, find_tenant_by_slug

router = APIRouter(prefix="/admin/tenants", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", summary="List tenants")
async def list_tenants():
    db = get_priceintel_db()
    items = await db.tenants.find({}).to_list(length=200)
    return {"items": dump_docs(items)}


@router.post("", summary="Onboard a new marketplace customer")
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
