from __future__ import annotations

"""
Daraz.pk scraper.

Daraz robots.txt disallows `/catalog/` (search). This scraper therefore
does **not** search Daraz. It only reads a product **detail** page you
already have a URL for (`/products/...`).

Pass a Daraz product URL from Postman. We extract title + price from
JSON-LD when present, then fall back to visible page text.
"""
import json
import logging
import re

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper

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
        await page.goto(clean, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)

        data = await _json_ld_product(page)
        if not data:
            data = await _next_data_product(page)
        title = (data or {}).get("name")
        price = _offer_price(data)
        image_url = _image(data)
        in_stock = _in_stock(data)

        if not title:
            title = await _text(
                page,
                [
                    "h1",
                    ".pdp-mod-product-badge-title",
                    '[class*="pdp-mod-product-badge-title"]',
                    '[data-qa-locator="product-title"]',
                ],
            )
        if not title:
            og = await page.query_selector('meta[property="og:title"]')
            if og:
                title = await og.get_attribute("content")
        if price is None:
            price_text = await _text(
                page,
                [
                    ".pdp-price",
                    ".pdp-v2-product-price",
                    '[class*="pdp-price"]',
                    '[data-qa-locator="product-price"]',
                ],
            )
            price = _parse_price(price_text or "")
        if price is None:
            price = await _meta_price(page)
        url_price = _price_from_url(url)
        if url_price and (price is None or price < 1):
            price = url_price

        if not title or price is None:
            # Last resort: page title often is "Name - Daraz.pk"
            if not title:
                raw = (await page.title() or "").split(" - ")[0].strip()
                title = raw or None
            if title and title.strip().lower() in {"error", "access denied", "captcha", "robot check"}:
                title = None
            logger.warning("Could not parse Daraz product page %s title=%r price=%r", url, title, price)
            if not title or price is None:
                return None

        canonical = (data or {}).get("url") or clean
        return CompetitorListing(
            competitor=self.competitor_name,
            competitor_product_id=_extract_id(canonical),
            title=title.strip(),
            price=price,
            url=canonical,
            image_url=image_url,
            in_stock=in_stock,
            source="scrape",
        )


async def _json_ld_product(page) -> dict | None:
    scripts = await page.eval_on_selector_all(
        'script[type="application/ld+json"]',
        "els => els.map(e => e.textContent)",
    )
    for raw in scripts or []:
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") in ("Product", ["Product"]):
                return node
            if isinstance(node, dict) and node.get("@graph"):
                for item in node["@graph"]:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        return item
    return None


async def _next_data_product(page) -> dict | None:
    """Daraz often embeds product fields in __NEXT_DATA__ when JSON-LD is missing."""
    try:
        raw = await page.eval_on_selector(
            "script#__NEXT_DATA__",
            "el => el && el.textContent",
        )
        if not raw:
            return None
        payload = json.loads(raw)
    except Exception:
        return None

    def walk(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title")
            price = (
                node.get("price")
                or node.get("salePrice")
                or (node.get("skus") or [{}])[0].get("price")
                if isinstance(node.get("skus"), list) and node.get("skus")
                else None
            )
            if name and price not in (None, "", 0, "0"):
                return {"name": name, "offers": {"price": price}, "image": node.get("image")}
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(payload)


async def _meta_price(page) -> float | None:
    for sel in (
        'meta[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
    ):
        el = await page.query_selector(sel)
        if el:
            parsed = _parse_price((await el.get_attribute("content")) or "")
            if parsed:
                return parsed
    return None


def _offer_price(data: dict | None) -> float | None:
    if not data:
        return None
    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    return _parse_price(str(offers.get("price") or ""))


def _image(data: dict | None) -> str | None:
    if not data:
        return None
    image = data.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        return image.get("url")
    return image if isinstance(image, str) else None


def _in_stock(data: dict | None) -> bool:
    if not data:
        return True
    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    avail = str((offers or {}).get("availability") or "")
    return "OutOfStock" not in avail


async def _text(page, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if text:
                return text
    return None


def _price_from_url(url: str) -> float | None:
    from urllib.parse import parse_qs, urlparse

    values = parse_qs(urlparse(url).query).get("price") or []
    return _parse_price(values[0]) if values else None


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"(rs\.?|pkr)", " ", str(text), flags=re.I)
    cleaned = cleaned.replace(",", "").replace("₹", "")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    return float(match.group(1))


def _extract_id(url: str) -> str:
    match = re.search(r"i(\d+)-s(\d+)\.html", url)
    return match.group(0) if match else url.rstrip("/").split("/")[-1]
