from __future__ import annotations

"""Amazon product-page scraper (title + price). Keeps marketplace currency."""
import logging
import re
from urllib.parse import urlparse

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import _parse_price
from app.services.markets import amazon_canonical_host, currency_from_amazon_host

logger = logging.getLogger(__name__)

ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})", re.I)


class AmazonScraper(BaseScraper):
    competitor_name = "amazon"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste an Amazon product URL (/dp/ASIN) instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        asin = _asin(url)
        host = amazon_canonical_host(url)
        clean = f"https://{host}/dp/{asin}" if asin else url.split("?")[0]
        currency = currency_from_amazon_host(host)
        await page.set_extra_http_headers(
            {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        await page.goto(clean, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)

        title = await _text(
            page,
            [
                "#productTitle",
                "#title",
                "h1#title span",
                "h1.a-size-large",
                "h1",
            ],
        )
        if not title:
            og = await page.query_selector('meta[property="og:title"]')
            if og:
                title = await og.get_attribute("content")
        if not title:
            title = await page.title()
        title = _clean_amazon_title(title or "")

        price = None
        for sel in (
            "#corePrice_feature_div .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            ".a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            'span[data-a-color="price"] .a-offscreen',
            'meta[itemprop="price"]',
        ):
            el = await page.query_selector(sel)
            if not el:
                continue
            raw = (await el.get_attribute("content")) or (await el.inner_text())
            price = _parse_price(raw or "")
            if price:
                break

        # Refine currency from page cues when host mapping is wrong / generic.
        body = ""
        try:
            body = (await page.content())[:8000]
        except Exception:
            body = ""
        if "₹" in (title or "") or "₹" in body:
            currency = "INR"
        elif "£" in body:
            currency = "GBP"
        elif "€" in body:
            currency = "EUR"
        elif "AED" in body or "Dhs" in body:
            currency = "AED"
        elif "SAR" in body:
            currency = "SAR"
        elif ("Rs." in body or "PKR" in body) and currency == "USD":
            currency = "PKR"

        if not title:
            title = _title_from_url(url)
        if not title:
            logger.warning("Amazon parse failed %s title=%r price=%r", clean, title, price)
            return None

        # Keep native marketplace price — do not force PKR.
        if price is None or price <= 0:
            price = 0.01  # title-only scrape; discovery can still search

        return CompetitorListing(
            competitor="amazon",
            competitor_product_id=asin or clean,
            title=title.strip(),
            price=float(price),
            currency=currency,
            url=clean,
            image_url=None,
            in_stock=True,
            source="scrape",
        )


def _asin(url: str) -> str | None:
    match = ASIN_RE.search(url or "")
    return match.group(1).upper() if match else None


def _clean_amazon_title(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip())
    text = re.sub(r"\s*:\s*Amazon\.[a-z.].*$", "", text, flags=re.I)
    text = re.sub(r"\s*\|\s*Amazon\.[a-z.].*$", "", text, flags=re.I)
    return text.strip(" -|")


def _title_from_url(url: str) -> str:
    path = urlparse(url).path or ""
    parts = [p for p in path.split("/") if p and p.lower() not in {"dp", "gp", "product"}]
    if not parts:
        return ""
    slug = parts[0] if not re.fullmatch(r"[A-Z0-9]{10}", parts[0], re.I) else (
        parts[1] if len(parts) > 1 else ""
    )
    if not slug or re.fullmatch(r"[A-Z0-9]{10}", slug, re.I):
        return ""
    return slug.replace("-", " ").strip()


async def _text(page, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = await page.query_selector(sel)
        if not el:
            continue
        text = (await el.inner_text()).strip()
        if text:
            return text
    return None
