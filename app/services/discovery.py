from __future__ import annotations

"""
Pick one of your products → search the web → fetch product pages → compare.

Does not scrape marketplace search/catalog pages. It asks a search engine
for likely product URLs, then reads those detail pages the same way a
pasted URL works.
"""
import asyncio
import logging
import re
from urllib.parse import urlparse

from app.config import get_settings
from app.db import get_priceintel_db
from app.services.catalog_sync import sync_full_catalog
from app.services.comparison import _price_is_sane
from app.services.matching.fuzzy_text import fuzzy_text_match
from app.services.scrape import attach_listing, fetch_competitor_listings
from app.services.tenants import tenant_id as tid
from app.services.urls import competitor_from_url, product_id_from_storefront_url
from app.services.web_search import search_provider, search_web

logger = logging.getLogger(__name__)
settings = get_settings()

BLOCKED_HOST_PARTS = (
    "sadiq.ai",
    "facebook.",
    "instagram.",
    "youtube.",
    "youtu.be",
    "twitter.",
    "x.com",
    "tiktok.",
    "pinterest.",
    "reddit.",
    "linkedin.",
    "wikipedia.",
    "quora.",
    "google.",
    "bing.com",
    "duckduckgo.",
)
BLOCKED_PATH = re.compile(
    r"/(catalog|search|sr|s|category|categories|collection|collections|"
    r"shop|stores|login|cart|wishlist|tag|blog|news)(/|$)",
    re.I,
)
PRODUCT_PATH = re.compile(
    r"/(products?|item|itm|dp|gp/product|p)/",
    re.I,
)


async def discover_from_storefront(tenant: dict, storefront_url: str) -> dict:
    product_id = product_id_from_storefront_url(storefront_url)
    return await discover_product(tenant, product_id, storefront_url=storefront_url)


async def discover_product(
    tenant: dict,
    product_id: str,
    *,
    storefront_url: str | None = None,
    max_urls: int | None = None,
) -> dict:
    db = get_priceintel_db()
    key = tid(tenant)
    product = await db.catalog_products.find_one({"tenant_id": key, "id": product_id})
    if not product:
        await sync_full_catalog(tenant, product_id=product_id)
        product = await db.catalog_products.find_one({"tenant_id": key, "id": product_id})
    if not product:
        raise ValueError(f"Product {product_id} was not found in the catalog.")

    title = (product.get("title") or "").strip()
    if not title:
        raise ValueError("That catalog product has no title to search with.")

    candidates = await _search_candidates(title, max_urls=max_urls)
    skipped = []
    comparisons = []
    if not candidates:
        return _pack(product, comparisons, skipped, storefront_url, [])

    fetched = await fetch_competitor_listings(
        [(competitor_from_url(url), url) for url in candidates]
    )
    min_score = settings.DISCOVERY_MIN_SCORE
    our_price = product.get("price") or 0

    for url, listing, error in fetched:
        if error or listing is None:
            skipped.append({"url": url, "reason": error or "Could not read title/price"})
            continue
        candidate = listing.model_dump()
        candidate["category"] = product.get("category")
        score = fuzzy_text_match(product, candidate)
        if score < min_score:
            skipped.append(
                {
                    "url": url,
                    "title": listing.title,
                    "reason": (
                        f"Title match too weak ({score} < {min_score}): {listing.title[:80]}"
                    ),
                }
            )
            continue
        if our_price and not _price_is_sane(listing.price, our_price):
            skipped.append(
                {
                    "url": url,
                    "title": listing.title,
                    "reason": f"Price Rs. {listing.price:,.0f} looks like a different item/size",
                }
            )
            continue
        auto_approve = score >= (tenant.get("matching") or {}).get(
            "auto_approve_score", settings.MATCH_AUTO_APPROVE_SCORE
        )
        row = await attach_listing(
            tenant,
            product,
            listing,
            auto_approve=auto_approve or score >= min_score,
            storefront_url=storefront_url,
        )
        row["match_score"] = score
        comparisons.append(row)
        await asyncio.sleep(0)

    comparisons.sort(key=lambda row: (row.get("competitor_listing") or {}).get("price") or 9e9)
    return _pack(product, comparisons, skipped, storefront_url, candidates)


async def discover_unmapped(tenant: dict, limit: int = 3) -> dict:
    from app.services import automation

    unmapped = await automation.list_unmapped(tenant, limit=limit)
    results = []
    for item in unmapped.get("items") or []:
        try:
            results.append(await discover_product(tenant, item["id"], storefront_url=item.get("url")))
        except Exception as exc:
            results.append({"product_id": item.get("id"), "error": str(exc)})
    return {"count": len(results), "results": results}


FILLER_WORDS = {
    "premium",
    "new",
    "hot",
    "best",
    "sale",
    "original",
    "quality",
    "official",
    "latest",
    "durable",
}


async def _search_candidates(title: str, max_urls: int | None = None) -> list[str]:
    cap = max_urls or settings.DISCOVERY_MAX_URLS
    query = _short_title(title)
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        clean = (url or "").split("#")[0].strip()
        if not clean or clean in seen:
            return
        if not _is_product_url(clean):
            return
        seen.add(clean)
        found.append(clean)

    # One query per shop — long OR-chains confuse DuckDuckGo and return junk.
    for site in settings.discovery_sites[:3]:
        if len(found) >= cap:
            break
        for row in await asyncio.to_thread(search_web, f"{query} site:{site}", 5):
            add(row.get("url") or "")
    if settings.DISCOVERY_OPEN_WEB and len(found) < cap:
        for row in await asyncio.to_thread(search_web, f'"{query}" buy', 8):
            add(row.get("url") or "")
            if len(found) >= cap:
                break
    logger.info("Discovery search query=%r urls=%s", query, found)
    return found[:cap]


def _short_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s+-]", " ", title)
    words = [w for w in cleaned.split() if w and w.lower() not in FILLER_WORDS]
    return " ".join(words[:8])


def _is_product_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    host_cmp = host[4:] if host.startswith("www.") else host
    if any(part in host for part in BLOCKED_HOST_PARTS):
        return False
    if host_cmp.endswith("daraz.pk"):
        return "/products/" in path.lower()
    if BLOCKED_PATH.search(path):
        return False
    known = {s.lower() for s in settings.discovery_sites}
    if any(host_cmp.endswith(site) for site in known):
        return bool(PRODUCT_PATH.search(path))
    if settings.DISCOVERY_OPEN_WEB:
        return bool(PRODUCT_PATH.search(path))
    return False


def _pack(product, comparisons, skipped, storefront_url, searched) -> dict:
    cheapest = comparisons[0] if comparisons else None
    return {
        "provider": search_provider(),
        "product_id": product["id"],
        "our_product": {
            "id": product["id"],
            "title": product.get("title"),
            "price": product.get("price"),
            "marketplace": product.get("marketplace"),
            "url": storefront_url or product.get("url"),
        },
        "searched_urls": searched,
        "matches": comparisons,
        "skipped": skipped,
        "match_count": len(comparisons),
        "headline": (
            (cheapest or {}).get("headline")
            if cheapest
            else "No matching product pages were found. Try a more specific title, or paste a competitor URL."
        ),
        "cheaper": (cheapest or {}).get("cheaper"),
        "difference_rs": (cheapest or {}).get("difference_rs"),
        "detail": (
            f"Found {len(comparisons)} matching listing(s) across the web."
            if comparisons
            else "Search ran, but nothing cleared the title/price match bar."
        ),
    }
