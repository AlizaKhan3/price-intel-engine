from __future__ import annotations

"""
Price comparison. Two sources of truth, both tenant-scoped:

1. Catalog groups — same `group_id`, different marketplace, already in Mongo.
2. Approved scraper matches — a Sadiq (or customer) product vs Daraz etc.
"""
import logging

from bson import ObjectId

from app.config import get_settings
from app.db import get_priceintel_db
from app.models.product import Alert, MatchStatus, MatchTier, PriceSnapshot
from app.services import alerts as alert_delivery
from app.services.tenants import tenant_id as tid

logger = logging.getLogger(__name__)
settings = get_settings()


def _product_filter(tenant_key: str) -> dict:
    query: dict = {"tenant_id": tenant_key}
    if settings.COMPARE_ACTIVE_ONLY:
        query["active"] = True
    if settings.COMPARE_IN_STOCK_ONLY:
        query["in_stock"] = True
    return query


async def recompute_comparisons(tenant: dict) -> list[dict]:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    results: list[dict] = []
    results.extend(await _compare_catalog_groups(db, tenant, tenant_key))
    results.extend(await _compare_approved_matches(db, tenant, tenant_key))

    await db.comparisons_cache.delete_many({"tenant_id": tenant_key})
    if results:
        await db.comparisons_cache.insert_many(results)
    return results


async def _compare_catalog_groups(db, tenant: dict, tenant_key: str) -> list[dict]:
    products = await db.catalog_products.find(_product_filter(tenant_key)).to_list(length=None)
    by_group: dict[str, list[dict]] = {}
    for product in products:
        group_id = product.get("group_id")
        if not group_id:
            continue
        by_group.setdefault(group_id, []).append(product)

    results = []
    gap_floor = (tenant.get("alerts") or {}).get("price_gap_pct", settings.ALERT_PRICE_GAP_PCT)

    for group_id, listings in by_group.items():
        if len(listings) < 2:
            continue
        listings.sort(key=lambda p: p.get("price") or 0)
        cheapest = listings[0]
        for listing in listings[1:]:
            our_price = listing.get("price") or 0
            their_price = cheapest.get("price") or 0
            if not our_price:
                continue
            gap_pct = round((our_price - their_price) / our_price * 100, 2)
            comparison = {
                "tenant_id": tenant_key,
                "product_id": listing["id"],
                "title": listing.get("title"),
                "group_id": group_id,
                "our_price": our_price,
                "our_marketplace": listing.get("marketplace"),
                "competitor": cheapest.get("marketplace") or "catalog",
                "competitor_price": their_price,
                "competitor_url": cheapest.get("url"),
                "competitor_product_id": cheapest["id"],
                "gap_pct": gap_pct,
                "source": "catalog_group",
            }
            results.append(comparison)
            await _record_snapshot(db, tenant_key, f"{tenant_key}:{listing['id']}", our_price, listing.get("in_stock", True))
            await _record_snapshot(
                db,
                tenant_key,
                f"{tenant_key}:{cheapest['id']}",
                their_price,
                cheapest.get("in_stock", True),
            )
            if gap_pct >= gap_floor:
                await _raise_alert(db, tenant, comparison)
    return results


async def _compare_approved_matches(db, tenant: dict, tenant_key: str) -> list[dict]:
    results = []
    gap_floor = (tenant.get("alerts") or {}).get("price_gap_pct", settings.ALERT_PRICE_GAP_PCT)
    cursor = db.product_matches.find(
        {
            "tenant_id": tenant_key,
            "status": MatchStatus.APPROVED.value,
            "tier": {"$ne": MatchTier.CATALOG_GROUP.value},
        }
    )
    async for match in cursor:
        product = await db.catalog_products.find_one(
            {"tenant_id": tenant_key, "id": match["product_id"]}
        )
        if match.get("peer_product_id"):
            continue
        listing_id = match.get("competitor_listing_id")
        listing = None
        if listing_id and ObjectId.is_valid(str(listing_id)):
            listing = await db.competitor_listings.find_one(
                {"tenant_id": tenant_key, "_id": ObjectId(str(listing_id))}
            )
        if listing is None:
            listing = await db.competitor_listings.find_one(
                {"tenant_id": tenant_key, "competitor_product_id": listing_id}
            )
        if not product or not listing:
            continue

        our_price = product["price"]
        their_price = listing["price"]
        gap_pct = round((our_price - their_price) / our_price * 100, 2) if our_price else 0
        comparison = {
            "tenant_id": tenant_key,
            "product_id": product["id"],
            "title": product.get("title"),
            "group_id": product.get("group_id"),
            "our_price": our_price,
            "our_marketplace": product.get("marketplace"),
            "competitor": listing.get("competitor"),
            "competitor_price": their_price,
            "competitor_url": listing.get("url"),
            "competitor_product_id": listing.get("competitor_product_id"),
            "gap_pct": gap_pct,
            "source": "scrape",
        }
        results.append(comparison)
        await _record_snapshot(db, tenant_key, f"{tenant_key}:{product['id']}", our_price, product.get("in_stock", True))
        await _record_snapshot(
            db,
            tenant_key,
            f"{listing.get('competitor')}:{listing.get('competitor_product_id')}",
            their_price,
            listing.get("in_stock", True),
        )
        if gap_pct >= gap_floor:
            await _raise_alert(db, tenant, comparison)
    return results


async def _record_snapshot(db, tenant_key: str, listing_id: str, price: float, in_stock: bool) -> None:
    snapshot = PriceSnapshot(
        tenant_id=tenant_key,
        listing_id=listing_id,
        price=price,
        in_stock=in_stock,
    )
    await db.price_snapshots.insert_one(snapshot.model_dump())


async def _raise_alert(db, tenant: dict, comparison: dict) -> None:
    tenant_key = comparison["tenant_id"]
    existing = await db.alerts.find_one(
        {
            "tenant_id": tenant_key,
            "product_id": comparison["product_id"],
            "competitor": comparison["competitor"],
            "acknowledged": False,
        }
    )
    if existing:
        return

    alert = Alert(
        tenant_id=tenant_key,
        product_id=comparison["product_id"],
        competitor=comparison["competitor"],
        our_price=comparison["our_price"],
        competitor_price=comparison["competitor_price"],
        gap_pct=comparison["gap_pct"],
    )
    await db.alerts.insert_one(alert.model_dump())
    logger.info(
        "ALERT tenant=%s %s is %.1f%% cheaper on %s",
        tenant_key,
        comparison.get("title"),
        comparison["gap_pct"],
        comparison["competitor"],
    )
    await alert_delivery.deliver(tenant, comparison)
