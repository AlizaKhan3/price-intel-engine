from __future__ import annotations

"""Fetch title + price from any competitor product page the user pastes."""
import logging

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.html_product import build_listing
from app.services.urls import competitor_from_url

logger = logging.getLogger(__name__)


class GenericPageScraper(BaseScraper):
    competitor_name = "web"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste a product page URL instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        slug = competitor_from_url(url)
        self.competitor_name = slug
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        html = await page.content()
        listing = build_listing(url, html, slug)
        if listing is None:
            logger.warning("Could not parse product page %s", url)
        return listing
