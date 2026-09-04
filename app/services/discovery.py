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
from urllib.parse import urlparse, urlunparse

from app.config import get_settings
from app.db import get_priceintel_db
from app.services.catalog_sync import sync_full_catalog
from app.services.scrape import attach_listing, fetch_competitor_listings
from app.services.tenants import tenant_id as tid
from app.services.urls import competitor_from_url, competitor_label, product_id_from_storefront_url, slug_from_storefront_url
from app.services.web_search import search_provider, search_web, shopify_products
from rapidfuzz import fuzz

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
    # Always refresh this SKU so sale price / discount % match the storefront.
    await sync_full_catalog(tenant, product_id=product_id)
    product = await db.catalog_products.find_one({"tenant_id": key, "id": product_id})
    if not product:
        raise ValueError(f"Product {product_id} was not found in the catalog.")

    title = (product.get("title") or "").strip()
    if not title:
        raise ValueError("That catalog product has no title to search with.")

    candidates = await _search_candidates(
        title, max_urls=max_urls, storefront_url=storefront_url or product.get("url")
    )
    skipped = []
    comparisons = []
    if not candidates:
        skipped.append(
            {
                "url": "",
                "reason": (
                    "Search did not return product pages for "
                    + ", ".join(_search_queries(title, storefront_url or product.get("url")))
                ),
            }
        )
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
        score, miss = _match_score(title, listing.title, storefront_url or product.get("url"))
        if miss:
            skipped.append(
                {
                    "url": url,
                    "title": listing.title,
                    "reason": f"{miss}: {listing.title[:80]}",
                }
            )
            continue
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
        if our_price and not _discovery_price_ok(listing.price, our_price):
            skipped.append(
                {
                    "url": url,
                    "title": listing.title,
                    "reason": (
                        f"Price Rs. {listing.price:,.0f} is too far from yours "
                        f"(Rs. {our_price:,.0f}) — likely a different size or item"
                    ),
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

    comparisons = _one_per_shop(comparisons)[: settings.DISCOVERY_MAX_URLS]
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
    "with",
    "and",
    "for",
    "the",
    "a",
    "of",
    "to",
    "in",
    "on",
    "by",
    "lights",
    "light",
    "quiet",
    "auto",
    "off",
    "cool",
    "mist",
    "aromatherapy",
    "hu",
    "pk",
}

# Words that must not be enough on their own to call two products the same.
GENERIC_WORDS = FILLER_WORDS | {
    "organizer",
    "organiser",
    "box",
    "holder",
    "storage",
    "stand",
    "display",
    "set",
    "pack",
    "pieces",
    "piece",
    "candy",
    "earring",
    "earrings",
    "cosmetic",
    "cosmetics",
    "makeup",
    "home",
    "luxury",
    "imported",
    "high",
    "style",
}

# If our listing uses a group, the competitor title must use the same group.
TOKEN_GROUPS = (
    frozenset({"jewelry", "jewellery", "jewelery"}),
    frozenset({"perfume", "perfumes", "fragrance", "cologne", "cosmetic", "cosmetics", "makeup"}),
    frozenset({"train"}),
    frozenset({"diffuser", "humidifier"}),
)


async def _search_candidates(
    title: str,
    max_urls: int | None = None,
    storefront_url: str | None = None,
) -> list[str]:
    cap = max_urls or settings.DISCOVERY_MAX_URLS
    per_host = max(1, settings.DISCOVERY_PER_HOST)
    queries = _search_queries(title, storefront_url)
    found: list[str] = []
    seen_url: set[str] = set()
    seen_host: dict[str, int] = {}

    def add(url: str, snippet_title: str = "") -> None:
        clean = _canonical_url(url)
        if not clean or clean in seen_url:
            return
        if not _is_product_url(clean):
            return
        if snippet_title:
            score, miss = _match_score(title, snippet_title, storefront_url)
            if miss or score < 50:
                return
        host = _host(clean)
        if seen_host.get(host, 0) >= per_host:
            return
        seen_url.add(clean)
        seen_host[host] = seen_host.get(host, 0) + 1
        found.append(clean)

    logger.info("Discovery queries=%s", queries)
    # Open web first so Shopify "best fuzzy organizer" does not crowd out real matches.
    for query in queries[:2]:
        if len(found) >= cap:
            break
        for row in await asyncio.to_thread(search_web, f"{query} Pakistan", 12):
            add(row.get("url") or "", row.get("title") or "")
            if len(found) >= cap:
                break

    if len(found) < cap:
        site_rows = await asyncio.gather(
            *[
                asyncio.to_thread(search_web, f"{queries[0]} site:{site}", 6)
                for site in settings.discovery_sites
            ]
        )
        for rows in site_rows:
            if len(found) >= cap:
                break
            for row in rows:
                add(row.get("url") or "", row.get("title") or "")
                if len(found) >= cap:
                    break

    for site in settings.discovery_sites[:8]:
        if len(found) >= cap:
            break
        if any(_host(item).endswith(site) for item in found):
            continue
        ranked = []
        for row in shopify_products(site, queries[0], limit=5):
            score, miss = _match_score(title, row.get("title") or "", storefront_url)
            if miss:
                continue
            ranked.append((score, row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if ranked and ranked[0][0] >= settings.DISCOVERY_MIN_SCORE:
            add(ranked[0][1].get("url") or "", ranked[0][1].get("title") or "")

    logger.info("Discovery search urls=%s", found)
    return found[:cap]


def _slug_words(storefront_url: str | None) -> list[str]:
    slug = slug_from_storefront_url(storefront_url or "") or ""
    return [word for word in slug.replace("_", "-").split("-") if word and not word.isdigit()]


def _search_queries(title: str, storefront_url: str | None = None) -> list[str]:
    queries: list[str] = []
    slug_parts = [w for w in _slug_words(storefront_url) if w.lower() not in FILLER_WORDS]
    blob = f"{title} {storefront_url or ''}".lower()
    if "train" in blob and "diffuser" in blob:
        queries.append("mini train shape essential oil diffuser")
        queries.append("steam train essential oil diffuser")
    # Keep "and" so we search like a person: "jewelry and perfume organizer".
    cleaned = re.sub(r"[^\w\s+-]", " ", title or "")
    keep_and = [
        w
        for w in cleaned.split()
        if w.lower() not in (FILLER_WORDS - {"and"}) and w.lower() not in {"premium"}
    ]
    if keep_and:
        queries.append(" ".join(keep_and[:8]))
    if slug_parts:
        queries.append(" ".join(slug_parts[:8]))
    seen = set()
    unique = []
    for query in queries:
        key = query.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(query.strip())
    return unique or [_short_title(title)]


def _match_score(ours: str, theirs: str, storefront_url: str | None = None) -> tuple[float, str | None]:
    """Return (score, skip_reason). skip_reason set when it is clearly a different item."""
    miss = _missing_required(ours, theirs, storefront_url)
    if miss:
        return 0.0, miss
    return _title_score(ours, theirs), None


def _missing_required(ours: str, theirs: str, storefront_url: str | None) -> str | None:
    our_words = set(_normalize_title(f"{ours} {' '.join(_slug_words(storefront_url))}").split())
    their_words = set(_normalize_title(theirs).split())
    missing = []
    for group in TOKEN_GROUPS:
        if our_words & group and not (their_words & group):
            missing.append(sorted(our_words & group)[0])
    if missing:
        return "Different product (missing " + ", ".join(missing) + ")"
    our_distinct = _canonical_tokens(our_words - GENERIC_WORDS)
    their_distinct = _canonical_tokens(their_words - GENERIC_WORDS)
    if our_distinct and not (our_distinct & their_distinct):
        return "Different product (no distinctive words in common)"
    return None


def _canonical_tokens(words: set[str]) -> set[str]:
    """Treat jewellery/jewelry (and similar) as the same token."""
    mapped = set()
    for word in words:
        for group in TOKEN_GROUPS:
            if word in group:
                mapped.add(sorted(group)[0])
                break
        else:
            mapped.add(word)
    return mapped


def _discovery_price_ok(theirs: float, ours: float) -> bool:
    if theirs <= 0 or ours <= 0:
        return False
    ratio = theirs / ours
    return settings.DISCOVERY_PRICE_MIN_RATIO <= ratio <= settings.DISCOVERY_PRICE_MAX_RATIO


def _canonical_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _one_per_shop(comparisons: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for row in comparisons:
        listing = row.get("competitor_listing") or {}
        host = _host(listing.get("url") or "")
        price = listing.get("price") or 9e9
        current = best.get(host)
        if current is None or price < ((current.get("competitor_listing") or {}).get("price") or 9e9):
            best[host] = row
    return list(best.values())


def _distinctive_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s+-]", " ", title or "")
    words = []
    seen = set()
    for word in cleaned.split():
        key = word.lower()
        if key in FILLER_WORDS or key in seen:
            continue
        seen.add(key)
        words.append(word)
        if len(words) >= 6:
            break
    return " ".join(words)


def _short_title(title: str) -> str:
    return _distinctive_title(title)


def _title_score(ours: str, theirs: str) -> float:
    a = _normalize_title(ours)
    b = _normalize_title(theirs)
    if not a or not b:
        return 0.0
    return round(
        max(
            fuzz.token_set_ratio(a, b),
            fuzz.token_sort_ratio(a, b),
            fuzz.partial_ratio(a, b),
        ),
        1,
    )


def _normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]+", " ", title or "").lower()
    words = []
    seen = set()
    for word in cleaned.split():
        if word in FILLER_WORDS or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return " ".join(words)


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
    shops = len(comparisons)
    if cheapest:
        cheap_name = competitor_label((cheapest.get("competitor_listing") or {}).get("competitor") or "a shop")
        cheap_price = (cheapest.get("competitor_listing") or {}).get("price")
        headline = cheapest.get("headline") or ""
        detail = (
            f"Compared {shops} shops. Cheapest is {cheap_name} at Rs. {cheap_price:,.0f}. "
            f"Your price is Rs. {(product.get('price') or 0):,.0f}."
        )
    else:
        headline = "No matching product pages were found. Try a more specific title, or paste a competitor URL."
        detail = "Search ran, but nothing cleared the title/price match bar."
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
        "match_count": shops,
        "headline": headline,
        "cheaper": (cheapest or {}).get("cheaper"),
        "difference_rs": (cheapest or {}).get("difference_rs"),
        "detail": detail,
    }
