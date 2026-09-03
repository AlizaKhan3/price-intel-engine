from __future__ import annotations

"""
MongoDB connections.

`priceintel_db` is the platform database (tenants, matches, snapshots).
Each tenant's catalog is a *separate* Mongo connection, resolved from the
tenant record so Customer B never reads Customer A's products.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

settings = get_settings()

_priceintel_client: AsyncIOMotorClient | None = None
_catalog_clients: dict[str, AsyncIOMotorClient] = {}


def get_priceintel_db() -> AsyncIOMotorDatabase:
    global _priceintel_client
    if _priceintel_client is None:
        _priceintel_client = AsyncIOMotorClient(settings.PRICEINTEL_MONGO_URI)
    return _priceintel_client[settings.PRICEINTEL_DB_NAME]


def get_catalog_db(tenant: dict) -> AsyncIOMotorDatabase:
    catalog = tenant.get("catalog") or {}
    uri = catalog.get("mongo_uri") or settings.CATALOG_MONGO_URI
    db_name = catalog.get("db_name") or settings.CATALOG_DB_NAME
    if uri not in _catalog_clients:
        _catalog_clients[uri] = AsyncIOMotorClient(uri)
    return _catalog_clients[uri][db_name]


async def ensure_indexes() -> None:
    db = get_priceintel_db()

    await db.tenants.create_index("slug", unique=True)
    await db.tenants.create_index("api_key_hash", unique=True)

    await db.catalog_products.create_index([("tenant_id", 1), ("id", 1)], unique=True)
    await db.catalog_products.create_index([("tenant_id", 1), ("group_id", 1)])
    await db.catalog_products.create_index([("tenant_id", 1), ("category", 1)])
    await db.catalog_products.create_index([("tenant_id", 1), ("marketplace_id", 1)])

    await db.competitor_listings.create_index(
        [("tenant_id", 1), ("competitor", 1), ("competitor_product_id", 1)],
        unique=True,
    )
    await db.competitor_listings.create_index([("tenant_id", 1), ("last_scraped_at", 1)])

    await db.product_matches.create_index(
        [("tenant_id", 1), ("product_id", 1), ("competitor_listing_id", 1)],
        unique=True,
    )
    await db.product_matches.create_index([("tenant_id", 1), ("status", 1)])

    try:
        await db.create_collection(
            "price_snapshots",
            timeseries={
                "timeField": "recorded_at",
                "metaField": "listing_id",
                "granularity": "hours",
            },
        )
    except Exception:
        pass

    await db.price_snapshots.create_index([("listing_id", 1), ("recorded_at", -1)])
    await db.alerts.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.comparisons_cache.create_index([("tenant_id", 1), ("gap_pct", -1)])
