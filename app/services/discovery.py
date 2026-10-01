from __future__ import annotations

"""
Paste any product URL → search the web → fetch product pages → compare.

Catalog storefront URLs sync title/price from Mongo. Any other product page
is scraped first. Search never uses marketplace catalog pages (e.g. Daraz
/catalog/); only product detail URLs are opened.
"""
import asyncio
import logging
import re
import time
from urllib.parse import urlparse, urlunparse

from app.config import get_settings
from app.db import get_priceintel_db
from app.services.catalog_sync import sync_full_catalog
from app.services.scrape import attach_listing, fetch_competitor_listings, scrape_url_as_our_product
from app.services.tenants import tenant_id as tid
from app.services.urls import (
    competitor_from_url,
    competitor_label,
    is_catalog_storefront_url,
    product_id_from_storefront_url,
    slug_from_any_url,
    slug_from_storefront_url,
)
from app.services.web_search import search_provider, search_web, shopify_products
from app.services.markets import Market, get_market
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)
settings = get_settings()

# Price guides and spec sites. They are not shops, and fetching them burns the time budget.
AGGREGATOR_HOSTS = (
    "qeemat.com.pk",
    "dablew.pk",
    "whatmobile.com.pk",
    "gsmarena.com",
    "mobileinto.com",
    "phonearena.com",
)
BLOCKED_HOST_PARTS = (
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
    r"/(catalog|search|sr(?=/|$)|categories?|collections?|"
    r"stores|login|cart|wishlist|tags?|blog|news)(/|$)",
    re.I,
)
# /shop/ alone is a catalog; /shop/some-product-slug is a product page.
SHOP_INDEX = re.compile(r"/shops?/?$", re.I)
PRODUCT_PATH = re.compile(
    r"/(products?|item|itm|dp|gp/product|p|shop)/[^/]+",
    re.I,
)
# PriceOye / category PDPs: /smart-watches/samsung/slug-with-hyphens
CATEGORY_PDP = re.compile(
    r"^/(?:smart-watches|mobiles|laptops|tablets|appliances|fashion|health|"
    r"beauty|groceries|tvs|cameras|audio|gaming|wearables)/[^/]+/[^/]+",
    re.I,
)
MEGA_BRANDS = frozenset(
    {
        "samsung",
        "apple",
        "sony",
        "huawei",
        "xiaomi",
        "oppo",
        "vivo",
        "oneplus",
        "google",
        "lg",
        "dell",
        "hp",
        "lenovo",
        "asus",
        "nike",
        "adidas",
    }
)
# Watch case sizes / common pack counts — not product model identity.
SIZE_NUMBERS = frozenset(
    {"16", "32", "40", "41", "42", "44", "45", "46", "49", "64", "128", "256", "512"}
)
MARKETING_TAIL = frozenset(
    {
        "premium",
        "smartwatch",
        "fitness",
        "tracking",
        "amoled",
        "display",
        "advanced",
        "health",
        "monitoring",
        "bluetooth",
        "wifi",
        "waterproof",
        "wireless",
        "original",
        "official",
        "bundle",
        "combo",
    }
)


async def discover_from_storefront(
    tenant: dict,
    storefront_url: str,
    *,
    market: Market | None = None,
    market_code: str | None = None,
    product_name: str | None = None,
) -> dict:
    """
    Paste any product URL → find the same item elsewhere → compare prices.

    Catalog storefront URLs (e.g. Sadiq) still sync from Mongo.
    Any other product page is scraped for title/price first.
    """
    url = (storefront_url or "").strip()
    resolved = market or get_market(market_code)
    if is_catalog_storefront_url(url):
        try:
            product_id = product_id_from_storefront_url(url)
            return await discover_product(
                tenant, product_id, storefront_url=url, market=resolved
            )
        except ValueError:
            pass
        except Exception as exc:
            logger.warning("Catalog lookup failed for %s (%s); scraping the page", url, exc)
    return await discover_from_external_url(tenant, url, market=resolved, product_name=product_name)


async def discover_from_external_url(
    tenant: dict,
    product_url: str,
    *,
    max_urls: int | None = None,
    market: Market | None = None,
    product_name: str | None = None,
) -> dict:
    product = await scrape_url_as_our_product(
        tenant, product_url, market=market, product_name=product_name
    )
    return await _discover_with_product(
        tenant,
        product,
        storefront_url=product_url,
        max_urls=max_urls,
        exclude_host=_host(product_url),
        market=market,
    )


async def discover_product(
    tenant: dict,
    product_id: str,
    *,
    storefront_url: str | None = None,
    max_urls: int | None = None,
    market: Market | None = None,
) -> dict:
    db = get_priceintel_db()
    key = tid(tenant)
    # Always refresh this SKU so sale price / discount % match the storefront.
    await sync_full_catalog(tenant, product_id=product_id)
    product = await db.catalog_products.find_one({"tenant_id": key, "id": product_id})
    if not product:
        raise ValueError(f"Product {product_id} was not found in the catalog.")
    if market:
        product = {**product, "market_code": market.code, "currency": product.get("currency") or market.currency}
    return await _discover_with_product(
        tenant,
        product,
        storefront_url=storefront_url or product.get("url"),
        max_urls=max_urls,
        exclude_host=_host(storefront_url or product.get("url") or ""),
        market=market,
    )


