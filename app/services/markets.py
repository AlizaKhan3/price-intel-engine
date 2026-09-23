from __future__ import annotations

"""
Marketplace / country context for compare + discovery.

Detect country from the product URL when possible; otherwise the UI asks
the user to pick a market. Prices stay in that market's currency (no silent
PKR conversion).
"""
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class Market:
    code: str
    country: str
    flag: str
    currency: str
    currency_symbol: str
    search_gl: str
    search_country: str
    search_region: str
    query_locale: str
    discovery_sites: tuple[str, ...] = field(default_factory=tuple)
    amazon_host: str | None = None

    @property
    def label(self) -> str:
        return f"{self.country} {self.flag}".strip()


# Approximate mid-market rates vs 1 USD (for cross-currency display only).
_USD_RATES = {
    "USD": 1.0,
    "PKR": 278.0,
    "GBP": 0.79,
    "EUR": 0.92,
    "AED": 3.67,
    "INR": 83.0,
    "SAR": 3.75,
    "CAD": 1.36,
    "AUD": 1.52,
}

_PK_SITES = (
    "homducts.pk",
    "daraz.pk",
    "smartaccessories.pk",
    "apricot.com.pk",
    "shopperspk.com",
    "telemart.pk",
    "kiswa.pk",
    "priceoye.pk",
    "fonepro.pk",
    "starcity.pk",
    "highfy.pk",
    "needbazaar.pk",
    "shopaholic.pk",
)

MARKETS: dict[str, Market] = {
    "us": Market(
        code="us",
        country="United States",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        search_gl="us",
        search_country="US",
        search_region="us-en",
        query_locale="United States",
        discovery_sites=("amazon.com", "walmart.com", "target.com", "bestbuy.com", "ebay.com"),
        amazon_host="www.amazon.com",
    ),
    "uk": Market(
        code="uk",
        country="United Kingdom",
        flag="🇬🇧",
        currency="GBP",
        currency_symbol="£",
        search_gl="uk",
        search_country="GB",
        search_region="uk-en",
        query_locale="United Kingdom",
        discovery_sites=("amazon.co.uk", "argos.co.uk", "johnlewis.com", "currys.co.uk"),
        amazon_host="www.amazon.co.uk",
    ),
    "ae": Market(
        code="ae",
        country="United Arab Emirates",
        flag="🇦🇪",
        currency="AED",
        currency_symbol="AED ",
        search_gl="ae",
        search_country="AE",
        search_region="ae-en",
        query_locale="UAE",
        discovery_sites=("amazon.ae", "noon.com", "sharaf.com"),
        amazon_host="www.amazon.ae",
    ),
    "in": Market(
        code="in",
        country="India",
        flag="🇮🇳",
        currency="INR",
        currency_symbol="₹",
        search_gl="in",
        search_country="IN",
        search_region="in-en",
        query_locale="India",
        discovery_sites=("amazon.in", "flipkart.com", "myntra.com", "ajio.com"),
        amazon_host="www.amazon.in",
    ),
    "pk": Market(
        code="pk",
        country="Pakistan",
        flag="🇵🇰",
        currency="PKR",
        currency_symbol="Rs. ",
        search_gl="pk",
        search_country="PK",
        search_region="pk-en",
        query_locale="Pakistan",
        discovery_sites=_PK_SITES,
        amazon_host=None,
    ),
    "sa": Market(
        code="sa",
        country="Saudi Arabia",
        flag="🇸🇦",
        currency="SAR",
        currency_symbol="SAR ",
        search_gl="sa",
        search_country="SA",
        search_region="sa-en",
        query_locale="Saudi Arabia",
        discovery_sites=("amazon.sa", "noon.com", "jarir.com"),
        amazon_host="www.amazon.sa",
    ),
    "ca": Market(
        code="ca",
        country="Canada",
        flag="🇨🇦",
        currency="CAD",
        currency_symbol="CA$",
        search_gl="ca",
        search_country="CA",
        search_region="ca-en",
        query_locale="Canada",
        discovery_sites=("amazon.ca", "walmart.ca", "bestbuy.ca"),
        amazon_host="www.amazon.ca",
    ),
    "au": Market(
        code="au",
        country="Australia",
        flag="🇦🇺",
        currency="AUD",
        currency_symbol="A$",
        search_gl="au",
        search_country="AU",
        search_region="au-en",
        query_locale="Australia",
        discovery_sites=("amazon.com.au", "jbhifi.com.au", "kogan.com"),
        amazon_host="www.amazon.com.au",
    ),
    "de": Market(
        code="de",
        country="Germany",
        flag="🇩🇪",
        currency="EUR",
        currency_symbol="€",
        search_gl="de",
        search_country="DE",
        search_region="de-de",
        query_locale="Germany",
        discovery_sites=("amazon.de", "otto.de", "zalando.de"),
        amazon_host="www.amazon.de",
    ),
}

