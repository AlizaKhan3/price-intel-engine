from __future__ import annotations

"""Internal usage log — who ran compare/discover, when, and which product URL."""
from datetime import datetime
from typing import Any

from fastapi import Request

from app.db import get_priceintel_db


async def log_usage(
    *,
    action: str,
    storefront_url: str = "",
    competitor_url: str = "",
    product_id: str | None = None,
    product_title: str | None = None,
    our_price: float | None = None,
    match_count: int | None = None,
    searched_urls: int | None = None,
    skipped: int | None = None,
    success: bool = True,
    error: str | None = None,
    actor: str | None = None,
    source: str = "ui",
    tenant_id: str | None = None,
    request: Request | None = None,
) -> None:
    db = get_priceintel_db()
    ip = None
    user_agent = None
    if request is not None:
        ip = (
            (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        )
        user_agent = (request.headers.get("user-agent") or "")[:300]

    doc: dict[str, Any] = {
        "action": action,
        "storefront_url": (storefront_url or "")[:500],
        "competitor_url": (competitor_url or "")[:500] or None,
        "product_id": product_id,
        "product_title": (product_title or "")[:200] or None,
        "our_price": our_price,
        "match_count": match_count,
        "searched_urls": searched_urls,
        "skipped": skipped,
        "success": success,
        "error": (error or "")[:400] or None,
        "actor": (actor or "").strip()[:80] or None,
        "source": source,
        "tenant_id": tenant_id,
        "ip": ip,
        "user_agent": user_agent,
        "created_at": datetime.utcnow(),
    }
    try:
        await db.usage_events.insert_one(doc)
    except Exception:
        # Never break compare/discover because the audit log failed.
        import logging

        logging.getLogger(__name__).exception("Failed to write usage_events")


async def list_usage(limit: int = 100, skip: int = 0) -> dict:
    db = get_priceintel_db()
    limit = max(1, min(limit, 500))
    skip = max(0, skip)
    total = await db.usage_events.count_documents({})
    cursor = (
        db.usage_events.find({}, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    items = []
    async for row in cursor:
        created = row.get("created_at")
        if isinstance(created, datetime):
            row["created_at"] = created.isoformat() + "Z"
        items.append(row)
    return {"total": total, "count": len(items), "items": items}


def summarize_result(result: dict | None) -> dict:
    if not result:
        return {}
    ours = result.get("our_product") or {}
    if result.get("matches") is not None:
        match_count = (
            result.get("match_count")
            if result.get("match_count") is not None
            else len(result.get("matches") or [])
        )
    elif result.get("competitor_listing"):
        match_count = 1
    else:
        match_count = None
    return {
        "product_id": result.get("product_id") or ours.get("id"),
        "product_title": ours.get("title"),
        "our_price": ours.get("price"),
        "match_count": match_count,
        "searched_urls": len(result.get("searched_urls") or []),
        "skipped": len(result.get("skipped") or []),
    }
