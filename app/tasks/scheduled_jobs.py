from __future__ import annotations

"""
The actual scheduled jobs. Each job runs for every active tenant so a
second customer is picked up automatically after they are onboarded.
"""
import asyncio
import logging

from app.db import get_priceintel_db
from app.scrapers.registry import get_scraper
from app.services import catalog_sync, comparison
from app.services.matching.pipeline import find_best_match
from app.services.tenants import list_active_tenants, tenant_id as tid
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def sync_catalog_task():
    return asyncio.run(_for_each_tenant(catalog_sync.sync_full_catalog))


@celery_app.task
def scrape_competitors_task():
    return asyncio.run(_for_each_tenant(_scrape_one_tenant))


@celery_app.task
def run_matching_task():
    return asyncio.run(_for_each_tenant(_run_matching))


@celery_app.task
def recompute_comparisons_task():
    return asyncio.run(_for_each_tenant(comparison.recompute_comparisons))


async def _for_each_tenant(fn):
    results = {}
    for tenant in await list_active_tenants():
        key = tid(tenant)
        try:
            results[key] = await fn(tenant)
        except Exception:
            logger.exception("Job failed for tenant=%s", key)
            results[key] = {"error": True}
    return results


async def _scrape_one_tenant(tenant: dict) -> int:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    products = await db.catalog_products.find(
        {"tenant_id": tenant_key, "active": True}
    ).to_list(length=None)

    total_found = 0
    competitors = tenant.get("competitors") or ["daraz"]
    for competitor in competitors:
        scraper = get_scraper(competitor)
        queries = [p["title"] for p in products if p.get("title")]
        results = await scraper.run_batch(queries)

        for listings in results.values():
            for listing in listings:
                payload = listing.model_dump()
                payload["tenant_id"] = tenant_key
                await db.competitor_listings.update_one(
                    {
                        "tenant_id": tenant_key,
                        "competitor": listing.competitor,
                        "competitor_product_id": listing.competitor_product_id,
                    },
                    {"$set": payload},
                    upsert=True,
                )
                total_found += 1

    logger.info("Scraping tenant=%s listings=%d", tenant_key, total_found)
    return total_found


async def _run_matching(tenant: dict) -> int:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    matching = tenant.get("matching") or {}
    products = await db.catalog_products.find({"tenant_id": tenant_key}).to_list(length=None)

    matched = 0
    for product in products:
        candidates = await db.competitor_listings.find(
            {"tenant_id": tenant_key, "category": product.get("category")}
        ).to_list(length=None)
        if not candidates:
            candidates = await db.competitor_listings.find({"tenant_id": tenant_key}).to_list(
                length=500
            )
        for candidate in candidates:
            candidate["id"] = str(candidate["_id"])

        decision = find_best_match(
            product,
            candidates,
            min_score=matching.get("min_score"),
        )
        if not decision:
            continue

        decision["tenant_id"] = tenant_key
        await db.product_matches.update_one(
            {
                "tenant_id": tenant_key,
                "product_id": decision["product_id"],
                "competitor_listing_id": decision["competitor_listing_id"],
            },
            {"$set": decision},
            upsert=True,
        )
        matched += 1

    logger.info("Matching tenant=%s matches=%d", tenant_key, matched)
    return matched
