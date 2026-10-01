from __future__ import annotations

"""
Base scraper.

Every competitor scraper follows the same contract: given a search
query (usually your product's title, or its brand+model), return zero
or more `CompetitorListing`-shaped candidates found on that site.

Design choices baked in here on purpose:
  - a fixed delay between requests (SCRAPER_REQUEST_DELAY_SECONDS)
  - a descriptive, honest User-Agent that identifies this bot and gives
    a contact email — the polite/standard practice for automated
    fetching, and it means a site owner can email you instead of just
    blocking your IP
  - Playwright (real browser) rather than raw HTTP, because most modern
    Pakistani e-commerce sites render listings client-side with JS

Before pointing this at any real site, check that site's /robots.txt
and Terms of Service, and keep request volume low and human-paced.
"""
import asyncio
import logging
from abc import ABC, abstractmethod

from playwright.async_api import async_playwright

from app.config import get_settings
from app.models.product import CompetitorListing
from app.scrapers.browser import block_heavy_resources, is_browser_crash, launch_chromium

logger = logging.getLogger(__name__)
settings = get_settings()


class BaseScraper(ABC):
    competitor_name: str

    @abstractmethod
    async def search(self, page, query: str) -> list[CompetitorListing]:
        """Given a Playwright page and a search query, return listings found."""
        raise NotImplementedError

    async def _open_page(self, browser):
        context = await browser.new_context(user_agent=settings.SCRAPER_USER_AGENT)
        await context.route("**/*", block_heavy_resources)
        page = await context.new_page()
        return context, page

    async def run_search(self, query: str) -> list[CompetitorListing]:
        async with async_playwright() as p:
            browser = await launch_chromium(p)
            context, page = await self._open_page(browser)
            try:
                results = await self.search(page, query)
            finally:
                await context.close()
                await browser.close()

        await asyncio.sleep(settings.SCRAPER_REQUEST_DELAY_SECONDS)
        return results

    async def run_batch(self, queries: list[str]) -> dict[str, list[CompetitorListing]]:
        """Run `search` for many queries. Each query gets its own page so one
        crash does not take down the rest."""
        results = {}
        async with async_playwright() as p:
            browser = await launch_chromium(p)
            try:
                for query in queries:
                    context = None
                    try:
                        if not browser.is_connected():
                            browser = await launch_chromium(p)
                        context, page = await self._open_page(browser)
                        results[query] = await self.search(page, query)
                    except Exception as exc:
                        logger.exception("Scrape failed for query=%r on %s", query, self.competitor_name)
                        results[query] = []
                        if is_browser_crash(exc):
                            try:
                                await browser.close()
                            except Exception:
                                pass
                            browser = await launch_chromium(p)
                    finally:
                        if context is not None:
                            await context.close()
                    await asyncio.sleep(settings.SCRAPER_REQUEST_DELAY_SECONDS)
            finally:
                await browser.close()
        return results
