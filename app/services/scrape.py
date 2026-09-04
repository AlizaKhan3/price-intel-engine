from __future__ import annotations

"""Scrape one competitor product page and attach it to one of your products."""
import asyncio
import logging
from datetime import datetime

from app.config import get_settings
from app.db import get_priceintel_db
from app.models.product import CompetitorListing
from app.models.product import MatchStatus, MatchTier
from app.scrapers.registry import get_scraper
from app.services.catalog_sync import sync_full_catalog
from app.services.compare_summary import explain_prices
from app.services.matching.pipeline import find_best_match
from app.services.tenants import tenant_id as tid
from app.services.urls import competitor_from_url, competitor_label, product_id_from_storefront_url

logger = logging.getLogger(__name__)


async def fetch_competitor_listing(competitor: str, competitor_url: str) -> CompetitorListing:
    rows = await fetch_competitor_listings([(competitor, competitor_url)])
    _, listing, error = rows[0]
    if listing is None:
        raise RuntimeError(
            error
            or "Could not read title/price from that competitor page. Try another product URL."
        )
    return listing


async def fetch_competitor_listings(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, CompetitorListing | None, str | None]]:
    """Open one browser and fetch many product pages."""
    from playwright.async_api import async_playwright

    settings = get_settings()
    results: list[tuple[str, CompetitorListing | None, str | None]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=settings.SCRAPER_USER_AGENT)
        try:
            for i, (competitor, url) in enumerate(pairs):
                try:
                    listing = await get_scraper(competitor).fetch_product(page, url)
                    results.append((url, listing, None if listing else "No title/price on that page"))
                except Exception as exc:
                    logger.warning("Fetch failed %s: %s", url, exc)
                    results.append((url, None, str(exc)))
                if i < len(pairs) - 1:
                    await asyncio.sleep(settings.SCRAPER_REQUEST_DELAY_SECONDS)
        finally:
            await browser.close()
    return results


async def compare_storefront_and_competitor(
    tenant: dict,
    *,
    storefront_url: str,
    competitor_url: str,
    auto_approve: bool = True,
) -> dict:
    product_id = product_id_from_storefront_url(storefront_url)
    competitor = competitor_from_url(competitor_url)
    return await scrape_product_url(
        tenant,
        product_id=product_id,
        competitor=competitor,
        competitor_url=competitor_url,
        auto_approve=auto_approve,
        storefront_url=storefront_url,
    )


async def scrape_product_url(
    tenant: dict,
    *,
    product_id: str,
    competitor: str,
    competitor_url: str,
    auto_approve: bool = False,
    storefront_url: str | None = None,
) -> dict:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    await sync_full_catalog(tenant, product_id=product_id)
    product = await db.catalog_products.find_one({"tenant_id": tenant_key, "id": product_id})
    if not product:
        raise ValueError(
            f"Product {product_id} was not found in the catalog database. Check the storefront URL."
        )

    listing = await fetch_competitor_listing(competitor, competitor_url)
    return await attach_listing(
        tenant,
        product,
        listing,
        auto_approve=auto_approve,
        storefront_url=storefront_url,
        competitor=competitor,
    )


async def attach_listing(
    tenant: dict,
    product: dict,
    listing: CompetitorListing,
    *,
    auto_approve: bool = False,
    storefront_url: str | None = None,
    competitor: str | None = None,
) -> dict:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    payload = listing.model_dump()
    payload["tenant_id"] = tenant_key
    payload["category"] = product.get("category")
    payload["last_scraped_at"] = datetime.utcnow()
    await db.competitor_listings.update_one(
        {
            "tenant_id": tenant_key,
            "competitor": listing.competitor,
            "competitor_product_id": listing.competitor_product_id,
        },
        {"$set": payload},
        upsert=True,
    )
    saved = await db.competitor_listings.find_one(
        {
            "tenant_id": tenant_key,
            "competitor": listing.competitor,
            "competitor_product_id": listing.competitor_product_id,
        }
    )
    saved["id"] = str(saved["_id"])

    matching = tenant.get("matching") or {}
    decision = find_best_match(product, [saved], min_score=matching.get("min_score"))
    if decision is None:
        decision = {
            "product_id": product["id"],
            "competitor_listing_id": saved["id"],
            "tier": MatchTier.MANUAL.value,
            "confidence": 0,
            "status": MatchStatus.PENDING.value,
        }

    if auto_approve:
        decision["status"] = MatchStatus.APPROVED.value
        decision["reviewed_by"] = "compare_ui"
        decision["reviewed_at"] = datetime.utcnow()

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

    their_name = competitor_label(saved.get("competitor") or competitor)
    our_name = product.get("marketplace") or tenant.get("name") or "Your store"
    explained = explain_prices(
        product.get("price") or 0,
        saved.get("price") or 0,
        our_label=our_name,
        competitor_label=their_name,
    )

    comparison = {
        "our_product_id": product["id"],
        "our_title": product.get("title"),
        "our_price": product.get("price") or 0,
        "our_url": storefront_url or product.get("url"),
        "our_marketplace": product.get("marketplace"),
        "competitor": saved.get("competitor"),
        "competitor_title": saved.get("title"),
        "competitor_price": saved.get("price") or 0,
        "competitor_url": saved.get("url"),
        "difference_rs": explained["difference_rs"],
        "gap_pct": explained["gap_pct"],
        "cheaper": explained["cheaper"],
        "headline": explained["headline"],
        "detail": explained["detail"],
        "source": "scrape",
        "message": f"{explained['headline']} {explained['detail']}",
    }
    await db.comparisons_cache.delete_many(
        {
            "tenant_id": tenant_key,
            "product_id": product["id"],
            "source": "scrape",
            "competitor": saved.get("competitor"),
        }
    )
    await db.comparisons_cache.insert_one({**comparison, "tenant_id": tenant_key, "product_id": product["id"]})

    return {
        "message": comparison["message"],
        "headline": explained["headline"],
        "detail": explained["detail"],
        "cheaper": explained["cheaper"],
        "difference_rs": explained["difference_rs"],
        "our_product": {
            "id": product["id"],
            "title": product.get("title"),
            "price": product.get("price"),
            "marketplace": product.get("marketplace"),
            "url": storefront_url or product.get("url"),
        },
        "competitor_listing": {
            "id": saved["id"],
            "competitor": saved.get("competitor"),
            "title": saved.get("title"),
            "price": saved.get("price"),
            "url": saved.get("url"),
        },
        "match": {
            "confidence": decision.get("confidence"),
            "tier": decision.get("tier"),
            "status": decision.get("status"),
        },
        "comparison": comparison,
    }
