from __future__ import annotations

"""
Find candidate product-page URLs via a web search API — not by scraping
marketplace catalog/search pages (Daraz robots.txt disallows /catalog/).

Priority: Serper → Google Programmable Search → DuckDuckGo.
"""
import logging
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SEARCH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def search_web(query: str, max_results: int = 8) -> list[dict]:
    settings = get_settings()
    if settings.SERPER_API_KEY:
        return _serper(query, max_results, settings.SERPER_API_KEY)
    if settings.GOOGLE_CSE_ID and settings.GOOGLE_CSE_KEY:
        return _google_cse(query, max_results, settings.GOOGLE_CSE_ID, settings.GOOGLE_CSE_KEY)
    try:
        items = _ddgs(query, max_results)
    except Exception as exc:
        logger.warning("DuckDuckGo package search failed (%s); using HTML fallback", exc)
        items = []
    if len(items) < 2:
        items = _merge(items, _duckduckgo_html(query, max_results))
    if len(items) < 2:
        items = _merge(items, _bing_html(query, max_results))
    return items[:max_results]


def shopify_products(host: str, query: str, limit: int = 3) -> list[dict]:
    """Public Shopify storefront suggest — used to find a product page on a known shop."""
    try:
        response = httpx.get(
            f"https://{host}/search/suggest.json",
            params={"q": query, "resources[type]": "product", "resources[limit]": limit},
            headers={"User-Agent": SEARCH_UA},
            timeout=12,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return []
        products = (
            ((response.json().get("resources") or {}).get("results") or {}).get("products")
            or []
        )
    except Exception:
        return []
    items = []
    for product in products:
        path = product.get("url") or ""
        if not path and product.get("handle"):
            path = f"/products/{product['handle']}"
        if not path:
            continue
        url = path if path.startswith("http") else f"https://{host}{path}"
        items.append({"title": product.get("title") or "", "url": url})
    return items


def search_provider() -> str:
    settings = get_settings()
    if settings.SERPER_API_KEY:
        return "serper"
    if settings.GOOGLE_CSE_ID and settings.GOOGLE_CSE_KEY:
        return "google_cse"
    return "duckduckgo"


def _serper(query: str, max_results: int, api_key: str) -> list[dict]:
    response = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": max_results, "gl": "pk"},
        timeout=20,
    )
    response.raise_for_status()
    items = []
    for row in (response.json().get("organic") or [])[:max_results]:
        url = row.get("link")
        if url:
            items.append({"title": row.get("title") or "", "url": url})
    return items


def _google_cse(query: str, max_results: int, cx: str, key: str) -> list[dict]:
    response = httpx.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"q": query, "cx": cx, "key": key, "num": min(max_results, 10)},
        timeout=20,
    )
    response.raise_for_status()
    items = []
    for row in (response.json().get("items") or [])[:max_results]:
        url = row.get("link")
        if url:
            items.append({"title": row.get("title") or "", "url": url})
    return items


def _ddgs(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS

    items = []
    # Yahoo respects site: and Pakistan queries. The DuckDuckGo engine crashes on
    # Python 3.9 TLS 1.3 and the HTML fallback often ignores site: filters.
    with DDGS(verify=False) as client:
        for row in client.text(
            query,
            region="pk-en",
            max_results=max_results,
            backend="yahoo",
        ) or []:
            url = row.get("href") or row.get("url")
            if url:
                items.append({"title": row.get("title") or "", "url": url})
    return items


def _duckduckgo_html(query: str, max_results: int) -> list[dict]:
    response = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": SEARCH_UA},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    items = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        response.text,
        re.I | re.S,
    ):
        url = _unwrap_ddg(unescape(match.group(1)))
        title = re.sub(r"<[^>]+>", "", unescape(match.group(2))).strip()
        if url:
            items.append({"title": title, "url": url})
        if len(items) >= max_results:
            break
    return items


def _bing_html(query: str, max_results: int) -> list[dict]:
    try:
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": query, "cc": "PK", "setlang": "en"},
            headers={"User-Agent": SEARCH_UA},
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception:
        logger.warning("Bing HTML search failed")
        return []
    items = []
    seen = set()
    for match in re.finditer(r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', response.text, re.I | re.S):
        url = unescape(match.group(1)).split("&")[0]
        if url in seen or "microsoft.com" in url or "bing.com" in url:
            continue
        seen.add(url)
        title = re.sub(r"<[^>]+>", "", unescape(match.group(2))).strip()
        items.append({"title": title, "url": url})
        if len(items) >= max_results:
            break
    return items


def _merge(left: list[dict], right: list[dict]) -> list[dict]:
    seen = {(row.get("url") or "").split("#")[0] for row in left}
    merged = list(left)
    for row in right:
        url = (row.get("url") or "").split("#")[0]
        if url and url not in seen:
            seen.add(url)
            merged.append(row)
    return merged


def _unwrap_ddg(url: str) -> str:
    if "uddg=" in url:
        parsed = urlparse(url)
        values = parse_qs(parsed.query).get("uddg") or []
        if values:
            return unquote(values[0])
    return url
