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
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)
settings = get_settings()

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


async def discover_from_storefront(tenant: dict, storefront_url: str) -> dict:
    """
    Paste any product URL → find the same item elsewhere → compare prices.

    Catalog storefront URLs (e.g. Sadiq) still sync from Mongo.
    Any other product page is scraped for title/price first.
    """
    url = (storefront_url or "").strip()
    if is_catalog_storefront_url(url):
        try:
            product_id = product_id_from_storefront_url(url)
            return await discover_product(tenant, product_id, storefront_url=url)
        except ValueError:
            pass
    return await discover_from_external_url(tenant, url)


async def discover_from_external_url(
    tenant: dict,
    product_url: str,
    *,
    max_urls: int | None = None,
) -> dict:
    product = await scrape_url_as_our_product(tenant, product_url)
    return await _discover_with_product(
        tenant,
        product,
        storefront_url=product_url,
        max_urls=max_urls,
        exclude_host=_host(product_url),
    )


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
    return await _discover_with_product(
        tenant,
        product,
        storefront_url=storefront_url or product.get("url"),
        max_urls=max_urls,
        exclude_host=_host(storefront_url or product.get("url") or ""),
    )


async def _discover_with_product(
    tenant: dict,
    product: dict,
    *,
    storefront_url: str | None = None,
    max_urls: int | None = None,
    exclude_host: str | None = None,
) -> dict:
    title = (product.get("title") or "").strip()
    if not title:
        raise ValueError("That product has no title to search with.")

    candidates = await _search_candidates(
        title,
        max_urls=max_urls,
        storefront_url=storefront_url or product.get("url"),
        exclude_host=exclude_host,
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
    our_host = exclude_host or _host(storefront_url or product.get("url") or "")

    for url, listing, error in fetched:
        if error or listing is None:
            skipped.append({"url": url, "reason": error or "Could not read title/price"})
            continue
        if our_host and _host(url) == our_host:
            skipped.append({"url": url, "reason": "Same shop as your pasted link"})
            continue
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
            # Strong title/model hits still count — pasted price can be a fake/outlier
            # listing (e.g. Watch 5 at Rs. 4,500 vs real market ~60k).
            if score < 90:
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
}


async def _search_candidates(
    title: str,
    max_urls: int | None = None,
    storefront_url: str | None = None,
    exclude_host: str | None = None,
) -> list[str]:
    cap = max_urls or settings.DISCOVERY_MAX_URLS
    per_host = max(1, settings.DISCOVERY_PER_HOST)
    queries = _search_queries(title, storefront_url)
    found: list[str] = []
    seen_url: set[str] = set()
    seen_host: dict[str, int] = {}
    source_host = (exclude_host or _host(storefront_url or "")).lower()

    def add(url: str, snippet_title: str = "") -> None:
        clean = _canonical_url(url)
        if not clean or clean in seen_url:
            return
        if not _is_product_url(clean):
            return
        host = _host(clean)
        if source_host and (host == source_host or host.endswith("." + source_host) or source_host.endswith("." + host)):
            return
        if snippet_title:
            score, miss = _match_score(title, snippet_title, storefront_url)
            if miss or score < max(50, settings.DISCOVERY_MIN_SCORE - 18):
                # Snippet titles are often truncated; URL slug can still be a hit.
                if not _url_slug_matches(title, clean, storefront_url):
                    return
        elif not _url_slug_matches(title, clean, storefront_url):
            return
        if seen_host.get(host, 0) >= per_host:
            return
        seen_url.add(clean)
        seen_host[host] = seen_host.get(host, 0) + 1
        found.append(clean)

    logger.info("Discovery queries=%s exclude_host=%s", queries, source_host or None)
    # Open web first so Shopify "best fuzzy organizer" does not crowd out real matches.
    for query in queries[:3]:
        if len(found) >= cap:
            break
        for row in await asyncio.to_thread(search_web, f"{query} Pakistan buy", 12):
            add(row.get("url") or "", row.get("title") or "")
            if len(found) >= cap:
                break
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

    for site in settings.discovery_sites[:10]:
        if len(found) >= cap:
            break
        if source_host and site in source_host:
            continue
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
    slug = slug_from_any_url(storefront_url or "") or slug_from_storefront_url(storefront_url or "") or ""
    return [word for word in slug.replace("_", "-").split("-") if word and not word.isdigit()]


def _search_queries(title: str, storefront_url: str | None = None) -> list[str]:
    queries: list[str] = []
    slug_parts = [w for w in _slug_words(storefront_url) if w.lower() not in FILLER_WORDS]
    blob = f"{title} {storefront_url or ''}".lower()
    if "train" in blob and "diffuser" in blob:
        queries.append("mini train shape essential oil diffuser")
        queries.append("steam train essential oil diffuser")
    core = _core_product_query(title)
    if core:
        queries.append(core)
    # Keep "and" so we search like a person: "jewelry and perfume organizer".
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