async def _discover_with_product(
    tenant: dict,
    product: dict,
    *,
    storefront_url: str | None = None,
    max_urls: int | None = None,
    exclude_host: str | None = None,
    market: Market | None = None,
) -> dict:
    title = (product.get("title") or "").strip()
    if not title:
        raise ValueError("That product has no title to search with.")
    from app.scrapers.parse import is_generic_site_title

    if is_generic_site_title(title):
        raise ValueError(
            "We couldn't read a product name from that page. "
            "We didn't guess a price or search for the site name. "
            "Paste a product link that includes the product name."
        )

    market = market or get_market(product.get("market_code"))
    started = time.monotonic()
    candidates = await _search_candidates(
        title,
        max_urls=max_urls,
        storefront_url=storefront_url or product.get("url"),
        exclude_host=exclude_host,
        market=market,
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
        return _pack(product, comparisons, skipped, storefront_url, [], market=market)

    remaining = settings.COMPARE_BUDGET_SECONDS - (time.monotonic() - started)
    fetched = await fetch_competitor_listings(
        [(competitor_from_url(url), url) for url in candidates],
        budget_seconds=max(8.0, remaining),
    )
    min_score = settings.DISCOVERY_MIN_SCORE
    our_price = product.get("price") or 0
    our_price_unknown = bool(product.get("price_unknown")) or our_price <= 0
    our_host = exclude_host or _host(storefront_url or product.get("url") or "")
    market_currency = market.currency if market else (product.get("currency") or "USD")

    for url, listing, error in fetched:
        if listing is None or listing.price_unknown or not listing.price:
            skipped.append(
                {
                    "url": url,
                    "reason": error or "Could not read a real price on that page",
                }
            )
            continue
        if our_host and _host(url) == our_host:
            skipped.append({"url": url, "reason": "Same shop as your pasted link"})
            continue
        # Align competitor price into the compare market currency.
        from app.services.markets import convert_amount

        listing_cur = (listing.currency or market_currency).upper()
        if listing_cur != market_currency and listing.price:
            listing.price = convert_amount(listing.price, listing_cur, market_currency)
        listing.currency = market_currency
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
        # Keep title-matched shops even when price is far apart (flash-sale vs
        # full price, or outlier listings). Flag them instead of dropping.
        price_outlier = bool(
            not our_price_unknown
            and our_price
            and listing.price
            and not _discovery_price_ok(listing.price, our_price)
        )
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
        row["price_outlier"] = price_outlier
        if price_outlier and row.get("headline"):
            row["headline"] = f"{row['headline']} (price gap is large — check size/variant)"
        comparisons.append(row)
        await asyncio.sleep(0)

    comparisons = _one_per_shop(comparisons)[: settings.DISCOVERY_MAX_URLS]
    from app.scrapers.parse import comparison_rank

    comparisons.sort(
        key=lambda row: comparison_rank(
            (row.get("competitor_listing") or {}).get("in_stock", True),
            (row.get("competitor_listing") or {}).get("price"),
        )
    )
    return _pack(product, comparisons, skipped, storefront_url, candidates, market=market)


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
    "home",
    "luxury",
    "imported",
    "high",
    "style",
    # Category words shared by many unrelated SKUs (face wash ≠ gluta white wash).
    "face",
    "wash",
    "whitening",
    "white",
    "brightening",
    "bright",
    "cream",
    "serum",
    "lotion",
    "soap",
    "gel",
    "shampoo",
    "conditioner",
    "oil",
    "mist",
    "spray",
    "ml",
    "g",
    "gm",
    "kg",
    "oz",
    "size",
    "volume",
    "bottle",
    "tube",
    "skin",
    "care",
    "skincare",
    "vintage",
}

# Materials / adjectives — helpful context, not identity.
MATERIAL_WORDS = {
    "acrylic",
    "metal",
    "plastic",
    "clear",
    "wood",
    "leather",
    "glass",
    "steel",
    "silicone",
}

# If our listing uses a group, the competitor title must use the same group.
TOKEN_GROUPS = (
    frozenset({"jewelry", "jewellery", "jewelery"}),
    frozenset({"perfume", "perfumes", "fragrance", "cologne", "cosmetic", "cosmetics", "makeup"}),
    frozenset({"train"}),
    frozenset({"diffuser", "humidifier"}),
    frozenset({"vitamin", "vitamins"}),
)

# Stable label per synonym group (avoid sorted() picking "cologne" for perfume).
TOKEN_CANON = {
    "jewelry": "jewelry",
    "jewellery": "jewelry",
    "jewelery": "jewelry",
    "perfume": "perfume",
    "perfumes": "perfume",
    "fragrance": "perfume",
    "cologne": "perfume",
    "cosmetic": "perfume",
    "cosmetics": "perfume",
    "makeup": "perfume",
    "train": "train",
    "diffuser": "diffuser",
    "humidifier": "diffuser",
    "vitamin": "vitamin",
    "vitamins": "vitamin",
    "toothbrush": "toothbrush",
    "toothbrushes": "toothbrush",
    "seasoning": "spice",
    "spice": "spice",
    "spices": "spice",
}


