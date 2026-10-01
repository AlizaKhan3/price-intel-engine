from __future__ import annotations

"""Amazon product-page scraper (title + price). Keeps marketplace currency.

A bot-check page is never turned into a placeholder price, and a title that
is only "Amazon" is never used as a search query.
"""
import logging

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.html_product import AMAZON_BLOCKED_MESSAGE, build_listing, fetch_html
from app.scrapers.parse import friendly_error, is_blocked_html, is_generic_site_title

logger = logging.getLogger(__name__)


class AmazonScraper(BaseScraper):
    competitor_name = "amazon"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste an Amazon product URL (/dp/ASIN) instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        """Playwright fallback. HTTP parsing is tried first by the compare flow."""
        try:
            await page.goto(url.split("?")[0], wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            logger.warning("Amazon browser open failed %s: %s", url, exc)
            raise
        try:
            html = await page.content()
        except Exception as exc:
            logger.warning("Amazon browser content failed %s: %s", url, exc)
            raise
        if is_blocked_html(html):
            listing = build_listing(url, html, "amazon")
            if listing and not is_generic_site_title(listing.title):
                return listing
            raise ValueError(AMAZON_BLOCKED_MESSAGE)
        listing = build_listing(url, html, "amazon")
        if listing and is_generic_site_title(listing.title):
            raise ValueError(AMAZON_BLOCKED_MESSAGE)
        return listing

    async def fetch_product_http(self, url: str) -> CompetitorListing | None:
        try:
            html = await fetch_html(url, timeout=12)
        except Exception as exc:
            logger.warning("Amazon HTTP scrape failed %s: %s", url, friendly_error(exc))
            return None
        if is_blocked_html(html):
            listing = build_listing(url, html, "amazon")
            if listing and listing.price_unknown and not is_generic_site_title(listing.title):
                return listing
            return None
        listing = build_listing(url, html, "amazon")
        if listing and is_generic_site_title(listing.title):
            return None
        return listing


def listing_from_url_only(url: str) -> CompetitorListing | None:
    """Title from the URL slug when Amazon blocks the page. No guessed price."""
    listing = build_listing(
        url,
        "<html><title>Amazon.com</title><p>Click the button below to continue shopping</p>"
        "<form action='/errors/validateCaptcha'></form></html>",
        "amazon",
    )
    if listing is None or is_generic_site_title(listing.title):
        return None
    return listing
