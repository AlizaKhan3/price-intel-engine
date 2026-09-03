from __future__ import annotations

"""
Example scraper — Daraz.pk.

*** IMPORTANT: THIS FILE NEEDS TO BE VERIFIED AGAINST THE LIVE SITE. ***

I could not test this against the real, current daraz.pk DOM from this
environment (no general internet access in the sandbox this was built
in). The structure below is a realistic, correctly-shaped Playwright
scraper — the search flow, selector strategy, and data extraction
pattern are all standard for this class of site — but the exact CSS
selectors WILL need a five-minute check against the live page before
this runs for real:

  1. Open https://www.daraz.pk/catalog/?q=<something> in a browser
  2. Right-click a product card -> Inspect
  3. Update SELECTOR_* below to match what you see
  4. Large marketplaces often front listing pages with anti-bot checks
     (Cloudflare/PerimeterX). If Playwright gets blocked, your options,
     cheapest first: slow down further, rotate a residential proxy, or
     use a managed scraping API (ScraperAPI, Bright Data, ScrapingBee —
     all have free trial tiers) that handles headless-browser + proxy +
     CAPTCHA solving for you.

Use this file as the template for every other competitor scraper
(Telemart, iShopping.pk, etc.) — same shape, different selectors.
"""
from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper

SEARCH_URL = "https://www.daraz.pk/catalog/?q={query}"

# --- verify these against the live DOM before first run ---
SELECTOR_RESULT_CARD = "div[data-qa-locator='product-item']"
SELECTOR_TITLE = ".title--wFj93"
SELECTOR_PRICE = ".price--NVB62"
SELECTOR_LINK = "a"
SELECTOR_IMAGE = "img"


class DarazScraper(BaseScraper):
    competitor_name = "daraz"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        url = SEARCH_URL.format(query=query.replace(" ", "+"))
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_selector(SELECTOR_RESULT_CARD, timeout=10000)

        cards = await page.query_selector_all(SELECTOR_RESULT_CARD)
        listings: list[CompetitorListing] = []

        for card in cards[:10]:  # cap results per query to keep this polite
            title_el = await card.query_selector(SELECTOR_TITLE)
            price_el = await card.query_selector(SELECTOR_PRICE)
            link_el = await card.query_selector(SELECTOR_LINK)
            image_el = await card.query_selector(SELECTOR_IMAGE)

            if not (title_el and price_el and link_el):
                continue

            title = (await title_el.inner_text()).strip()
            price_text = (await price_el.inner_text()).strip()
            href = await link_el.get_attribute("href")
            image_url = await image_el.get_attribute("src") if image_el else None

            price = _parse_price(price_text)
            if price is None or not href:
                continue

            url_full = href if href.startswith("http") else f"https:{href}"

            listings.append(CompetitorListing(
                competitor=self.competitor_name,
                competitor_product_id=_extract_id(url_full),
                title=title,
                price=price,
                url=url_full,
                image_url=image_url,
            ))

        return listings


def _parse_price(text: str) -> float | None:
    import re
    digits = re.sub(r"[^\d.]", "", text)
    return float(digits) if digits else None


def _extract_id(url: str) -> str:
    import re
    match = re.search(r"i(\d+)-s(\d+)\.html", url)
    return match.group(0) if match else url