async def _search_candidates(
    title: str,
    max_urls: int | None = None,
    storefront_url: str | None = None,
    exclude_host: str | None = None,
    market: Market | None = None,
) -> list[str]:
    cap = max_urls or settings.DISCOVERY_MAX_URLS
    per_host = max(1, settings.DISCOVERY_PER_HOST)
    queries = _search_queries(title, storefront_url)
    deadline = time.monotonic() + float(settings.DISCOVERY_CANDIDATE_BUDGET_SECONDS)
    found: list[str] = []
    seen_url: set[str] = set()
    seen_host: dict[str, int] = {}
    source_host = (exclude_host or _host(storefront_url or "")).lower()
    sites = list(market.discovery_sites) if market and market.discovery_sites else settings.discovery_sites
    locale = (market.query_locale if market else "Pakistan") or ""

    def add(url: str, snippet_title: str = "") -> None:
        clean = _canonical_url(url)
        if not clean or clean in seen_url:
            return
        if not _is_product_url(clean, market=market):
            return
        host = _host(clean)
        if source_host and (host == source_host or host.endswith("." + source_host) or source_host.endswith("." + host)):
            return
        if snippet_title:
            score, miss = _match_score(title, snippet_title, storefront_url)
            # A cover or a different model (A16 vs A17) must not sneak in via the URL slug.
            if miss and ("accessory" in miss or "model mismatch" in miss):
                return
            if miss or score < max(50, settings.DISCOVERY_MIN_SCORE - 18):
                if not _url_slug_matches(title, clean, storefront_url):
                    return
        elif not _url_slug_matches(title, clean, storefront_url):
            return
        if seen_host.get(host, 0) >= per_host:
            return
        seen_url.add(clean)
        seen_host[host] = seen_host.get(host, 0) + 1
        found.append(clean)

    logger.info("Discovery queries=%s exclude_host=%s market=%s", queries, source_host or None, market.code if market else None)
    for query in queries[:2]:
        if len(found) >= cap or time.monotonic() >= deadline:
            break
        q_buy = f"{query} {locale} buy".strip() if locale else f"{query} buy"
        q_plain = f"{query} {locale}".strip() if locale else query
        for row in await asyncio.to_thread(search_web, q_buy, 12, market):
            add(row.get("url") or "", row.get("title") or "")
            if len(found) >= cap:
                break
        if len(found) >= cap or time.monotonic() >= deadline:
            break
        for row in await asyncio.to_thread(search_web, q_plain, 12, market):
            add(row.get("url") or "", row.get("title") or "")
            if len(found) >= cap:
                break

    if len(found) < cap and queries and time.monotonic() < deadline:
        site_tasks = [
            asyncio.wait_for(
                asyncio.to_thread(search_web, f"{queries[0]} site:{site}", 6, market),
                timeout=8,
            )
            for site in sites[:6]
        ]
        try:
            site_rows = await asyncio.wait_for(
                asyncio.gather(*site_tasks, return_exceptions=True),
                timeout=9,
            )
        except asyncio.TimeoutError:
            site_rows = []
        for rows in site_rows:
            if isinstance(rows, Exception):
                continue
            if len(found) >= cap:
                break
            for row in rows:
                add(row.get("url") or "", row.get("title") or "")
                if len(found) >= cap:
                    break

    if queries and time.monotonic() < deadline:
        shop_rows = await _shopify_many(
            [site for site in sites[:12] if not (source_host and site in source_host)],
            queries[0],
            limit=5,
        )
        for site, rows in shop_rows:
            if len(found) >= cap:
                break
            if any(_host(item).endswith(site) for item in found):
                continue
            ranked = []
            for row in rows:
                score, miss = _match_score(title, row.get("title") or "", storefront_url)
                if miss:
                    continue
                ranked.append((score, row))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            floor = settings.DISCOVERY_MIN_SCORE if found else max(50, settings.DISCOVERY_MIN_SCORE - 15)
            if ranked and ranked[0][0] >= floor:
                add(ranked[0][1].get("url") or "", ranked[0][1].get("title") or "")

    logger.info("Discovery search urls=%s", found)
    return found[:cap]


async def _shopify_many(sites: list[str], query: str, limit: int = 5) -> list[tuple[str, list[dict]]]:
    """Shopify suggest lookups run in threads so they don't block the event loop."""

    async def one(site: str) -> tuple[str, list[dict]]:
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(shopify_products, site, query, limit),
                timeout=4,
            )
        except Exception:
            rows = []
        return site, rows or []

    if not sites:
        return []
    return list(await asyncio.gather(*(one(site) for site in sites)))


def _slug_words(storefront_url: str | None) -> list[str]:
    slug = slug_from_any_url(storefront_url or "") or slug_from_storefront_url(storefront_url or "") or ""
    return [word for word in slug.replace("_", "-").split("-") if word and not word.isdigit()]


