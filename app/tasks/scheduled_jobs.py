from __future__ import annotations

"""
Scheduled jobs. Catalog sync is automatic. External competitor scraping
only *refreshes URLs you already saved* — it does not search Daraz.
"""
import asyncio
import logging

from app.services import automation, catalog_sync, comparison, discovery
from app.services.tenants import list_active_tenants, tenant_id as tid
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def sync_catalog_task():
    return asyncio.run(_for_each_tenant(catalog_sync.sync_full_catalog))


@celery_app.task
def scrape_competitors_task():
    return asyncio.run(_for_each_tenant(_refresh_tenant))


@celery_app.task
def run_matching_task():
    return asyncio.run(_for_each_tenant(_discover_tenant))


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


async def _refresh_tenant(tenant: dict) -> dict:
    return await automation.refresh_mapped_prices(tenant, limit=200)


async def _discover_tenant(tenant: dict) -> dict:
    return await discovery.discover_unmapped(tenant, limit=3)
