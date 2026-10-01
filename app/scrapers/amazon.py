from __future__ import annotations

"""Amazon product-page scraper (title + price). Keeps marketplace currency."""
import logging
import re
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import _parse_price
from app.services.markets import amazon_canonical_host, currency_from_amazon_host

logger = logging.getLogger(__name__)

ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})", re.I)
TITLE_RE = re.compile(
    r'id="productTitle"[^>]*>\s*([^<]+)|'
    r'property="og:title"\s+content="([^"]+)"|'
    r'name="title"\s+content="([^"]+)"|'
    r"<title>([^<]+)</title>",
    re.I,
)
PRICE_RE = re.compile(
    r'class="a-offscreen"[^>]*>\s*([^<]+)|'
    r'itemprop="price"\s+content="([^"]+)"|'
    r'"priceAmount"\s*:\s*([0-9.]+)|'
    r'"price"\s*:\s*"?([0-9.]+)"?',
    re.I,
)


class AmazonScraper(BaseScraper):
    competitor_name = "amazon"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste an Amazon product URL (/dp/ASIN) instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        """Prefer Playwright; on crash/timeout fall back to plain HTTP (Railway-safe)."""
        try:
            listing = await self._fetch_with_playwright(page, url)
            if listing and listing.title:
                return listing
        except Exception as exc:
            logger.warning("Amazon Playwright scrape failed %s: %s", url, exc)

        listing = await self.fetch_product_http(url)
        if listing:
            return listing
        return listing_from_url_only(url)

    async def _fetch_with_playwright(self, page, url: str) -> CompetitorListing | None:
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
        await page.goto(clean, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1200)

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
            try:
                og = await page.query_selector('meta[property="og:title"]')
                if og:
                    title = await og.get_attribute("content")
            except Exception:
                title = None
        if not title:
            try:
                title = await page.title()
            except Exception:
                title = None
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
            try:
                el = await page.query_selector(sel)
            except Exception:
                break
            if not el:
                continue
            raw = (await el.get_attribute("content")) or (await el.inner_text())
            price = _parse_price(raw or "")
            if price:
                break

        body = ""
        try:
            body = (await page.content())[:8000]
        except Exception:
            body = ""
        currency = _currency_from_cues(title, body, currency)

        if not title:
            title = _title_from_url(url)
        if not title:
            logger.warning("Amazon parse failed %s title=%r price=%r", clean, title, price)
            return None

        if price is None or price <= 0:
            price = 0.01

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

    async def fetch_product_http(self, url: str) -> CompetitorListing | None:
        """Lightweight HTTP scrape — avoids Chromium OOM/crashes on small Railway boxes."""
        asin = _asin(url)
        host = amazon_canonical_host(url)
        clean = f"https://{host}/dp/{asin}" if asin else url.split("?")[0]
        currency = currency_from_amazon_host(host)
        settings = get_settings()
        headers = {
            "User-Agent": settings.SCRAPER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = await client.get(clean)
                html = response.text or ""
        except Exception as exc:
            logger.warning("Amazon HTTP scrape failed %s: %s", clean, exc)
            return None

        if len(html) < 400:
            return None

        title = _title_from_html(html) or _title_from_url(url)
        price = _price_from_html(html)
        currency = _currency_from_cues(title, html[:8000], currency)

        if not title:
            return None
        if price is None or price <= 0:
            price = 0.01

        return CompetitorListing(
            competitor="amazon",
            competitor_product_id=asin or clean,
            title=title.strip(),
            price=float(price),
            currency=currency,
            url=clean,
            image_url=None,
            in_stock=True,
            source="http_scrape",
        )


def listing_from_url_only(url: str) -> CompetitorListing | None:
    """Last resort so discovery can still search by title slug / ASIN."""
    asin = _asin(url)
    host = amazon_canonical_host(url)
    clean = f"https://{host}/dp/{asin}" if asin else (url or "").split("?")[0]
    title = _title_from_url(url)
    if not title and asin:
        title = f"Amazon product {asin}"
    if not title:
        return None
    return CompetitorListing(
        competitor="amazon",
        competitor_product_id=asin or clean,
        title=title.strip(),
        price=0.01,
        currency=currency_from_amazon_host(host),
        url=clean,
        image_url=None,
        in_stock=True,
        source="url_fallback",
    )


def _asin(url: str) -> str | None:
    match = ASIN_RE.search(url or "")
    return match.group(1).upper() if match else None


def _clean_amazon_title(title: str) -> str:
    text = (
        (title or "")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"\s*:\s*Amazon\.[a-z.].*$", "", text, flags=re.I)
    text = re.sub(r"\s*\|\s*Amazon\.[a-z.].*$", "", text, flags=re.I)
    text = re.sub(r"^Amazon\.com\s*:\s*", "", text, flags=re.I)
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


def _title_from_html(html: str) -> str:
    match = TITLE_RE.search(html or "")
    if not match:
        return ""
    raw = next((g for g in match.groups() if g), "") or ""
    return _clean_amazon_title(raw)


def _price_from_html(html: str) -> float | None:
    for match in PRICE_RE.finditer(html or ""):
        raw = next((g for g in match.groups() if g), "") or ""
        price = _parse_price(raw)
        if price and price > 0:
            return price
    return None


def _currency_from_cues(title: str, body: str, default: str) -> str:
    currency = default
    blob = f"{title or ''}\n{body or ''}"
    if "₹" in blob:
        return "INR"
    if "£" in blob:
        return "GBP"
    if "€" in blob:
        return "EUR"
    if "AED" in blob or "Dhs" in blob:
        return "AED"
    if "SAR" in blob:
        return "SAR"
    if ("Rs." in blob or "PKR" in blob) and currency == "USD":
        return "PKR"
    return currency


async def _text(page, selectors: list[str]) -> str | None:
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
        except Exception:
            return None
        if not el:
            continue
        text = (await el.inner_text()).strip()
        if text:
            return text
    return None