def _url_slug_matches(title: str, url: str, storefront_url: str | None = None) -> bool:
    raw_path = urlparse(url).path or ""
    # Daraz ids (-i465976536.html) are not part of the model code.
    raw_path = re.sub(r"-i\d+.*$", "", raw_path, flags=re.I)
    raw_path = re.sub(r"\.(html?|php)$", "", raw_path, flags=re.I)
    path = raw_path.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    path = re.sub(r"([a-z])(\d)", r"\1 \2", path)
    path = re.sub(r"(\d)([a-z])", r"\1 \2", path)
    path_words = {w for w in path.split() if w}
    brand = _brand_tokens(title, storefront_url)
    if brand:
        hit = len(_canonical_tokens(brand) & path_words)
        need = 1 if brand & MEGA_BRANDS else min(2, len(brand))
        rest = brand - MEGA_BRANDS
        if hit < need and not (rest and rest <= path_words):
            return False
    if _is_accessory_title(path) and not _is_accessory_title(title):
        return False
    if _strict_model_conflict(title, path):
        return False
    models = _model_numbers(title)
    if models and not (models & _model_numbers(path)):
        return False
    our_ed = _edition_tokens(title)
    path_ed = _edition_tokens(path)
    if models and our_ed != path_ed and (our_ed or path_ed):
        return False
    return True


def _match_score(ours: str, theirs: str, storefront_url: str | None = None) -> tuple[float, str | None]:
    """Return (score, skip_reason). skip_reason set when it is clearly a different item."""
    miss = _missing_required(ours, theirs, storefront_url)
    if miss:
        return 0.0, miss
    return _title_score(ours, theirs), None


def _edition_tokens(text: str) -> set[str]:
    """Product editions (Watch 5 Pro), not shop names like 'Fone Pro'."""
    edition = {"pro", "plus", "max", "ultra", "fe"}
    raw = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text or "")
    raw = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", raw)
    words = _normalize_title(raw).split()
    found: set[str] = set()
    for i, word in enumerate(words):
        if word not in edition:
            continue
        window = words[max(0, i - 3) : i + 1]
        near_model = any(any(ch.isdigit() for ch in token) for token in window)
        near_device = i > 0 and words[i - 1] in {
            "watch",
            "phone",
            "tab",
            "bud",
            "buds",
            "book",
            "pad",
            "galaxy",
        }
        if near_model or near_device:
            found.add(word)
    return found


