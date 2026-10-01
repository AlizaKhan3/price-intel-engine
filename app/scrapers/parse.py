"""Pure price / title parsing shared by the HTTP and browser scrapers.

Kept free of Playwright so the rules can be tested without a browser.
"""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

# Digits glued to letters are model tokens (710BT, P20i), not prices.
_MODEL_NUMBER_RE = re.compile(
    r"(?i)(?<![a-z0-9])(\d{2,6})(?=[a-z])|(?<=[a-z])(\d{2,6})(?![0-9])"
)
_CURRENCY_RE = re.compile(
    r"(?i)(rs\.?|pkr|usd|eur|gbp|aed|sar|inr|\$|£|€|₹)"
)
_PRICE_NUMBER_RE = re.compile(r"(\d{1,3}(?:[,\s]\d{3})+|\d+)(?:\.(\d{1,2}))?")
_SITE_TITLES = frozenset(
    {
        "amazon",
        "amazon.com",
        "amazon.com.pk",
        "daraz",
        "daraz.pk",
        "sadiq",
        "sadiq.ai",
        "robot check",
        "captcha",
        "access denied",
        "just a moment...",
        "just a moment",
        "attention required",
        "sorry",
        "sorry! something went wrong!",
        "error",
        "continue shopping",
    }
)


