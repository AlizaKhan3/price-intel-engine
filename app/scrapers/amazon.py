from __future__ import annotations

"""Amazon product-page scraper (title + price). Datacenter IPs often get soft-blocked."""
import logging
import re
from urllib.parse import urlparse

from app.config import get_settings
from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import _parse_price

logger = logging.getLogger(__name__)

ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})", re.I)


class AmazonScraper(BaseScraper):
    competitor_name = "amazon"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste an Amazon product URL (/dp/ASIN) instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        asin = _asin(url)
        clean = f"https://www.amazon.com/dp/{asin}" if asin else url.split("?")[0]
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

        currency = "USD"
        body = ""
        try:
            body = (await page.content())[:8000]
        except Exception:
            body = ""
        if "₹" in (title or "") or "₹" in body:
            currency = "INR"
        elif "Rs." in body or "PKR" in body:
            currency = "PKR"
        elif "£" in body:
            currency = "GBP"
        elif "€" in body:
            currency = "EUR"

        if not title:
            title = _title_from_url(url)
        if not title:
            logger.warning("Amazon parse failed %s title=%r price=%r", clean, title, price)
            return None

        # Convert foreign currency so PK comparisons stay meaningful.
        pkr_price = _to_pkr(price, currency) if price else None
        if pkr_price is None or pkr_price <= 0:
            # Title-only scrape still lets discovery search Pakistan shops.
            pkr_price = 1.0

        return CompetitorListing(
            competitor="amazon",
            competitor_product_id=asin or clean,
            title=title.strip(),
            price=float(pkr_price),
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
    text = re.sub(r"\s*:\s*Amazon\.com.*$", "", text, flags=re.I)
    text = re.sub(r"\s*\|\s*Amazon\.com.*$", "", text, flags=re.I)
    return text.strip(" -|")


def _title_from_url(url: str) -> str:
    path = urlparse(url).path or ""
    parts = [p for p in path.split("/") if p and p.lower() not in {"dp", "gp", "product"}]
    if not parts:
        return ""
    slug = parts[0] if not re.fullmatch(r"[A-Z0-9]{10}", parts[0], re.I) else (parts[1] if len(parts) > 1 else "")
    if not slug or re.fullmatch(r"[A-Z0-9]{10}", slug, re.I):
        return ""
    return slug.replace("-", " ").strip()


def _to_pkr(amount: float, currency: str) -> float:
    settings = get_settings()
    rates = {
        "PKR": 1.0,
        "USD": float(getattr(settings, "USD_TO_PKR", 278) or 278),
        "EUR": float(getattr(settings, "USD_TO_PKR", 278) or 278) * 1.08,
        "GBP": float(getattr(settings, "USD_TO_PKR", 278) or 278) * 1.27,
        "INR": 3.3,
    }
    return round(amount * rates.get((currency or "USD").upper(), rates["USD"]), 2)


async def _text(page, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = await page.query_selector(sel)
        if not el:
            continue
        text = (await el.inner_text()).strip()
        if text:
            return text
    return None