def _missing_required(ours: str, theirs: str, storefront_url: str | None) -> str | None:
    if _is_accessory_title(theirs) and not _is_accessory_title(ours):
        return "Different product (accessory, not the main item)"
    if _strict_model_conflict(ours, theirs):
        needed = _strict_model_codes(ours) or _model_numbers(ours)
        return "Different product (model mismatch: need " + "/".join(sorted(needed)) + ")"

    our_words = set(_normalize_title(f"{ours} {' '.join(_slug_words(storefront_url))}").split())
    their_words = set(_normalize_title(theirs).split())
    brand = _brand_tokens(ours, storefront_url)
    their_exp = _canonical_tokens(their_words) | their_words
    # Also expand glued tokens (watch5 → watch, 5) for matching.
    their_exp |= _model_numbers(theirs)
    their_exp |= set(re.sub(r"([a-z])(\d)", r"\1 \2", " ".join(their_words)).split())
    brand_ok = _brand_ok(brand, their_exp)

    our_strict, _our_loose = _split_models(ours)
    their_strict, _their_loose = _split_models(theirs)
    our_models = _model_numbers(ours)
    their_models = _model_numbers(theirs)
    # RAM/storage digits must not veto a shared model code (A16 6GB vs A16).
    if our_strict and their_strict and (our_strict & their_strict):
        pass
    elif our_models and not (our_models & their_models):
        return (
            "Different product (model mismatch: need "
            + "/".join(sorted(our_models))
            + ")"
        )

    our_ed = _edition_tokens(ours)
    their_ed = _edition_tokens(theirs)
    # Only enforce editions when a model number is in play (Watch 5 ≠ Watch 5 Pro).
    if our_models and our_ed != their_ed and (our_ed or their_ed):
        return "Different product (edition mismatch: " + " ".join(sorted(our_ed | their_ed)) + ")"

    missing = []
    for group in TOKEN_GROUPS:
        if our_words & group and not (their_words & group):
            # Same brand line (Daily Wish Face Wash) can omit "Vitamin C" in the title.
            if brand_ok and group & {"vitamin", "vitamins"}:
                continue
            missing.append(sorted(our_words & group)[0])
    if missing:
        return "Different product (missing " + ", ".join(missing) + ")"

    our_distinct = _canonical_tokens(our_words - GENERIC_WORDS - MATERIAL_WORDS)
    their_distinct = _canonical_tokens(their_words - GENERIC_WORDS - MATERIAL_WORDS)
    if our_distinct and not (our_distinct & their_distinct):
        return "Different product (no distinctive words in common)"

    # Brand / line name: "Daily Wish" must appear — "Gluta White Face Wash" must not pass.
    if brand and not brand_ok:
        return "Different product (brand mismatch: " + " ".join(sorted(brand)) + ")"

    if our_distinct:
        overlap = len(our_distinct & their_distinct)
        need = 1 if len(our_distinct) <= 2 else max(2, (len(our_distinct) + 2) // 3)
        # Strong brand match: one shared key word is enough (Daily Wish Face Wash).
        if brand and brand_ok:
            need = min(need, 1)
        if overlap < need:
            return f"Different product (only {overlap}/{need} key words match)"
    return None


def _brand_ok(brand: set[str], their_exp: set[str]) -> bool:
    if not brand:
        return True
    canon_brand = _canonical_tokens(brand)
    if canon_brand.issubset(their_exp):
        return True
    # Mega-brand + line: "Galaxy Watch 5" without saying Samsung is still OK.
    mega = brand & MEGA_BRANDS
    rest = _canonical_tokens(brand - MEGA_BRANDS)
    if mega and rest and rest.issubset(their_exp):
        return True
    if mega and mega.issubset(their_exp) and not rest:
        return True
    return False


def _canonical_tokens(words: set[str]) -> set[str]:
    """Collapse synonyms (jewellery/jewelry, cosmetic/perfume) to one token."""
    mapped = set()
    for word in words:
        mapped.add(TOKEN_CANON.get(word, word))
    return mapped


def _brand_tokens(ours: str, storefront_url: str | None) -> set[str]:
    """First distinctive title tokens — usually the brand/line name (e.g. daily wish)."""
    # Not brands: product adjectives / category words that generic listings lead with
    # ("5/6 Modes Electric Toothbrushes…" must not invent brand {modes, electric}).
    weak_prefix = {
        "digital",
        "fast",
        "mini",
        "portable",
        "electric",
        "automatic",
        "wireless",
        "usb",
        "led",
        "smart",
        "pro",
        "max",
        "super",
        "ultra",
        "acrylic",
        "metal",
        "plastic",
        "clear",
        "modes",
        "mode",
        "rechargeable",
        "whitening",
        "waterproof",
        "silicone",
        "reusable",
        "natural",
        "naturally",
        "conditioning",
        "household",
        "rotating",
        "premium",
        "adults",
        "kids",
        "women",
        "womens",
        "men",
        "mens",
        "unisex",
        "small",
        "large",
        "holder",
        "holders",
        "brush",
        "toothbrush",
        "toothbrushes",
        "massager",
        "massage",
        "roller",
        "cube",
        "ice",
        "face",
        "eyes",
        "neck",
        "skin",
        "care",
        "mold",
        "gas",
        "body",
        "spray",
        "water",
        "bottle",
        "tritan",
        "glass",
        "jars",
        "jar",
        "seasoning",
        "storage",
        "rack",
        "timer",
        "ipx",
        "ipx7",
        "tooth",
    }
    words = [
        w
        for w in _normalize_title(f"{ours} {' '.join(_slug_words(storefront_url))}").split()
        if w not in GENERIC_WORDS
        and w not in weak_prefix
        and not w.isdigit()
        and (len(w) > 1 or w == "c")
    ]
    if not words:
        return set()
    return set(words[:2])


# Letter-led model codes (A16, S24, P20i) and digit-led ones (710BT).
_LETTER_CODE = re.compile(r"\b([a-z]{1,3})(\d{1,4})([a-z]{0,3})\b")
_DIGIT_FEATURE = re.compile(r"\b(\d{2,4})(bt|nc|anc)\b")
_UNIT_SUFFIX = frozenset({"w", "v", "ml", "mah", "mm", "cm", "kg", "gb", "tb", "hz", "oz", "g", "l"})
_SKIP_PREFIX = frozenset(
    {
        "rs",
        "pkr",
        "usd",
        "mm",
        "cm",
        "gb",
        "tb",
        "ml",
        "kg",
        "oz",
        "pk",
        "ipx",
        "usb",
        "led",
        "mah",
        "ram",
        "rom",
        "sim",
        "for",
        "and",
        "the",
        "pro",
        "max",
        "new",
    }
)
_ACCESSORY_TITLE = re.compile(
    r"\b("
    r"covers?|cases?|casings?|pouches?|sleeves?|bumpers?|"
    r"protectors?|screen\s+guards?|tempered\s+glass|"
    r"(?:charger|cable|case|cover|protector|glass)\s+for|"
    r"data\s+cables?|charging\s+cables?|"
    r"back\s+covers?|flip\s+covers?|phone\s+covers?|mobile\s+covers?"
    r")\b",
    re.I,
)


def _compact_model_text(text: str) -> str:
    """Glue spaced model codes (A 16, 710 BT) without joining words like Watch 5."""
    low = (text or "").lower().replace("_", " ")
    low = re.sub(r"[\-–—/]+", " ", low)
    # "a 16" / "p 20 i" → a16 / p20i. Do not swallow the next word ("a 16 pro").
    low = re.sub(
        r"\b([a-z])\s+(\d{1,4})(?:\s+([a-z]))?(?=\s|$)",
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3) or ''}",
        low,
    )
    low = re.sub(r"\b(\d{2,4})\s+(bt|nc|anc)\b", r"\1\2", low)
    return low


