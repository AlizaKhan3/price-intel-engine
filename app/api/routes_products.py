from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.deps import get_current_tenant
from app.api.serialize import dump_doc, dump_docs
from app.db import get_priceintel_db
from app.services.catalog_sync import sync_full_catalog
from app.services.tenants import tenant_id as tid

router = APIRouter(tags=["catalog"])


@router.get(
    "/me",
    summary="Who am I",
    description="Returns the authenticated tenant. Use this to verify an API key.",
)
async def me(tenant: dict = Depends(get_current_tenant)):
    return {
        "id": tid(tenant),
        "slug": tenant.get("slug"),
        "name": tenant.get("name"),
        "status": tenant.get("status"),
        "competitors": tenant.get("competitors") or [],
        "matching": tenant.get("matching"),
        "storefront_base_url": tenant.get("storefront_base_url"),
    }


@router.post(
    "/catalog/sync",
    summary="Sync catalog",
    description=(
        "Pull products from your MongoDB into PriceIntel. "
        "A full catalog (~14k products on a remote cluster) takes minutes, so by default "
        "this returns immediately and runs in the background. "
        "For a first Swagger test set limit=200. Poll GET /v1/catalog/status."
    ),
)
async def trigger_catalog_sync(
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(get_current_tenant),
    wait: bool = Query(
        default=False,
        description="Block until sync finishes. Leave false in Swagger so the UI does not hang.",
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=50000,
        description="Sync only this many products. Use 200 for a smoke test.",
    ),
):
    if wait:
        return await sync_full_catalog(tenant, limit=limit)
    background_tasks.add_task(sync_full_catalog, tenant, limit=limit)
    return {
        "status": "started",
        "message": "Sync running in the background. Watch the server terminal, then GET /v1/catalog/status or GET /v1/products.",
        "limit": limit,
    }


@router.get("/catalog/status", summary="Latest catalog sync status")
async def catalog_status(tenant: dict = Depends(get_current_tenant)):
    db = get_priceintel_db()
    run = await db.sync_runs.find_one({"tenant_id": tid(tenant), "job": "catalog"})
    products = await db.catalog_products.count_documents({"tenant_id": tid(tenant)})
    body = dump_doc(run) or {"status": "never_run"}
    body["products_in_cache"] = products
    return body


@router.get("/products", summary="List synced products")
async def list_products(
    tenant: dict = Depends(get_current_tenant),
    category: str | None = None,
    marketplace: str | None = None,
    group_id: str | None = None,
    active: bool | None = None,
    q: str | None = Query(default=None, description="Case-insensitive title search"),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    db = get_priceintel_db()
    query: dict = {"tenant_id": tid(tenant)}
    if category:
        query["category"] = category
    if marketplace:
        query["marketplace"] = marketplace
    if group_id:
        query["group_id"] = group_id
    if active is not None:
        query["active"] = active
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    cursor = db.catalog_products.find(query).skip(skip).limit(limit)
    products = await cursor.to_list(length=limit)
    total = await db.catalog_products.count_documents(query)
    return {"total": total, "items": dump_docs(products)}


@router.get("/products/{product_id}", summary="Get one product")
async def get_product(product_id: str, tenant: dict = Depends(get_current_tenant)):
    db = get_priceintel_db()
    product = await db.catalog_products.find_one({"tenant_id": tid(tenant), "id": product_id})
    return dump_doc(product)


@router.get(
    "/groups/{group_id}",
    summary="Listings in a product group",
    description="Same item sold by multiple marketplaces in your catalog, with prices side by side.",
)
async def get_group(group_id: str, tenant: dict = Depends(get_current_tenant)):
    db = get_priceintel_db()
    items = await db.catalog_products.find(
        {"tenant_id": tid(tenant), "group_id": group_id}
    ).to_list(length=None)
    prices = [p["price"] for p in items if p.get("price")]
    return {
        "group_id": group_id,
        "count": len(items),
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "items": dump_docs(items),
    }
