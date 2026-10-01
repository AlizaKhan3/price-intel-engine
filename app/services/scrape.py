from __future__ import annotations

"""Scrape one competitor product page and attach it to one of your products."""
import asyncio
import logging
import time
from datetime import datetime

from app.config import get_settings
from app.db import get_priceintel_db
from app.models.product import CompetitorListing
from app.models.product import MatchStatus, MatchTier
from app.scrapers.registry import get_scraper
from app.services.catalog_sync import sync_full_catalog
from app.services.compare_summary import explain_prices
from app.services.markets import Market, convert_amount
from app.services.matching.pipeline import find_best_match
from app.services.tenants import tenant_id as tid
from app.services.urls import (
    competitor_from_url,
    competitor_label,
    external_product_id,
    product_id_from_storefront_url,
)

logger = logging.getLogger(__name__)


async def fetch_competitor_listing(competitor: str, competitor_url: str) -> CompetitorListing:
    from app.scrapers.parse import friendly_error, is_generic_site_title

    rows = await fetch_competitor_listings([(competitor, competitor_url)])
    _, listing, error = rows[0]
    if listing and listing.title and not is_generic_site_title(listing.title):
        return listing
    raise ValueError(
        friendly_error(error)
        if error
        else "Could not read a title and price from that page. Try another product URL."
    )


async def scrape_url_as_our_product(
    tenant: dict,
    product_url: str,
    *,
    market: Market | None = None,
) -> dict:
    """
    Read title + price from any product page and store it as a catalog row.

    Used when the pasted link is not from the tenant catalog (generic mode).
    """
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    clean = (product_url or "").strip()
    if not clean.startswith("http"):
        raise ValueError("Paste a full product URL starting with https://")

    competitor = competitor_from_url(clean)
    listing = await fetch_competitor_listing(competitor, clean)
    product_id = external_product_id(clean)
    shop = competitor_label(listing.competitor)
    listing_currency = (listing.currency or "").upper() or (market.currency if market else "USD")
    price = float(listing.price or 0)
    # Align listing currency to market when we need a common compare currency.
    if market and listing_currency != market.currency and price > 0.01:
        price = convert_amount(price, listing_currency, market.currency)
        product_currency = market.currency
        converted_from = listing_currency
    else:
        product_currency = listing_currency if listing_currency else (market.currency if market else "USD")
        converted_from = None

    product = {
        "tenant_id": tenant_key,
        "id": product_id,
        "title": listing.title.strip(),
        "price": price,
        "currency": product_currency,
        "marketplace": shop,
        "marketplace_id": listing.competitor,
        "url": listing.url or clean,
        "image_url": listing.image_url,
        "active": True,
        "in_stock": bool(listing.in_stock) if listing.in_stock is not None else True,
        "source": "external_scrape",
        "synced_at": datetime.utcnow(),
        "market_code": market.code if market else None,
    }
    if converted_from:
        product["original_currency"] = converted_from
        product["original_price"] = float(listing.price or 0)
    from app.scrapers.parse import is_generic_site_title

    if not product["title"] or is_generic_site_title(product["title"]):
        raise ValueError(
            "Could not read a product name from that page. "
            "If this was Amazon, the link was blocked and we did not guess a price "
            "or search for the word Amazon. Paste a URL that includes the product name."
        )
    if listing.price_unknown or product["price"] <= 0:
        # Real title, no price. Discovery can still search; nothing is invented.
        product["price"] = 0
        product["price_unknown"] = True
        product.pop("original_price", None)
        product.pop("original_currency", None)
    await db.catalog_products.update_one(
        {"tenant_id": tenant_key, "id": product_id},
        {"$set": product},
        upsert=True,
    )
    return product