def _is_accessory_title(title: str) -> bool:
    """True when the listing is a cover, case, protector, or a charger/cable itself."""
    text = title or ""
    if _ACCESSORY_TITLE.search(text):
        return True
    if re.search(r"\b(chargers?|cables?)\b", text, re.I):
        if re.search(r"\b(with|includes|including|plus)\s+(a\s+)?(chargers?|cables?)\b", text, re.I):
            return False
        return True
    return False


def _split_models(text: str) -> tuple[set[str], set[str]]:
    """Strict alphanumeric codes, plus leftover model digits (Watch 5)."""
    low = _compact_model_text(text)
    strict: set[str] = set()
    spans: list[tuple[int, int]] = []
    for match in _DIGIT_FEATURE.finditer(low):
        digits, suffix = match.group(1), match.group(2)
        strict.add(f"{digits}{suffix}")
        strict.add(digits)
        spans.append(match.span())
    for match in _LETTER_CODE.finditer(low):
        if any(start <= match.start() < end for start, end in spans):
            continue
        prefix, digits, suffix = match.group(1), match.group(2), match.group(3)
        if prefix in _SKIP_PREFIX or suffix in _UNIT_SUFFIX:
            continue
        strict.add(f"{prefix}{digits}{suffix}")
        spans.append(match.span())

    chars = list(low)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    bare = "".join(chars)
    bare = re.sub(r"([a-z])(\d)", r"\1 \2", bare)
    bare = re.sub(r"(\d)([a-z])", r"\1 \2", bare)
    loose: set[str] = set()
    for token in re.findall(r"\b\d{1,4}\b", bare):
        if token in SIZE_NUMBERS:
            continue
        if len(token) == 4 and token.startswith(("19", "20")):
            continue
        if re.search(rf"\b{re.escape(token)}\s*(ml|g|kg|mm|cm|oz|mah)\b", bare):
            continue
        if re.search(rf"\bipx\s*-?\s*{re.escape(token)}\b", bare) or f"ipx{token}" in bare.replace(" ", ""):
            continue
        if re.search(
            rf"\b{re.escape(token)}\s*(?:/\s*\d+\s*)?[- ]?modes?\b",
            bare,
        ) or re.search(rf"\bmodes?\s*{re.escape(token)}\b", bare):
            continue
        loose.add(token)
    return strict, loose


def _model_numbers(text: str) -> set[str]:
    """Model identity: A16 / S24 / P20i / 710BT, plus plain numbers like Watch 5."""
    strict, loose = _split_models(text)
    return strict | loose


def _strict_model_codes(text: str) -> set[str]:
    strict, _loose = _split_models(text)
    return strict


def _strict_stems(codes: set[str]) -> set[str]:
    stems: set[str] = set()
    for code in codes:
        match = re.fullmatch(r"(\d{2,4})(bt|nc|anc)", code)
        stems.add(match.group(1) if match else code)
    return stems


def _strict_model_conflict(ours: str, theirs: str) -> bool:
    """Alphanumeric codes must agree. A16 is not A17, and 710BT is not 510BT."""
    our_strict = _strict_model_codes(ours)
    their_strict = _strict_model_codes(theirs)
    if our_strict and their_strict:
        return not (our_strict & their_strict)
    if our_strict:
        return not (_strict_stems(our_strict) & _model_numbers(theirs))
    if their_strict:
        return not (_strict_stems(their_strict) & _model_numbers(ours))
    return False


def _product_type_queries(title: str) -> list[str]:
    """Human search phrases when the listing title is marketing fluff."""
    low = (title or "").lower()
    out: list[str] = []
    brand = _brand_tokens(title, None)
    brand_s = " ".join(sorted(brand)) if brand else ""
    phrases = [
        (("toothbrush", "toothbrushes"), "electric toothbrush"),
        (("body spray",), "body spray"),
        (("ice", "roller"), "silicone ice roller face"),
        (("seasoning", "spice", "jars"), "360 rotating spice rack glass jars"),
        (("water bottle", "tritan"), "tritan water bottle"),
    ]
    for keys, phrase in phrases:
        if any(k in low for k in keys):
            out.append(f"{brand_s} {phrase}".strip() if brand_s else phrase)
    return out


def _core_product_query(title: str) -> str:
    """Brand + model only — drop marketing tails like 'Premium Smartwatch with…'."""
    type_q = _product_type_queries(title)
    cleaned = re.sub(r"[^\w\s+-]", " ", title or "")
    words = [w for w in cleaned.split() if w.lower() not in (FILLER_WORDS - {"and"})]
    while words and words[0].isdigit():
        words = words[1:]
    core: list[str] = []
    for word in words:
        key = word.lower()
        if key in MARKETING_TAIL and len(core) >= 3:
            break
        if key in {"premium"} and len(core) >= 3:
            break
        core.append(word)
        if len(core) >= 3 and any(ch.isdigit() for ch in word):
            break
        if len(core) >= 6:
            break
    built = " ".join(core)
    brand = _brand_tokens(title, None)
    if type_q:
        # Prefer "Krone body spray" / "electric toothbrush" over marketing cores.
        if brand:
            branded = f"{' '.join(sorted(brand))} {type_q[0]}"
            # Avoid "attitude krone body spray" duplication if type already has brand words.
            if not any(b in type_q[0].lower() for b in brand):
                return branded.strip()
        return type_q[0]
    return built


