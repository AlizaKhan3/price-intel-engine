from __future__ import annotations

"""Scrape one competitor product page and attach it to one of your products."""
import logging
from datetime import datetime

from app.db import get_priceintel_db
from app.models.product import MatchStatus, MatchTier
from app.scrapers.registry import get_scraper
from app.services.matching.pipeline import find_best_match
from app.services.tenants import tenant_id as tid

logger = logging.getLogger(__name__)


async def scrape_product_url(
    tenant: dict,
    *,
    product_id: str,
    competitor: str,
    competitor_url: str,
    auto_approve: bool = False,
) -> dict:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    product = await db.catalog_products.find_one({"tenant_id": tenant_key, "id": product_id})
    if not product:
        raise ValueError(f"Product {product_id} is not in the synced catalog. Run catalog sync first.")

    scraper = get_scraper(competitor)
    if not hasattr(scraper, "fetch_product"):
        raise ValueError(f"Scraper {competitor} cannot fetch a product URL yet.")

    from playwright.async_api import async_playwright
    from app.config import get_settings

    settings = get_settings()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=settings.SCRAPER_USER_AGENT)
        try:
            listing = await scraper.fetch_product(page, competitor_url)
        finally:
            await browser.close()

    if listing is None:
        raise RuntimeError(
            "Could not read title/price from that page. Daraz may have blocked the browser or changed markup."
        )

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
        decision["reviewed_by"] = "scrape_api"
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

    comparison = None
    if decision["status"] == MatchStatus.APPROVED.value:
        our_price = product.get("price") or 0
        their_price = saved.get("price") or 0
        gap_pct = round((our_price - their_price) / our_price * 100, 2) if our_price else 0
        comparison = {
            "our_product_id": product["id"],
            "our_title": product.get("title"),
            "our_price": our_price,
            "our_marketplace": product.get("marketplace"),
            "competitor": saved.get("competitor"),
            "competitor_title": saved.get("title"),
            "competitor_price": their_price,
            "competitor_url": saved.get("url"),
            "gap_pct": gap_pct,
            "source": "scrape",
            "note": "Positive gap_pct means YOUR listing is more expensive than Daraz.",
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
        "our_product": {
            "id": product["id"],
            "title": product.get("title"),
            "price": product.get("price"),
            "marketplace": product.get("marketplace"),
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