async def fetch_competitor_listings(
    pairs: list[tuple[str, str]],
    *,
    budget_seconds: float | None = None,
) -> list[tuple[str, CompetitorListing | None, str | None]]:
    """Fetch product pages. HTTP first, then one fresh browser page per URL.

    A crash is retried once and does not discard the other URLs. Confirmed
    bot pages are not opened in Chromium (that is what was OOMing on Railway).
    """
    from app.scrapers.browser import launch_chromium
    from app.scrapers.html_product import blocked_message, build_listing, fetch_html
    from app.scrapers.parse import friendly_error

    settings = get_settings()
    budget = settings.COMPARE_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = time.monotonic() + max(1.0, budget)
    slots: list[tuple[str, CompetitorListing | None, str | None] | None] = [None] * len(pairs)
    sem = asyncio.Semaphore(4)

    async def http_one(idx: int, competitor: str, url: str):
        async with sem:
            if time.monotonic() >= deadline:
                return idx, None, "We ran out of time reading product pages.", False
            try:
                html = await fetch_html(url, timeout=10)
            except Exception as exc:
                logger.info("HTTP fetch failed %s: %s", url, exc)
                return idx, None, friendly_error(exc), True
            listing = build_listing(url, html, competitor)
            wall = blocked_message(url, html)
            if wall and listing is None:
                return idx, None, wall, False
            if listing and not listing.price_unknown:
                return idx, listing, None, False
            if listing and listing.source == "blocked":
                note = wall or (
                    "That site showed a bot-check page, so no price was used."
                )
                return idx, listing, note, False
            if listing and listing.price_unknown:
                return idx, listing, "Couldn't read a price on that page.", True
            return idx, None, "No title or price on that page.", True

    http_rows = await asyncio.gather(
        *(http_one(idx, competitor, url) for idx, (competitor, url) in enumerate(pairs))
    )
    pending: list[tuple[int, str, str, CompetitorListing | None, str | None]] = []
    for idx, listing, error, try_browser in http_rows:
        url = pairs[idx][1]
        if listing and not listing.price_unknown and not error:
            slots[idx] = (url, listing, None)
        elif try_browser and time.monotonic() < deadline:
            pending.append((idx, pairs[idx][0], url, listing, error))
        else:
            slots[idx] = (url, listing, error)

    if pending:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            state: dict = {"browser": None}
            try:
                state["browser"] = await launch_chromium(playwright)
                for n, (idx, competitor, url, http_listing, http_error) in enumerate(pending):
                    if time.monotonic() >= deadline:
                        slots[idx] = (
                            url,
                            http_listing,
                            http_error
                            or "We ran out of time opening more pages. Try again, or paste a direct product link.",
                        )
                        continue
                    listing, error = await _read_page_with_retry(
                        playwright,
                        state,
                        competitor,
                        url,
                        settings.SCRAPER_USER_AGENT,
                    )
                    if listing and not listing.price_unknown:
                        slots[idx] = (url, listing, None)
                    elif http_listing and http_listing.title:
                        slots[idx] = (url, http_listing, error or http_error)
                    else:
                        slots[idx] = (url, None, error or http_error or "Couldn't read that page.")
                    if n < len(pending) - 1:
                        await asyncio.sleep(min(0.35, settings.SCRAPER_REQUEST_DELAY_SECONDS))
            finally:
                browser = state.get("browser")
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass

    return [slot if slot is not None else (pairs[i][1], None, "Couldn't read that page.") for i, slot in enumerate(slots)]