def _search_queries(title: str, storefront_url: str | None = None) -> list[str]:
    queries: list[str] = []
    slug_parts = [w for w in _slug_words(storefront_url) if w.lower() not in FILLER_WORDS]
    blob = f"{title} {storefront_url or ''}".lower()
    if "train" in blob and "diffuser" in blob:
        queries.append("mini train shape essential oil diffuser")
        queries.append("steam train essential oil diffuser")
    queries.extend(_product_type_queries(title))
    core = _core_product_query(title)
    if core:
        queries.append(core)
    cleaned = re.sub(r"[^\w\s+-]", " ", title or "")
    keep_and = [
        w
        for w in cleaned.split()
        if w.lower() not in (FILLER_WORDS - {"and"}) and w.lower() not in {"premium"}
    ]
    if keep_and:
        long_q = " ".join(keep_and[:8])
        if long_q.lower() != (core or "").lower():
            queries.append(long_q)
    if slug_parts and len(slug_parts) >= 2:
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


def _is_aggregator_host(host: str) -> bool:
    name = (host or "").lower()
    if name.startswith("www."):
        name = name[4:]
    return any(name == item or name.endswith("." + item) for item in AGGREGATOR_HOSTS)


def _one_per_shop(comparisons: list[dict]) -> list[dict]:
    from app.scrapers.parse import comparison_rank

    best: dict[str, dict] = {}
    for row in comparisons:
        listing = row.get("competitor_listing") or {}
        host = _host(listing.get("url") or "")
        current = best.get(host)
        if current is None:
            best[host] = row
            continue
        current_listing = current.get("competitor_listing") or {}
        if comparison_rank(listing.get("in_stock", True), listing.get("price")) < comparison_rank(
            current_listing.get("in_stock", True), current_listing.get("price")
        ):
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
    """Score title similarity. Also compares brand+model core so marketing
    fluff on either side (Premium / AMOLED / Advanced Health…) does not
    drag a true match below the bar (e.g. 68 vs 78 on Watch 5)."""
    scores = [_fuzzy_titles(ours, theirs)]
    core = _core_product_query(ours)
    if core and core.lower().strip() != (ours or "").lower().strip():
        scores.append(_fuzzy_titles(core, theirs))
    their_core = _core_product_query(theirs)
    if their_core and their_core.lower().strip() != (theirs or "").lower().strip():
        scores.append(_fuzzy_titles(ours, their_core))
        if core:
            scores.append(_fuzzy_titles(core, their_core))
    return round(max(scores), 1)


def _fuzzy_titles(ours: str, theirs: str) -> float:
    a = _normalize_title(ours)
    b = _normalize_title(theirs)
    if not a or not b:
        return 0.0
    set_r = fuzz.token_set_ratio(a, b)
    sort_r = fuzz.token_sort_ratio(a, b)
    partial_r = fuzz.partial_ratio(a, b)
    # Short competitor titles inflate partial_ratio ("face wash" vs long SKU).
    if len(b.split()) <= 5:
        partial_r = min(partial_r, (set_r + sort_r) / 2)
    return max(set_r, sort_r, partial_r)


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


def _is_product_url(url: str, market: Market | None = None) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    host_cmp = host[4:] if host.startswith("www.") else host
    if _is_aggregator_host(host):
        return False
    if any(part in host for part in BLOCKED_HOST_PARTS):
        return False
    if "amazon." in host_cmp:
        return bool(re.search(r"/(dp|gp/product)/[A-Z0-9]{10}", path, re.I))
    if host_cmp.endswith("daraz.pk"):
        return "/products/" in path.lower()
    if SHOP_INDEX.search(path):
        return False
    if BLOCKED_PATH.search(path):
        return False
    if PRODUCT_PATH.search(path) or CATEGORY_PDP.search(path):
        return True
    sites = list(market.discovery_sites) if market and market.discovery_sites else settings.discovery_sites
    known = {s.lower() for s in sites}
    if any(host_cmp.endswith(site) for site in known):
        return bool(PRODUCT_PATH.search(path) or CATEGORY_PDP.search(path))
    if settings.DISCOVERY_OPEN_WEB:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and "-" in parts[-1] and len(parts[-1]) >= 12:
            return True
        return bool(PRODUCT_PATH.search(path))
    return False


