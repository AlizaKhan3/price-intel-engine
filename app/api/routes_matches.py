from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_tenant
from app.api.serialize import dump_doc
from app.db import get_priceintel_db
from app.models.product import MatchStatus
from app.services.tenants import tenant_id as tid

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("/pending", summary="Human review queue")
async def list_pending_matches(
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(default=50, ge=1, le=200),
):
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    cursor = db.product_matches.find(
        {"tenant_id": tenant_key, "status": MatchStatus.PENDING.value}
    ).limit(limit)
    matches = await cursor.to_list(length=limit)

    enriched = []
    for match in matches:
        product = await db.catalog_products.find_one(
            {"tenant_id": tenant_key, "id": match["product_id"]}
        )
        listing = None
        listing_id = match.get("competitor_listing_id")
        if listing_id and ObjectId.is_valid(str(listing_id)):
            listing = await db.competitor_listings.find_one(
                {"tenant_id": tenant_key, "_id": ObjectId(str(listing_id))}
            )
        if listing is None and match.get("peer_product_id"):
            listing = await db.catalog_products.find_one(
                {"tenant_id": tenant_key, "id": match["peer_product_id"]}
            )
        enriched.append(
            {
                "match_id": str(match["_id"]),
                "confidence": match.get("confidence"),
                "tier": match.get("tier"),
                "our_product": dump_doc(product),
                "competitor_listing": dump_doc(listing),
            }
        )
    return {"items": enriched}


@router.post("/{match_id}/approve", summary="Approve a candidate match")
async def approve_match(
    match_id: str,
    reviewed_by: str = "api",
    tenant: dict = Depends(get_current_tenant),
):
    return await _set_status(match_id, tenant, MatchStatus.APPROVED, reviewed_by)


@router.post("/{match_id}/reject", summary="Reject a candidate match")
async def reject_match(
    match_id: str,
    reviewed_by: str = "api",
    tenant: dict = Depends(get_current_tenant),
):
    return await _set_status(match_id, tenant, MatchStatus.REJECTED, reviewed_by)


async def _set_status(match_id: str, tenant: dict, status: MatchStatus, reviewed_by: str):
    db = get_priceintel_db()
    result = await db.product_matches.update_one(
        {"_id": ObjectId(match_id), "tenant_id": tid(tenant)},
        {
            "$set": {
                "status": status.value,
                "reviewed_by": reviewed_by,
                "reviewed_at": datetime.utcnow(),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Match not found")
    return {"ok": True, "status": status.value}
