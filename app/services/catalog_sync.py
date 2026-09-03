from __future__ import annotations

"""
Catalog sync — copies a tenant's own products into PriceIntel.

Sadiq already stores every marketplace listing in `products`, keyed by
`group_id` (same item across sellers) and `marketplace`. After a sync we
also materialize those intra-catalog relationships as auto-approved matches
so comparisons work before any external scraper runs.
"""
import logging
from datetime import datetime

from pymongo import UpdateOne

from app.catalog.mapper import project_product
from app.config import get_settings
from app.db import get_catalog_db, get_priceintel_db
from app.models.product import MatchStatus, MatchTier
from app.services.tenants import tenant_id as tid

logger = logging.getLogger(__name__)
settings = get_settings()


async def _name_map(collection, name_fields: tuple[str, ...] = ("name", "title", "label")) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if collection is None:
        return mapping
    try:
        cursor = collection.find({})
        async for doc in cursor:
            label = None
            for field in name_fields:
                if doc.get(field):
                    label = str(doc[field])
                    break
            mapping[str(doc["_id"])] = label or str(doc["_id"])
    except Exception:
        logger.warning("Could not load lookup collection %s", getattr(collection, "name", "?"))
    return mapping


async def sync_full_catalog(
    tenant: dict,
    batch_size: int = 500,
    limit: int | None = None,
) -> dict:
    catalog = tenant.get("catalog") or {}
    tenant_key = tid(tenant)
    source = get_catalog_db(tenant)
    dest = get_priceintel_db()
    started = datetime.utcnow()
    await dest.sync_runs.update_one(
        {"tenant_id": tenant_key, "job": "catalog"},
        {"$set": {"status": "running", "started_at": started, "finished_at": None, "error": None, "limit": limit}},
        upsert=True,
    )

    try:
        result = await _sync_full_catalog_inner(
            tenant, batch_size=batch_size, limit=limit, source=source, dest=dest
        )
    except Exception as exc:
        logger.exception("Catalog sync failed tenant=%s", tenant_key)
        await dest.sync_runs.update_one(
            {"tenant_id": tenant_key, "job": "catalog"},
            {"$set": {"status": "failed", "finished_at": datetime.utcnow(), "error": str(exc)}},
        )
        raise

    result["started_at"] = started.isoformat() + "Z"
    result["finished_at"] = datetime.utcnow().isoformat() + "Z"
    await dest.sync_runs.update_one(
        {"tenant_id": tenant_key, "job": "catalog"},
        {"$set": {"status": "done", "finished_at": datetime.utcnow(), "result": result}},
    )
    return result


async def _sync_full_catalog_inner(
    tenant: dict,
    *,
    batch_size: int,
    limit: int | None,
    source,
    dest,
) -> dict:
    catalog = tenant.get("catalog") or {}
    tenant_key = tid(tenant)

    products_col = catalog.get("products_collection") or settings.CATALOG_PRODUCTS_COLLECTION
    categories_col = catalog.get("categories_collection") or settings.CATALOG_CATEGORIES_COLLECTION
    marketplaces_col = catalog.get("marketplaces_collection") or settings.CATALOG_MARKETPLACES_COLLECTION

    logger.info("Catalog sync starting tenant=%s limit=%s", tenant_key, limit)
    category_names = await _name_map(source[categories_col])
    marketplace_names = await _name_map(source[marketplaces_col])

    query: dict = {}
    if catalog.get("sync_active_only", settings.CATALOG_SYNC_ACTIVE_ONLY):
        query["active"] = True

    count = 0
    skipped = 0
    batch: list[dict] = []
    cache = dest.catalog_products
    url_template = catalog.get("product_url_template") or settings.STOREFRONT_PRODUCT_URL_TEMPLATE
    field_map = catalog.get("field_map") or {}

    cursor = source[products_col].find(query).batch_size(batch_size)
    if limit:
        cursor = cursor.limit(limit)

    async for raw in cursor:
        if "_id" not in raw:
            skipped += 1
            continue
        doc = project_product(
            raw,
            tenant_id=tenant_key,
            field_map=field_map,
            category_names=category_names,
            marketplace_names=marketplace_names,
            product_url_template=url_template,
        )
        batch.append(doc)
        count += 1
        if len(batch) >= batch_size:
            await _upsert_batch(cache, batch)
            batch = []
            logger.info("Catalog sync tenant=%s progress=%d", tenant_key, count)

    if batch:
        await _upsert_batch(cache, batch)

    logger.info("Catalog sync tenant=%s products upserted=%d, building group matches", tenant_key, count)
    group_matches = await _upsert_group_matches(dest, tenant_key)

    logger.info(
        "Catalog sync complete tenant=%s products=%d group_matches=%d",
        tenant_key,
        count,
        group_matches,
    )
    return {
        "tenant": tenant_key,
        "products_synced": count,
        "skipped": skipped,
        "categories_resolved": len(category_names),
        "marketplaces_resolved": len(marketplace_names),
        "group_matches_upserted": group_matches,
    }