def _pack(product, comparisons, skipped, storefront_url, searched, market: Market | None = None) -> dict:
    market = market or get_market(product.get("market_code"))
    currency = product.get("currency") or (market.currency if market else "USD")
    shops = len(comparisons)
    headline, detail, cheaper, difference = _summary_copy(
        product, comparisons, market=market, currency=currency
    )
    return {
        "provider": search_provider(),
        "product_id": product["id"],
        "our_product": {
            "id": product["id"],
            "title": product.get("title"),
            "price": product.get("price"),
            "price_unknown": bool(product.get("price_unknown")) or not (product.get("price") or 0),
            "currency": currency,
            "original_price": product.get("original_price"),
            "original_currency": product.get("original_currency"),
            "marketplace": product.get("marketplace"),
            "url": storefront_url or product.get("url"),
            "in_stock": product.get("in_stock", True) is not False,
        },
        "market": {
            "code": market.code if market else None,
            "country": market.country if market else None,
            "flag": market.flag if market else None,
            "currency": currency,
            "label": market.label if market else None,
        },
        "searched_urls": searched,
        "matches": comparisons,
        "skipped": skipped,
        "match_count": shops,
        "headline": headline,
        "cheaper": cheaper,
        "difference_rs": difference,
        "difference": difference,
        "currency": currency,
        "detail": detail,
    }


def _money_of(amount, market, currency: str) -> str:
    from app.services.markets import format_money

    return format_money(amount, market, currency=currency)


def _board_rows(product: dict, comparisons: list[dict]) -> list[dict]:
    """User plus competitors, in-stock first, then lowest price."""
    rows: list[dict] = []
    try:
        our_price = float(product.get("price") or 0)
    except (TypeError, ValueError):
        our_price = 0
    our_unknown = bool(product.get("price_unknown")) or our_price <= 0
    if not our_unknown:
        rows.append(
            {
                "name": (product.get("marketplace") or "Your shop").strip() or "Your shop",
                "price": our_price,
                "in_stock": product.get("in_stock", True) is not False,
                "you": True,
            }
        )
    for row in comparisons:
        listing = row.get("competitor_listing") or {}
        try:
            price = float(listing.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        rows.append(
            {
                "name": competitor_label(listing.get("competitor") or "a shop"),
                "price": price,
                "in_stock": listing.get("in_stock", True) is not False,
                "you": False,
            }
        )
    rows.sort(key=lambda item: (0 if item["in_stock"] else 1, item["price"], 0 if item["you"] else 1))
    return rows


def _summary_copy(
    product: dict,
    comparisons: list[dict],
    *,
    market,
    currency: str,
) -> tuple[str, str, str | None, float | None]:
    """Headline, detail, cheaper, difference. The user's own price can win."""
    shops = len(comparisons)
    shop_word = "shop" if shops == 1 else "shops"
    if shops == 0:
        return (
            "No matching product pages were found. Try a more specific title, or paste a competitor URL.",
            "Search ran, but nothing cleared the title/price match bar.",
            None,
            None,
        )
    rows = _board_rows(product, comparisons)
    in_stock = [row for row in rows if row["in_stock"]]
    winner = in_stock[0] if in_stock else (rows[0] if rows else None)
    price_unknown = bool(product.get("price_unknown")) or not (product.get("price") or 0)
    try:
        our_price = float(product.get("price") or 0)
    except (TypeError, ValueError):
        our_price = 0
    our_in_stock = product.get("in_stock", True) is not False

    if winner is None:
        return (
            "No matching product pages were found. Try a more specific title, or paste a competitor URL.",
            "Search ran, but nothing cleared the title/price match bar.",
            None,
            None,
        )

    winner_money = _money_of(winner["price"], market, currency)
    winner_stock = "" if winner["in_stock"] else " (out of stock)"
    if price_unknown:
        kind = "Lowest in-stock price" if winner["in_stock"] else "Lowest listed price"
        return (
            f"Found {shops} {shop_word}. Your price couldn't be read.",
            (
                "The site blocked or hid your price, so nothing was guessed. "
                f"{kind} is {winner['name']} at {winner_money}{winner_stock}."
            ),
            None,
            None,
        )

    your_money = _money_of(our_price, market, currency)
    your_stock = "" if our_in_stock else " (out of stock)"
    if winner["you"]:
        nxt = next((row for row in in_stock if not row["you"]), None)
        detail = (
            f"Compared {shops} {shop_word}. Cheapest is {winner['name']} (your price) at {winner_money}."
        )
        difference = 0.0
        if nxt:
            detail += f" Next is {nxt['name']} at {_money_of(nxt['price'], market, currency)}."
            difference = round(nxt["price"] - winner["price"], 2)
        return (
            f"{winner['name']} is the cheapest at {winner_money}.",
            detail,
            "us",
            difference,
        )

    detail = (
        f"Compared {shops} {shop_word}. Cheapest is {winner['name']}{winner_stock} at {winner_money}. "
        f"Your price is {your_money}{your_stock}."
    )
    tie = 1 if currency == "PKR" else 0.01
    if our_in_stock and winner["in_stock"] and abs(our_price - winner["price"]) < tie:
        cheaper = "tie"
        difference = 0.0
    else:
        cheaper = "competitor"
        difference = round(our_price - winner["price"], 2) if our_price else None
    return (
        f"{winner['name']} is the cheapest at {winner_money}{winner_stock}.",
        detail,
        cheaper,
        difference,
    )