# Host / TLD → market code (longest match wins via sorted check).
_HOST_MARKET = (
    ("amazon.com.be", "de"),
    ("amazon.com.au", "au"),
    ("amazon.co.uk", "uk"),
    ("amazon.co.jp", "us"),  # fallback USD display if JP not listed
    ("amazon.com", "us"),
    ("amazon.ae", "ae"),
    ("amazon.sa", "sa"),
    ("amazon.in", "in"),
    ("amazon.ca", "ca"),
    ("amazon.de", "de"),
    ("amazon.fr", "de"),
    ("amazon.it", "de"),
    ("amazon.es", "de"),
    ("daraz.pk", "pk"),
    ("daraz.lk", "pk"),
    ("sadiq.ai", "pk"),
    ("priceoye.pk", "pk"),
    ("telemart.pk", "pk"),
    ("homducts.pk", "pk"),
    ("apollosports.pk", "pk"),
    ("noon.com", "ae"),
    ("flipkart.com", "in"),
    ("walmart.com", "us"),
    ("target.com", "us"),
    ("bestbuy.com", "us"),
    ("argos.co.uk", "uk"),
    ("shein.com", "us"),
    ("us.shein.com", "us"),
    ("uk.shein.com", "uk"),
    ("ae.shein.com", "ae"),
    ("in.shein.com", "in"),
    ("pk.shein.com", "pk"),
)


def list_markets() -> list[Market]:
    order = ("us", "uk", "ae", "in", "pk", "sa", "ca", "au", "de")
    return [MARKETS[c] for c in order if c in MARKETS]


def get_market(code: str | None) -> Market | None:
    if not code:
        return None
    return MARKETS.get(code.strip().lower())


def detect_market_from_url(url: str) -> Market | None:
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for needle, code in _HOST_MARKET:
        if host == needle or host.endswith("." + needle):
            return MARKETS.get(code)
    # Country-coded Shein / generic *.co.uk etc.
    if host.endswith(".co.uk") or host.endswith(".uk"):
        return MARKETS["uk"]
    if host.endswith(".com.au") or host.endswith(".au"):
        return MARKETS["au"]
    if host.endswith(".pk"):
        return MARKETS["pk"]
    if host.endswith(".ae"):
        return MARKETS["ae"]
    if host.endswith(".in") and not host.endswith("linkedin.com"):
        return MARKETS["in"]
    if host.endswith(".sa"):
        return MARKETS["sa"]
    if host.endswith(".ca"):
        return MARKETS["ca"]
    if host.endswith(".de"):
        return MARKETS["de"]
    return None


def resolve_market(
    url: str,
    manual_code: str | None = None,
) -> tuple[Market | None, str]:
    """
    Resolve market for a compare run.

    Returns (market, source) where source is:
      - "manual"   — user selected a country
      - "detected" — inferred from URL
      - "none"     — need the user to pick
    Manual selection always wins when provided.
    """
    manual = get_market(manual_code)
    if manual:
        return manual, "manual"
    detected = detect_market_from_url(url)
    if detected:
        return detected, "detected"
    return None, "none"


def format_money(amount: float | None, market: Market | None, *, currency: str | None = None) -> str:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return "—"
    cur = (currency or (market.currency if market else "USD")).upper()
    if market and cur == market.currency:
        symbol = market.currency_symbol
    else:
        symbol = {
            "USD": "$",
            "GBP": "£",
            "EUR": "€",
            "INR": "₹",
            "PKR": "Rs. ",
            "AED": "AED ",
            "SAR": "SAR ",
            "CAD": "CA$",
            "AUD": "A$",
        }.get(cur, cur + " ")
    if cur in {"PKR", "JPY", "KRW"}:
        return f"{symbol}{value:,.0f}"
    if abs(value - round(value)) < 0.001:
        return f"{symbol}{value:,.0f}"
    return f"{symbol}{value:,.2f}"


def convert_amount(amount: float, from_currency: str, to_currency: str) -> float:
    """Convert via USD mid rates. Same currency → unchanged."""
    src = (from_currency or "").upper()
    dst = (to_currency or "").upper()
    if not amount or src == dst:
        return float(amount or 0)
    src_per_usd = _USD_RATES.get(src)
    dst_per_usd = _USD_RATES.get(dst)
    if not src_per_usd or not dst_per_usd:
        return float(amount)
    usd = float(amount) / src_per_usd
    return round(usd * dst_per_usd, 2)


def currency_from_amazon_host(host: str) -> str:
    market = detect_market_from_url(f"https://{host}/")
    return market.currency if market else "USD"


def amazon_canonical_host(url: str) -> str:
    """Keep the marketplace TLD from the pasted Amazon URL."""
    host = (urlparse(url or "").hostname or "www.amazon.com").lower()
    if "amazon." not in host:
        return "www.amazon.com"
    if not host.startswith("www."):
        host = "www." + host
    return host
