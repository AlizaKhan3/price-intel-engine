from __future__ import annotations

"""
Daraz.pk scraper.

Daraz robots.txt disallows `/catalog/` (search). This scraper therefore
does **not** search Daraz. It only reads a product **detail** page you
already have a URL for (`/products/...`).

The static HTML carries an AggregateOffer (sometimes lowPrice/highPrice,
often with no `offers.price`) plus `pdt_price`. That is parsed over HTTP
so Chromium is only a fallback.
"""
import logging
import re

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.html_product import build_listing
from app.scrapers.parse import parse_price as _parse_price

logger = logging.getLogger(__name__)

PRODUCT_URL_RE = re.compile(r"https?://(www\.)?daraz\.pk/products/", re.I)


class DarazScraper(BaseScraper):
    competitor_name = "daraz"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError(
            "Daraz robots.txt disallows /catalog/ search. "
            "Paste a Daraz product URL instead (https://www.daraz.pk/products/...)."
        )

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        if not PRODUCT_URL_RE.search(url):
            raise ValueError("Expected a daraz.pk /products/ URL")
        clean = url.split("?")[0]
        await page.goto(clean, wait_until="domcontentloaded", timeout=15000)
        html = await page.content()
        listing = build_listing(clean, html, self.competitor_name)
        if listing and not listing.price_unknown:
            return listing
        return listing
