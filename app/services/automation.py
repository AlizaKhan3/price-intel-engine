from __future__ import annotations

"""
Automation for thousands of catalog products.

Your catalog is already loaded from Mongo. What does not scale is finding
the first competitor URL. After a product is mapped once, we refresh that
saved URL on a schedule — no more pasting.

Discovery of *new* Daraz search results is not done here: Daraz robots.txt
blocks /catalog/. Use a licensed search/affiliate API, or import a CSV of
known product-page URLs.
"""
import asyncio
import csv
import io
import logging

from app.config import get_settings
from app.db import get_priceintel_db
from app.models.product import MatchStatus
from app.services.scrape import scrape_product_url
from app.services.tenants import tenant_id as tid
from app.services.urls import competitor_from_url, product_id_from_storefront_url

logger = logging.getLogger(__name__)
settings = get_settings()

LEFT_HEADERS = {
    "product_id",
    "id",
    "sku",
    "storefront_url",
    "your_url",
    "url",
    "sadiq_url",
}
RIGHT_HEADERS = {"competitor_url", "daraz_url", "theirs", "competitor"}
MAX_PAIRS_PER_REQUEST = 100


async def coverage(tenant: dict) -> dict:
    db = get_priceintel_db()
    key = tid(tenant)
    total = await db.catalog_products.count_documents({"tenant_id": key, "active": True})
    mapped_ids = await db.product_matches.distinct(
        "product_id",
        {"tenant_id": key, "status": MatchStatus.APPROVED.value, "tier": {"$ne": "catalog_group"}},
    )
    mapped = len(mapped_ids)
    return {
        "active_products": total,
        "mapped_to_external_competitor": mapped,
        "unmapped": max(total - mapped, 0),
        "coverage_pct": round(mapped / total * 100, 1) if total else 0,
        "note": (
            "Mapped products can be price-refreshed automatically. "
            "Unmapped products still need a competitor product URL (bulk import or one-by-one)."
        ),
    }


async def list_unmapped(tenant: dict, limit: int = 50, skip: int = 0) -> dict:
    db = get_priceintel_db()
    key = tid(tenant)
    mapped_ids = await db.product_matches.distinct(
        "product_id",
        {"tenant_id": key, "status": MatchStatus.APPROVED.value, "tier": {"$ne": "catalog_group"}},
    )
    query = {"tenant_id": key, "active": True, "id": {"$nin": mapped_ids}}
    total = await db.catalog_products.count_documents(query)
    cursor = db.catalog_products.find(query).skip(skip).limit(limit)
    items = []
    async for product in cursor:
        items.append(
            {
                "id": product["id"],
                "title": product.get("title"),
                "price": product.get("price"),
                "marketplace": product.get("marketplace"),
                "url": product.get("url"),
            }
        )
    return {"items": items, "count": len(items), "total_unmapped": total, "skip": skip}


def unmapped_csv(items: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["product_id", "title", "price", "marketplace", "storefront_url", "competitor_url"]
    )
    for item in items:
        writer.writerow(
            [
                item.get("id") or "",
                item.get("title") or "",
                item.get("price") if item.get("price") is not None else "",
                item.get("marketplace") or "",
                item.get("url") or "",
                "",
            ]
        )
    return buf.getvalue()


def parse_mapping_lines(text: str) -> list[tuple[str, str]]:
    pairs = []
    header: list[str] | None = None
    for raw in (text or "").replace("\ufeff", "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cols = next(csv.reader([line]))
        cols = [c.strip() for c in cols]
        if header is None and cols and cols[0].lower() in LEFT_HEADERS:
            header = [c.lower() for c in cols]
            continue
        if header:
            row = {key: (cols[i] if i < len(cols) else "") for i, key in enumerate(header)}
            left = (
                row.get("product_id")
                or row.get("id")
                or row.get("storefront_url")
                or row.get("your_url")
                or row.get("url")
                or ""
            )
            right = (
                row.get("competitor_url")
                or row.get("daraz_url")
                or row.get("theirs")
                or row.get("competitor")
                or ""
            )
        else:
            if len(cols) < 2:
                raise ValueError(f"Bad line (need product_id,competitor_url): {line}")
            left, right = cols[0], cols[-1]
        if not right:
            continue
        if not left:
            raise ValueError(f"Missing product id on line: {line}")
        pairs.append((left, right))
    return pairs


async def import_mappings(tenant: dict, text: str) -> dict:
    pairs = parse_mapping_lines(text)
    if len(pairs) > MAX_PAIRS_PER_REQUEST:
        raise ValueError(
            f"Paste at most {MAX_PAIRS_PER_REQUEST} mapped rows per request "
            f"({len(pairs)} given). Split the CSV and import in batches."
        )
    ok = 0
    errors = []
    results = []
    for left, competitor_url in pairs:
        try:
            if left.startswith("http"):
                product_id = product_id_from_storefront_url(left)
            else:
                product_id = left
            row = await scrape_product_url(
                tenant,
                product_id=product_id,
                competitor=competitor_from_url(competitor_url),
                competitor_url=competitor_url,
                auto_approve=True,
                storefront_url=left if left.startswith("http") else None,
            )
            ok += 1
            results.append({"product_id": product_id, "ok": True, "message": row.get("headline")})
        except Exception as exc:
            errors.append({"input": left, "error": str(exc)})
        await asyncio.sleep(settings.SCRAPER_REQUEST_DELAY_SECONDS)
    return {"imported": ok, "failed": len(errors), "results": results, "errors": errors}


async def refresh_mapped_prices(tenant: dict, limit: int = 50) -> dict:
    """Re-read saved competitor product pages and update prices."""
    db = get_priceintel_db()
    key = tid(tenant)
    matches = await db.product_matches.find(
        {
            "tenant_id": key,
            "status": MatchStatus.APPROVED.value,
            "tier": {"$ne": "catalog_group"},
        }
    ).limit(limit).to_list(length=limit)

    refreshed = 0
    errors = []
    headlines = []
    for match in matches:
        listing = None
        listing_id = match.get("competitor_listing_id")
        if listing_id:
            from bson import ObjectId

            if ObjectId.is_valid(str(listing_id)):
                listing = await db.competitor_listings.find_one(
                    {"tenant_id": key, "_id": ObjectId(str(listing_id))}
                )
        if not listing or not listing.get("url"):
            continue
        try:
            row = await scrape_product_url(
                tenant,
                product_id=match["product_id"],
                competitor=listing.get("competitor") or competitor_from_url(listing["url"]),
                competitor_url=listing["url"],
                auto_approve=True,
            )
            refreshed += 1
            headlines.append(row.get("headline"))
        except Exception as exc:
            errors.append({"product_id": match.get("product_id"), "error": str(exc)})
        await asyncio.sleep(settings.SCRAPER_REQUEST_DELAY_SECONDS)

    return {"refreshed": refreshed, "failed": len(errors), "errors": errors, "headlines": headlines}