async def _upsert_batch(cache, batch: list[dict]) -> None:
    ops = [
        UpdateOne(
            {"tenant_id": doc["tenant_id"], "id": doc["id"]},
            {"$set": doc},
            upsert=True,
        )
        for doc in batch
    ]
    if ops:
        await cache.bulk_write(ops, ordered=False)


async def _upsert_group_matches(db, tenant_key: str) -> int:
    """
    Products that already share `group_id` in the tenant catalog are the
    same item sold by different marketplaces. Treat that as a 100-confidence
    auto-approved match so price gaps show up on the first sync.
    """
    pipeline = [
        {"$match": {"tenant_id": tenant_key, "group_id": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$group_id", "ids": {"$addToSet": "$id"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 2}}},
    ]
    created = 0
    now = datetime.utcnow()
    ops: list[UpdateOne] = []
    async for group in db.catalog_products.aggregate(pipeline):
        ids = sorted(group["ids"])
        canonical = ids[0]
        for other_id in ids[1:]:
            listing_key = f"catalog:{other_id}"
            ops.append(
                UpdateOne(
                    {
                        "tenant_id": tenant_key,
                        "product_id": canonical,
                        "competitor_listing_id": listing_key,
                    },
                    {
                        "$setOnInsert": {
                            "tenant_id": tenant_key,
                            "product_id": canonical,
                            "competitor_listing_id": listing_key,
                            "peer_product_id": other_id,
                            "tier": MatchTier.CATALOG_GROUP.value,
                            "confidence": 100.0,
                            "status": MatchStatus.APPROVED.value,
                            "created_at": now,
                        }
                    },
                    upsert=True,
                )
            )
            if len(ops) >= 500:
                result = await db.product_matches.bulk_write(ops, ordered=False)
                created += result.upserted_count
                ops = []
    if ops:
        result = await db.product_matches.bulk_write(ops, ordered=False)
        created += result.upserted_count
    return created


async def watch_catalog_changes(tenant: dict) -> None:
    catalog = tenant.get("catalog") or {}
    tenant_key = tid(tenant)
    source = get_catalog_db(tenant)
    cache = get_priceintel_db().catalog_products
    products_col = catalog.get("products_collection") or settings.CATALOG_PRODUCTS_COLLECTION
    url_template = catalog.get("product_url_template") or settings.STOREFRONT_PRODUCT_URL_TEMPLATE
    field_map = catalog.get("field_map") or {}

    pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]
    async with source[products_col].watch(
        pipeline, full_document="updateLookup"
    ) as stream:
        async for change in stream:
            full_doc = change.get("fullDocument")
            if not full_doc:
                continue
            doc = project_product(
                full_doc,
                tenant_id=tenant_key,
                field_map=field_map,
                product_url_template=url_template,
            )
            await cache.update_one(
                {"tenant_id": doc["tenant_id"], "id": doc["id"]},
                {"$set": doc},
                upsert=True,
            )
            logger.info("Re-synced %s / %s", tenant_key, doc["id"])
