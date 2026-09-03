from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_tenant
from app.api.serialize import dump_docs
from app.db import get_priceintel_db
from app.services.comparison import recompute_comparisons
from app.services.tenants import tenant_id as tid

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


@router.get("", summary="Latest price comparisons")
async def list_comparisons(
    tenant: dict = Depends(get_current_tenant),
    min_gap_pct: float | None = None,
    competitor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = get_priceintel_db()
    query: dict = {"tenant_id": tid(tenant)}
    if min_gap_pct is not None:
        query["gap_pct"] = {"$gte": min_gap_pct}
    if competitor:
        query["competitor"] = competitor
    cursor = db.comparisons_cache.find(query).sort("gap_pct", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"items": dump_docs(items)}


@router.post("/recompute", summary="Recompute comparisons now")
async def trigger_recompute(tenant: dict = Depends(get_current_tenant)):
    results = await recompute_comparisons(tenant)
    return {"comparisons_computed": len(results)}


@router.get("/{product_id}/history", summary="Price history for a product")
async def price_history(
    product_id: str,
    tenant: dict = Depends(get_current_tenant),
    days: int = Query(default=30, ge=1, le=365),
):
    db = get_priceintel_db()
    since = datetime.utcnow() - timedelta(days=days)
    tenant_key = tid(tenant)
    cursor = db.price_snapshots.find(
        {
            "listing_id": {"$regex": f"{product_id}$"},
            "recorded_at": {"$gte": since},
            "tenant_id": tenant_key,
        }
    ).sort("recorded_at", 1)
    return {"items": dump_docs(await cursor.to_list(length=None))}