def _core_product_query(title: str) -> str:
    """Brand + model only — drop marketing tails like 'Premium Smartwatch with…'."""
    cleaned = re.sub(r"[^\w\s+-]", " ", title or "")
    words = [w for w in cleaned.split() if w.lower() not in (FILLER_WORDS - {"and"})]
    core: list[str] = []
    for word in words:
        key = word.lower()
        if key in MARKETING_TAIL and len(core) >= 3:
            break
        if key in {"premium"} and len(core) >= 3:
            break
        core.append(word)
        # Stop once we have brand + name + a model digit (Samsung Galaxy Watch 5).
        if len(core) >= 3 and any(ch.isdigit() for ch in word):
            break
        if len(core) >= 6:
            break
    return " ".join(core)


def _model_numbers(text: str) -> set[str]:
    """Product model digits (Watch 5 / Watch5), ignoring common size numbers."""
    raw = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text or "")
    raw = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", raw)
    found = set()
    for token in re.findall(r"\b\d{1,4}\b", raw.lower()):
        if token in SIZE_NUMBERS:
            continue
        # Ignore years.
        if len(token) == 4 and token.startswith(("19", "20")):
            continue
        found.add(token)
    return found


def _url_slug_matches(title: str, url: str, storefront_url: str | None = None) -> bool:
    path = (urlparse(url).path or "").lower().replace("-", " ").replace("_", " ").replace("/", " ")
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
    models = _model_numbers(title)
    if models and not (models & _model_numbers(path)):
        return False
    edition = {"pro", "plus", "max", "ultra", "fe"}
    our_ed = set(_normalize_title(title).split()) & edition
    path_ed = path_words & edition
    if models and our_ed != path_ed and (our_ed or path_ed):
        return False
    return True


def _match_score(ours: str, theirs: str, storefront_url: str | None = None) -> tuple[float, str | None]:
    """Return (score, skip_reason). skip_reason set when it is clearly a different item."""
    miss = _missing_required(ours, theirs, storefront_url)
    if miss:
        return 0.0, miss
    return _title_score(ours, theirs), None


def _missing_required(ours: str, theirs: str, storefront_url: str | None) -> str | None:
    our_words = set(_normalize_title(f"{ours} {' '.join(_slug_words(storefront_url))}").split())
    their_words = set(_normalize_title(theirs).split())
    brand = _brand_tokens(ours, storefront_url)
    their_exp = _canonical_tokens(their_words) | their_words
    # Also expand glued tokens (watch5 → watch, 5) for matching.
    their_exp |= _model_numbers(theirs)
    their_exp |= set(re.sub(r"([a-z])(\d)", r"\1 \2", " ".join(their_words)).split())
    brand_ok = _brand_ok(brand, their_exp)

    our_models = _model_numbers(ours)
    their_models = _model_numbers(theirs)
    if our_models and not (our_models & their_models):
        return (
            "Different product (model mismatch: need "
            + "/".join(sorted(our_models))
            + ")"
        )

    edition = {"pro", "plus", "max", "ultra", "fe"}
    our_ed = our_words & edition
    their_ed = their_words & edition
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
        if brand_ok:
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


def _brand_tokens(ours: str, storefront_url: str | None) -> set[str]:
    """First distinctive title tokens — usually the brand/line name (e.g. daily wish)."""
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
    }
    words = [
        w
        for w in _normalize_title(f"{ours} {' '.join(_slug_words(storefront_url))}").split()
        if w not in GENERIC_WORDS and not w.isdigit() and (len(w) > 1 or w == "c")
    ]
    if not words or words[0] in weak_prefix:
        return set()
    return set(words[:2])


def _canonical_tokens(words: set[str]) -> set[str]:
    """Collapse synonyms (jewellery/jewelry, cosmetic/perfume) to one token."""
    mapped = set()
    for word in words:
        mapped.add(TOKEN_CANON.get(word, word))
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
    set_r = fuzz.token_set_ratio(a, b)
    sort_r = fuzz.token_sort_ratio(a, b)
    partial_r = fuzz.partial_ratio(a, b)
    # Short competitor titles inflate partial_ratio ("face wash" vs long SKU).
    if len(b.split()) <= 5:
        partial_r = min(partial_r, (set_r + sort_r) / 2)
    return round(max(set_r, sort_r, partial_r), 1)


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
    if SHOP_INDEX.search(path):
        return False
    if BLOCKED_PATH.search(path):
        return False
    if PRODUCT_PATH.search(path) or CATEGORY_PDP.search(path):
        return True
    known = {s.lower() for s in settings.discovery_sites}
    if any(host_cmp.endswith(site) for site in known):
        return bool(PRODUCT_PATH.search(path) or CATEGORY_PDP.search(path))
    if settings.DISCOVERY_OPEN_WEB:
        parts = [p for p in path.split("/") if p]
        # Hyphenated PDP slug at depth >= 2 (PriceOye-style category trees).
        if len(parts) >= 2 and "-" in parts[-1] and len(parts[-1]) >= 12:
            return True
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