async def _read_page_with_retry(playwright, state: dict, competitor: str, url: str, user_agent: str):
    """One URL, its own page. Retry once after a crash by relaunching Chromium."""
    from app.scrapers.browser import block_heavy_resources, is_browser_crash, launch_chromium
    from app.scrapers.parse import friendly_error

    last = "We couldn't read that page."
    for attempt in (1, 2):
        context = None
        try:
            browser = state.get("browser")
            if browser is None or not browser.is_connected():
                state["browser"] = await launch_chromium(playwright)
            context = await state["browser"].new_context(
                user_agent=user_agent,
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            await context.route("**/*", block_heavy_resources)
            page = await context.new_page()
            listing = await get_scraper(competitor).fetch_product(page, url)
            if listing is None:
                return None, "No title or price on that page."
            return listing, None
        except Exception as exc:
            last = friendly_error(exc)
            logger.warning("Fetch failed %s (attempt %s): %s", url, attempt, exc)
            if is_browser_crash(exc) and attempt == 1:
                try:
                    if state.get("browser") is not None:
                        await state["browser"].close()
                except Exception:
                    pass
                try:
                    state["browser"] = await launch_chromium(playwright)
                except Exception as launch_exc:
                    return None, friendly_error(launch_exc)
                continue
            return None, last
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
    return None, last


async def compare_storefront_and_competitor(
    tenant: dict,
    *,
    storefront_url: str,
    competitor_url: str,
    auto_approve: bool = True,
) -> dict:
    from app.services.urls import is_catalog_storefront_url

    if is_catalog_storefront_url(storefront_url):
        product_id = product_id_from_storefront_url(storefront_url)
        await sync_full_catalog(tenant, product_id=product_id)
    else:
        product = await scrape_url_as_our_product(tenant, storefront_url)
        product_id = product["id"]

    competitor = competitor_from_url(competitor_url)
    return await scrape_product_url(
        tenant,
        product_id=product_id,
        competitor=competitor,
        competitor_url=competitor_url,
        auto_approve=auto_approve,
        storefront_url=storefront_url,
        skip_catalog_sync=not is_catalog_storefront_url(storefront_url),
    )


async def scrape_product_url(
    tenant: dict,
    *,
    product_id: str,
    competitor: str,
    competitor_url: str,
    auto_approve: bool = False,
    storefront_url: str | None = None,
    skip_catalog_sync: bool = False,
) -> dict:
    db = get_priceintel_db()
    tenant_key = tid(tenant)
    if not skip_catalog_sync:
        await sync_full_catalog(tenant, product_id=product_id)
    product = await db.catalog_products.find_one({"tenant_id": tenant_key, "id": product_id})
    if not product:
        raise ValueError(
            f"Product {product_id} was not found. Check the product URL."
        )

    listing = await fetch_competitor_listing(competitor, competitor_url)
    if listing.price_unknown or not listing.price:
        if listing.source == "blocked":
            raise ValueError(
                "That competitor page was a bot-check, so we didn't use a guessed price. "
                "Try another product link."
            )
        raise ValueError(
            "We couldn't read a price from that competitor page, so it wasn't compared. "
            "Try another product link."
        )
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
    market_code = product.get("market_code")
    from app.services.markets import get_market

    market = get_market(market_code) if market_code else None
    currency = product.get("currency") or (market.currency if market else None)
    if product.get("price_unknown") or not (product.get("price") or 0):
        from app.services.markets import format_money

        their_price = saved.get("price") or 0
        explained = {
            "cheaper": None,
            "difference_rs": None,
            "difference": None,
            "currency": currency,
            "gap_pct": 0,
            "headline": f"{their_name} lists this at {format_money(their_price, market, currency=currency)}.",
            "detail": (
                f"We couldn't read a price from your link, so this is {their_name}'s price only. "
                "Nothing was guessed."
            ),
        }
    else:
        explained = explain_prices(
            product.get("price") or 0,
            saved.get("price") or 0,
            our_label=our_name,
            competitor_label=their_name,
            market=market,
            currency=currency,
        )
    if saved.get("in_stock") is False and explained.get("headline"):
        explained["headline"] = f"{explained['headline']} (out of stock)"

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
            "price_unknown": bool(product.get("price_unknown")),
            "currency": product.get("currency"),
            "original_price": product.get("original_price"),
            "original_currency": product.get("original_currency"),
            "marketplace": product.get("marketplace"),
            "url": storefront_url or product.get("url"),
            "in_stock": product.get("in_stock", True),
        },
        "competitor_listing": {
            "id": saved["id"],
            "competitor": saved.get("competitor"),
            "title": saved.get("title"),
            "price": saved.get("price"),
            "currency": saved.get("currency") or product.get("currency"),
            "url": saved.get("url"),
            "in_stock": saved.get("in_stock", True),
        },
        "market": {
            "code": market.code if market else None,
            "country": market.country if market else None,
            "flag": market.flag if market else None,
            "currency": product.get("currency") or (market.currency if market else None),
        },
        "match": {
            "confidence": decision.get("confidence"),
            "tier": decision.get("tier"),
            "status": decision.get("status"),
        },
        "comparison": comparison,
    }
