from __future__ import annotations

"""Fetch title + price from any competitor product page the user pastes."""
import json
import logging
import re
from urllib.parse import urlparse

from app.models.product import CompetitorListing
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import _parse_price
from app.services.urls import competitor_from_url

logger = logging.getLogger(__name__)


class GenericPageScraper(BaseScraper):
    competitor_name = "web"

    async def search(self, page, query: str) -> list[CompetitorListing]:
        raise PermissionError("Paste a product page URL instead of searching.")

    async def fetch_product(self, page, url: str) -> CompetitorListing | None:
        slug = competitor_from_url(url)
        self.competitor_name = slug
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        data = await _json_ld_product(page)
        title = (data or {}).get("name")
        price = _offer_price(data)
        image_url = _image(data)
        in_stock = _in_stock(data)

        if not title:
            title = await page.title()
            og = await page.query_selector('meta[property="og:title"]')
            if og:
                title = (await og.get_attribute("content")) or title
        if price is None:
            price = await _meta_price(page)
        if price is None:
            price_text = await _visible_price(page)
            price = _parse_price(price_text or "")

        if not title or price is None:
            logger.warning("Could not parse product page %s title=%r price=%r", url, title, price)
            return None

        path = urlparse(url).path.rstrip("/")
        product_key = path.split("/")[-1] or url
        return CompetitorListing(
            competitor=slug,
            competitor_product_id=product_key[:200],
            title=str(title).strip(),
            price=price,
            url=url.split("?")[0],
            image_url=image_url,
            in_stock=in_stock if in_stock is not None else True,
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


def _offer_price(data: dict | None) -> float | None:
    if not data:
        return None
    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    return _parse_price(str(offers.get("price") or offers.get("lowPrice") or ""))


def _image(data: dict | None) -> str | None:
    if not data:
        return None
    image = data.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        return image.get("url")
    return image if isinstance(image, str) else None


def _in_stock(data: dict | None) -> bool | None:
    if not data:
        return None
    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    avail = str(offers.get("availability") or "")
    if not avail:
        return None
    return "OutOfStock" not in avail


async def _meta_price(page) -> float | None:
    selectors = [
        'meta[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            content = await el.get_attribute("content")
            parsed = _parse_price(content or "")
            if parsed:
                return parsed
    return None


async def _visible_price(page) -> str | None:
    selectors = [
        '[itemprop="price"]',
        ".price",
        ".product-price",
        "[class*='price']",
        "h1",
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if not el:
            continue
        text = (await el.inner_text()).strip()
        if re.search(r"\d", text):
            return text
    return None
