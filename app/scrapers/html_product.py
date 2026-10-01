"""Read a product title and price from static HTML (no browser)."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.models.product import CompetitorListing
from app.scrapers.parse import (
    amazon_price,
    clean_page_title,
    finance_context,
    html_document_title,
    is_blocked_html,
    is_generic_site_title,
    is_monthly_amount,
    iter_jsonld_products,
    jsonld_image,
    labeled_selling_price,
    meta_price,
    offer_price_and_stock,
    pdt_price,
    title_from_product_url,
    visible_price_from_html,
)
from app.services.markets import amazon_canonical_host, currency_from_amazon_host

logger = logging.getLogger(__name__)

BLOCKED_MESSAGE = (
    "That site showed a bot-check page instead of the product. "
    "We didn't guess a price. Try another link, or a URL that includes the product name."
)
AMAZON_BLOCKED_MESSAGE = (
    "Amazon showed a bot-check page instead of the product. "
    "We didn't invent a price or search using the word Amazon. "
    "Paste a link that includes the product name (…/product-name/dp/ASIN), "
    "or compare a shop that isn't blocking this request."
)


def _host(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _currency_for(url: str, competitor: str, explicit: str | None) -> str:
    if explicit:
        return explicit.upper()
    host = _host(url)
    if competitor == "amazon" or "amazon." in host:
        return currency_from_amazon_host(amazon_canonical_host(url))
    if host.endswith(".pk") or competitor == "daraz":
        return "PKR"
    if host == "pacifiko.com" or host.endswith(".pacifiko.com"):
        return "GTQ"
    return "USD"


def _product_id(url: str, competitor: str) -> str:
    if competitor == "amazon" or "amazon." in _host(url):
        match = re.search(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})", url or "", re.I)
        if match:
            return match.group(1).upper()
    if competitor == "daraz":
        match = re.search(r"i(\d+)", url or "")
        if match:
            return match.group(1)
    path = urlparse(url or "").path.rstrip("/")
    return (path.split("/")[-1] or url)[:200]


def _listing(
    *,
    url: str,
    competitor: str,
    title: str,
    price: float | None,
    currency: str | None,
    in_stock: bool | None,
    image_url: str | None,
    source: str,
    price_unknown: bool,
) -> CompetitorListing:
    return CompetitorListing(
        competitor=competitor,
        competitor_product_id=_product_id(url, competitor),
        title=title.strip(),
        price=float(price or 0),
        currency=_currency_for(url, competitor, currency),
        url=(url or "").split("?")[0],
        image_url=image_url,
        in_stock=True if in_stock is None else bool(in_stock),
        source=source,
        price_unknown=price_unknown,
    )


def build_listing(url: str, html: str, competitor: str) -> CompetitorListing | None:
    """Parse one product page. Never invents a price.

    A blocked page returns a title taken from the URL only when that URL
    actually contains a product name, with ``price_unknown`` set. Otherwise
    it returns None so the caller can show a friendly error.
    """
    page_title = html_document_title(html or "")
    blocked = is_blocked_html(html or "", page_title)
    slug_title = title_from_product_url(url)

    if blocked:
        if not slug_title:
            return None
        return _listing(
            url=url,
            competitor=competitor,
            title=slug_title,
            price=None,
            currency=None,
            in_stock=None,
            image_url=None,
            source="blocked",
            price_unknown=True,
        )

    products = iter_jsonld_products(html or "")
    chosen = None
    structured_price = None
    currency = None
    in_stock = None
    image = None
    for product in products:
        name = clean_page_title(str(product.get("name") or ""))
        price, offer_currency, stock = offer_price_and_stock(product)
        if chosen is None and name and not is_generic_site_title(name):
            chosen = product
            image = jsonld_image(product)
            in_stock = stock
        if price:
            chosen = product
            structured_price = price
            currency = offer_currency
            in_stock = stock
            image = jsonld_image(product) or image
            break

    title = ""
    if chosen is not None:
        title = clean_page_title(str(chosen.get("name") or ""))
    if not title or is_generic_site_title(title):
        title = page_title
    if not title or is_generic_site_title(title):
        title = slug_title

    finance = finance_context(html or "")
    labeled = labeled_selling_price(html or "")
    is_amazon = competitor == "amazon" or "amazon." in _host(url)
    buy_box = amazon_price(html or "") if is_amazon else None
    if structured_price and is_monthly_amount(structured_price, finance):
        structured_price = None
    if labeled and is_monthly_amount(labeled, finance):
        labeled = None

    price = None
    source = "http_scrape"
    # The buy box is the selected variant. A structured price can be a different offer.
    if buy_box:
        price = buy_box
        source = "amazon_buybox"
    elif labeled:
        price = labeled
        source = "selling_price"
    elif structured_price:
        price = structured_price
    if price is None:
        meta, meta_currency = meta_price(html or "")
        if meta and not is_monthly_amount(meta, finance):
            price = meta
            currency = currency or meta_currency
    if price is None:
        embedded = pdt_price(html or "")
        if embedded and not is_monthly_amount(embedded, finance):
            price = embedded
            currency = currency or ("PKR" if competitor == "daraz" or _host(url).endswith(".pk") else None)
    if price is None and title:
        loose = visible_price_from_html(html or "", title)
        if loose and not is_monthly_amount(loose, finance):
            price = loose
            source = "visible_text"

    if not title or is_generic_site_title(title):
        return None
    if price is None:
        return _listing(
            url=url,
            competitor=competitor,
            title=title,
            price=None,
            currency=currency,
            in_stock=in_stock,
            image_url=image,
            source="price_missing",
            price_unknown=True,
        )
    return _listing(
        url=url,
        competitor=competitor,
        title=title,
        price=price,
        currency=currency,
        in_stock=in_stock,
        image_url=image,
        source=source,
        price_unknown=False,
    )


def blocked_message(url: str, html: str) -> str | None:
    if not is_blocked_html(html or "", html_document_title(html or "")):
        return None
    host = _host(url)
    if "amazon." in host and not title_from_product_url(url):
        return AMAZON_BLOCKED_MESSAGE
    if not title_from_product_url(url):
        return BLOCKED_MESSAGE
    return None


async def fetch_html(url: str, *, timeout: float = 12.0) -> str:
    settings = get_settings()
    headers = {
        "User-Agent": settings.SCRAPER_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text or ""
