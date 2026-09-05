from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_tenant
from app.services import automation, discovery
from app.services import usage as usage_log
from app.services.tenants import tenant_id as tid

router = APIRouter(prefix="/automation", tags=["automation"])


class BulkBody(BaseModel):
    mappings: str = Field(
        ...,
        description=(
            "CSV or one pair per line. "
            "Columns: product_id (or storefront_url) and competitor_url. "
            "A downloaded unmapped.csv with competitor_url filled in also works."
        ),
    )


@router.get("/coverage", summary="How many products already have an external competitor URL")
async def get_coverage(tenant: dict = Depends(get_current_tenant)):
    return await automation.coverage(tenant)


@router.get("/unmapped", summary="Active products with no external competitor mapping yet")
async def get_unmapped(
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(default=50, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
):
    return await automation.list_unmapped(tenant, limit=limit, skip=skip)


@router.get("/unmapped.csv", summary="Download unmapped products so you can fill competitor URLs")
async def get_unmapped_csv(
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(default=2000, ge=1, le=10000),
    skip: int = Query(default=0, ge=0),
):
    data = await automation.list_unmapped(tenant, limit=limit, skip=skip)
    return Response(
        content=automation.unmapped_csv(data["items"]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="unmapped-products.csv"'},
    )


@router.post("/bulk", summary="Import many product → competitor URL pairs")
async def bulk_import(
    body: BulkBody,
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(get_current_tenant),
    wait: bool = Query(default=True),
):
    if wait:
        try:
            return await automation.import_mappings(tenant, body.mappings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        pairs = automation.parse_mapping_lines(body.mappings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(automation.import_mappings, tenant, body.mappings)
    return {
        "status": "started",
        "pairs": len(pairs),
        "message": f"Importing {len(pairs)} mappings in the background.",
    }


class DiscoverBody(BaseModel):
    product_id: str | None = Field(default=None)
    storefront_url: str | None = Field(
        default=None,
        description="Your product page. Used if product_id is omitted.",
    )
    actor: str | None = Field(
        default=None,
        description="Optional name for the internal usage log.",
    )


@router.post(
    "/discover",
    summary="Search the web for the same product and compare every match",
)
async def discover_one(
    body: DiscoverBody,
    request: Request,
    tenant: dict = Depends(get_current_tenant),
):
    try:
        if body.storefront_url:
            result = await discovery.discover_from_storefront(tenant, body.storefront_url)
        elif body.product_id:
            result = await discovery.discover_product(tenant, body.product_id)
        else:
            raise ValueError("Provide product_id or storefront_url.")
        summary = usage_log.summarize_result(result)
        await usage_log.log_usage(
            action="discover",
            storefront_url=body.storefront_url
            or (result.get("our_product") or {}).get("url")
            or "",
            actor=body.actor,
            source="api",
            tenant_id=tid(tenant),
            request=request,
            success=True,
            **summary,
        )
        return result
    except ValueError as exc:
        await usage_log.log_usage(
            action="discover",
            storefront_url=body.storefront_url or "",
            product_id=body.product_id,
            actor=body.actor,
            source="api",
            tenant_id=tid(tenant),
            request=request,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/discover-unmapped",
    summary="Auto-search the web for the next few unmapped products",
)
async def discover_unmapped(
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(default=1, ge=1, le=5),
):
    return await discovery.discover_unmapped(tenant, limit=limit)


@router.post("/refresh", summary="Re-check prices for products that already have a competitor URL")
async def refresh(
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(get_current_tenant),
    wait: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
):
    if wait:
        return await automation.refresh_mapped_prices(tenant, limit=limit)
    background_tasks.add_task(automation.refresh_mapped_prices, tenant, limit)
    return {
        "status": "started",
        "message": f"Refreshing up to {limit} saved competitor URLs in the background.",
        "limit": limit,
    }