def parse_price(text: str | int | float | None) -> float | None:
    """Parse a price token. Does not decide whether the number is a model id."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = float(text)
        return value if value > 0 else None
    raw = unescape(str(text))
    raw = _CURRENCY_RE.sub(" ", raw)
    raw = raw.replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def has_currency(text: str) -> bool:
    return bool(_CURRENCY_RE.search(text or ""))


def model_numbers(title: str) -> set[str]:
    """Numbers that are part of a model token, e.g. 710 in 'Tune 710BT'."""
    found: set[str] = set()
    for match in _MODEL_NUMBER_RE.finditer(title or ""):
        token = match.group(1) or match.group(2)
        if token:
            found.add(token)
    return found


def price_is_model_number(price: float | None, title: str) -> bool:
    if price is None:
        return False
    if abs(price - round(price)) > 1e-6:
        return False
    return str(int(round(price))) in model_numbers(title)


def parse_visible_price(text: str, title: str = "") -> float | None:
    """Price from a short on-page snippet. Rejects titles and model numbers.

    A bare ``710`` inside ``JBL Tune 710BT`` is not a price. ``Rs. 13,999`` is.
    An explicit currency (``Rs. 710``) is kept even when 710 is also a model.
    """
    snippet = re.sub(r"\s+", " ", unescape(text or "")).strip()
    if not snippet or len(snippet) > 80:
        return None
    if len(snippet.split()) > 8:
        return None
    price = parse_price(snippet)
    if price is None:
        return None
    if price_is_model_number(price, title) and not has_currency(snippet):
        return None
    # The whole snippet is basically the product title.
    if title and snippet.lower() in title.lower() and not has_currency(snippet):
        return None
    return price


def clean_page_title(title: str) -> str:
    text = unescape(title or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("&amp;", "&")
    text = re.sub(r"^Amazon\.com\s*:\s*", "", text, flags=re.I)
    text = re.sub(
        r"\s+[\|\-–—]\s+(?:Daraz(?:\.pk)?|Sadiq(?:\.ai)?|Amazon(?:\.[a-z.]+)?|eBay)(?:\s.*)?$",
        "",
        text,
        flags=re.I,
    )
    return text.strip(" -|")


def is_generic_site_title(title: str) -> bool:
    """True when the text is a site name or bot wall, not a product name."""
    text = clean_page_title(title).lower().strip(" .")
    if not text:
        return True
    if text in _SITE_TITLES:
        return True
    if text.startswith("amazon.com") and ":" not in text and len(text) < 24:
        return True
    return False


def title_from_product_url(url: str) -> str:
    """Product name from the URL path. ASIN-only Amazon links return ''."""
    path = urlparse(url or "").path or ""
    parts = [part for part in path.split("/") if part]
    slug = ""
    for part in parts:
        lowered = part.lower()
        if lowered in {"dp", "gp", "product", "products", "p", "item", "itm"}:
            continue
        if re.fullmatch(r"[A-Z0-9]{10}", part, re.I):
            continue
        slug = part
        break
    if not slug and parts:
        slug = parts[-1]
    slug = re.sub(r"-i\d+.*$", "", slug, flags=re.I)
    slug = re.sub(r"\.(html?|php)$", "", slug, flags=re.I)
    if re.fullmatch(r"[A-Z0-9]{10}", slug, re.I):
        return ""
    words = slug.replace("_", " ").replace("-", " ")
    words = re.sub(r"\s+", " ", words).strip()
    if len(words) < 3 or is_generic_site_title(words):
        return ""
    # A single token with no spaces is usually an id, not a name.
    if " " not in words and not re.search(r"[a-zA-Z]{3,}", words):
        return ""
    return words


def _node_type(node: dict) -> str:
    raw = node.get("@type")
    if isinstance(raw, list):
        for item in raw:
            if str(item).lower() == "product":
                return "Product"
            if str(item).lower() == "aggregateoffer":
                return "AggregateOffer"
        return str(raw[0]) if raw else ""
    return str(raw or "")


def iter_jsonld_products(html: str) -> list[dict]:
    products: list[dict] = []
    for raw in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    ):
        try:
            payload = json.loads(raw.strip())
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if _node_type(node) == "Product":
                products.append(node)
            graph = node.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict) and _node_type(item) == "Product":
                        products.append(item)
    return products


def _availability_in_stock(value) -> bool | None:
    if value is None or value == "":
        return None
    text = str(value)
    if re.search(r"OutOfStock|SoldOut|Discontinued", text, re.I):
        return False
    if re.search(r"InStock|PreOrder|LimitedAvailability|OnlineOnly", text, re.I):
        return True
    return None


def _spec_price(offer: dict) -> float | None:
    specs = offer.get("priceSpecification")
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        return None
    selling = None
    list_price = None
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        price = parse_price(spec.get("price"))
        if not price:
            continue
        kind = str(spec.get("priceType") or "")
        if re.search(r"ListPrice|Strikethrough", kind, re.I):
            list_price = list_price or price
            continue
        selling = price
        break
    return selling or list_price


def _one_offer_price(offer: dict) -> float | None:
    if not isinstance(offer, dict):
        return None
    kind = _node_type(offer)
    low = parse_price(offer.get("lowPrice"))
    high = parse_price(offer.get("highPrice"))
    price = parse_price(offer.get("price"))
    spec = _spec_price(offer)
    aggregate = kind == "AggregateOffer" or low is not None or high is not None
    if aggregate:
        return low or price or spec or high
    return price or spec or low or high


def offer_price_and_stock(data: dict | None) -> tuple[float | None, str | None, bool | None]:
    """Return (price, currency, in_stock) from a Product JSON-LD node.

    AggregateOffer uses lowPrice, then price, then highPrice.
    priceSpecification prefers the selling price over ListPrice.
    When several offers exist, the lowest in-stock price wins.
    """
    if not data:
        return None, None, None
    offers = data.get("offers") or {}
    offer_list: list[dict]
    if isinstance(offers, list):
        offer_list = [item for item in offers if isinstance(item, dict)]
    elif isinstance(offers, dict):
        offer_list = [offers]
    else:
        offer_list = []
    if not offer_list:
        return None, None, None

    ranked: list[tuple[tuple, float, str | None, bool | None]] = []
    for offer in offer_list:
        price = _one_offer_price(offer)
        if not price:
            continue
        stock = _availability_in_stock(offer.get("availability"))
        currency = offer.get("priceCurrency")
        if not currency:
            specs = offer.get("priceSpecification")
            if isinstance(specs, dict):
                specs = [specs]
            if isinstance(specs, list):
                for spec in specs:
                    if isinstance(spec, dict) and spec.get("priceCurrency"):
                        currency = spec.get("priceCurrency")
                        break
        # In-stock (or unknown) sorts ahead of explicit out-of-stock.
        oos_rank = 1 if stock is False else 0
        ranked.append(((oos_rank, price), price, str(currency).upper() if currency else None, stock))
    if not ranked:
        stock = _availability_in_stock(offer_list[0].get("availability"))
        return None, None, stock
    ranked.sort(key=lambda item: item[0])
    _, price, currency, stock = ranked[0]
    return price, currency, stock


def pdt_price(html: str) -> float | None:
    """Daraz embeds the displayed price as pdt_price even when JSON-LD omits it."""
    match = re.search(r'"pdt_price"\s*:\s*"([^"]+)"', html or "")
    if not match:
        return None
    return parse_price(match.group(1))


def meta_price(html: str) -> tuple[float | None, str | None]:
    patterns = (
        r'<meta[^>]+itemprop=["\']price["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']price["\']',
        r'<meta[^>]+property=["\'](?:product:price:amount|og:price:amount)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:product:price:amount|og:price:amount)["\']',
    )
    currency = None
    cur = re.search(
        r'<meta[^>]+(?:itemprop=["\']priceCurrency["\']|property=["\']product:price:currency["\'])[^>]+content=["\']([^"\']+)["\']',
        html or "",
        re.I,
    )
    if cur:
        currency = cur.group(1).upper()
    for pattern in patterns:
        match = re.search(pattern, html or "", re.I)
        if not match:
            continue
        price = parse_price(match.group(1))
        if price:
            return price, currency
    return None, currency


def _whole_fraction_price(chunk: str) -> float | None:
    whole = re.search(r'class="a-price-whole">\s*([0-9][0-9,]*)', chunk or "")
    if not whole:
        return None
    tail = chunk[whole.end() : whole.end() + 180]
    frac = re.search(r'class="a-price-fraction">\s*([0-9]+)', tail)
    raw = whole.group(1).replace(",", "")
    if frac:
        raw = f"{raw}.{frac.group(1)}"
    return parse_price(raw)


def amazon_price(html: str) -> float | None:
    """Main buy-box price. Ignores empty a-offscreen placeholders and later widgets."""
    for match in re.finditer(
        r'class="[^"]*(?:priceToPay|apex-pricetopay-value)[^"]*"[\s\S]{0,900}',
        html or "",
    ):
        chunk = match.group(0)
        # Split whole/fraction first. A later a-offscreen in the same window
        # is often a different widget (coupon, recommendation).
        price = _whole_fraction_price(chunk)
        if price:
            return price
        offscreen = re.search(r'class="a-offscreen">\s*([^<]*\d[^<]*)\s*<', chunk)
        if offscreen:
            price = parse_price(offscreen.group(1))
            if price:
                return price
    for match in re.finditer(r'class="a-offscreen">\s*([^<]+)', html or ""):
        raw = match.group(1).strip()
        if not re.search(r"\d", raw):
            continue
        price = parse_price(raw)
        if price:
            return price
    return None


def visible_price_from_html(html: str, title: str) -> float | None:
    """Last-resort price from price-like elements. Never reads h1 or <title>."""
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html or "", flags=re.I)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"<h1[\s\S]*?</h1>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"<title[\s\S]*?</title>", " ", cleaned, flags=re.I)
    pattern = re.compile(
        r"<(?:span|div|p|b|strong|ins)[^>]*(?:itemprop=[\"']price[\"']|class=[\"'][^\"']*price[^\"']*[\"'])[^>]*>(.*?)</(?:span|div|p|b|strong|ins)>",
        re.I | re.S,
    )
    for match in pattern.finditer(cleaned):
        text = re.sub(r"<[^>]+>", " ", match.group(1))
        price = parse_visible_price(text, title)
        if price:
            return price
    return None


def html_document_title(html: str) -> str:
    product = re.search(r'id=["\']productTitle["\'][^>]*>\s*([^<]+)', html or "", re.I)
    if product and product.group(1).strip():
        return clean_page_title(product.group(1))
    og = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html or "",
        re.I,
    )
    if not og:
        og = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html or "",
            re.I,
        )
    if og and og.group(1).strip():
        return clean_page_title(og.group(1))
    title = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
    if title:
        return clean_page_title(title.group(1))
    return ""


def is_blocked_html(html: str, title: str = "") -> bool:
    """Bot, captcha, or interstitial pages. A real product page is not blocked."""
    text = html or ""
    low = text.lower()
    head = low[:20000]
    cleaned = clean_page_title(title or html_document_title(text)).lower().strip(" .")
    has_product = 'id="producttitle"' in low or "application/ld+json" in low and '"@type":"product"' in low.replace(" ", "")
    if cleaned in _SITE_TITLES and not has_product:
        return True
    if "click the button below to continue shopping" in head:
        return True
    if "validatecaptcha" in head and "producttitle" not in low:
        return True
    if "to discuss automated access to amazon data" in head:
        return True
    if "cf-browser-verification" in head or "checking your browser before accessing" in head:
        return True
    if "sorry, we just need to make sure you" in head and "robot" in head:
        return True
    # Amazon's tiny continue-shopping wall (a few KB, no product title).
    if len(text) < 12000 and "amazon" in head and "producttitle" not in low:
        if "continue shopping" in head or "validatecaptcha" in head or "/errors/validatecaptcha" in head:
            return True
    return False


SHOP_BLOCKED_MESSAGE = (
    "This shop blocked automated access. We couldn't read a price from that page."
)


def friendly_error(exc: BaseException | str) -> str:
    """User-facing message. Playwright crash text never reaches the page."""
    text = str(exc or "").strip()
    low = text.lower()
    if re.search(r"\b403\b", low) or "forbidden" in low or re.search(r"\b429\b", low) or "too many requests" in low:
        return SHOP_BLOCKED_MESSAGE
    if any(token in low for token in ("crashed", "target closed", "has been closed", "browser closed")):
        return (
            "The page reader ran out of memory and stopped. "
            "Please try again in a moment."
        )
    if "timeout" in low or "timed out" in low:
        return "That page took too long to open. Try again, or paste a direct product link."
    if "net::" in low or "err_name" in low or "connection" in low and "refused" in low:
        return "We couldn't reach that site. Check the link and try again."
    if any(
        token in low
        for token in (
            "page.goto",
            "playwright",
            "eval_on_selector",
            "browser.new_context",
            "browsertype.launch",
            "executable doesn't exist",
        )
    ):
        return "We couldn't read that page. Try again, or paste a direct product link."
    if not text:
        return "We couldn't read a title and price from that page. Try another product link."
    if len(text) > 320:
        return "Something went wrong while comparing prices. Please try again."
    return text


def comparison_rank(in_stock: bool | None, price: float | None) -> tuple[int, float]:
    """In-stock listings sort ahead of out-of-stock ones, then by price."""
    unavailable = 1 if in_stock is False else 0
    amount = float(price) if price and price > 0 else 1e18
    return (unavailable, amount)


def jsonld_image(data: dict | None) -> str | None:
    if not data:
        return None
    image = data.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        url = image.get("url") or image.get("contentUrl")
        return url if isinstance(url, str) else None
    return image if isinstance(image, str) else None
