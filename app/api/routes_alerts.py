from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_tenant
from app.api.serialize import dump_docs
from app.db import get_priceintel_db
from app.services.tenants import tenant_id as tid

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", summary="List price-gap alerts")
async def list_alerts(
    tenant: dict = Depends(get_current_tenant),
    acknowledged: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = get_priceintel_db()
    cursor = (
        db.alerts.find({"tenant_id": tid(tenant), "acknowledged": acknowledged})
        .sort("created_at", -1)
        .limit(limit)
    )
    return {"items": dump_docs(await cursor.to_list(length=limit))}


@router.post("/{alert_id}/acknowledge", summary="Acknowledge an alert")
async def acknowledge_alert(alert_id: str, tenant: dict = Depends(get_current_tenant)):
    db = get_priceintel_db()
    result = await db.alerts.update_one(
        {"_id": ObjectId(alert_id), "tenant_id": tid(tenant)},
        {"$set": {"acknowledged": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"ok": True}
