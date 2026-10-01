"""Parsing rules for prices, bot pages, and search fallbacks."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.scrapers.html_product import build_listing
from app.scrapers.parse import (
    comparison_rank,
    friendly_error,
    is_blocked_html,
    is_generic_site_title,
    offer_price_and_stock,
    parse_price,
    parse_visible_price,
    title_from_product_url,
)
from app.services.web_search import HTML_FALLBACKS, search_web


DARAZ_AGGREGATE = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"JBL Tune 710BT Wireless Over-Ear Headphones","offers":{
  "@type":"AggregateOffer","lowPrice":"19999","highPrice":"24999","priceCurrency":"PKR",
  "availability":"https://schema.org/InStock"}}
</script>
<title>JBL Tune 710BT Wireless Over-Ear Headphones | Daraz.pk</title>
</head><body><h1>JBL Tune 710BT</h1></body></html>
"""

DARAZ_PDT_OOS = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"JBL Tune 710BT Wireless Over-Ear Headphones","offers":{
  "@type":"AggregateOffer","availability":"https://schema.org/OutOfStock"}}
</script>
<title>JBL Tune 710BT | Daraz.pk</title>
</head><body>
<h1>JBL Tune 710BT Wireless Over-Ear Headphones</h1>
<script>var tracking = {"pdt_price":"Rs. 23,999"};</script>
</body></html>
"""

FONEPRO = """
<html><head><title>JBL Tune 710BT Wireless Headphones Best Price in Pakistan</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","name":"JBL Tune 710BT Wireless Headphones Best Price in Pakistan"},
  {"@type":"Product","name":"JBL Tune 710BT Wireless Headphones","offers":[{
    "@type":"Offer",
    "priceSpecification":[
      {"@type":"UnitPriceSpecification","price":"13999.00","priceCurrency":"PKR"},
      {"@type":"UnitPriceSpecification","price":"18999.00","priceCurrency":"PKR","priceType":"https://schema.org/ListPrice"}
    ],
    "availability":"https://schema.org/OutOfStock"
  }]}
]}
</script>
</head><body><h1>JBL Tune 710BT</h1></body></html>
"""

TITLE_ONLY = """
<html><head><title>JBL Tune 710BT Wireless Headphones</title></head>
<body>
<h1>JBL Tune 710BT Wireless Headphones</h1>
<div class="price">JBL Tune 710BT Wireless Headphones</div>
</body></html>
"""

AMAZON_BOT = """
<html><head><title>Amazon.com</title></head><body>
<p>Click the button below to continue shopping</p>
<form action="/errors/validateCaptcha"></form>
<span class="a-offscreen">$1.00</span>
</body></html>
"""

AMAZON_PRODUCT = """
<html><head><title>Amazon.com: soundcore P20i Earbuds</title></head><body>
<span id="productTitle">soundcore by Anker P20i True Wireless Earbuds</span>
<div class="a-price priceToPay apex-pricetopay-value">
  <span class="a-offscreen"> </span>
  <span class="a-price-whole">20</span>
  <span class="a-price-fraction">99</span>
</div>
<span class="a-offscreen">$64.99</span>
</body></html>
"""


class ParseTests(unittest.TestCase):
    def test_currency_prices(self):
        self.assertEqual(parse_price("Rs. 9,500"), 9500)
        self.assertEqual(parse_price("Rs. 23,999"), 23999)
        self.assertEqual(parse_price("$20.99"), 20.99)

    def test_aggregate_offer_uses_low_price(self):
        listing = build_listing("https://www.daraz.pk/products/jbl-tune-710bt-i422504969.html", DARAZ_AGGREGATE, "daraz")
        self.assertIsNotNone(listing)
        self.assertEqual(listing.price, 19999)
        self.assertFalse(listing.price_unknown)
        self.assertTrue(listing.in_stock)
        self.assertNotIn("|", listing.title)

    def test_daraz_pdt_price_and_out_of_stock(self):
        listing = build_listing("https://www.daraz.pk/products/jbl-i422504969.html", DARAZ_PDT_OOS, "daraz")
        self.assertEqual(listing.price, 23999)
        self.assertFalse(listing.in_stock)
        self.assertNotEqual(listing.price, 710)

    def test_price_specification_ignores_list_price_and_model_number(self):
        listing = build_listing(
            "https://fonepro.pk/product/jbl-tune-710bt-wireless-headphones-best-price-in-pakistan/",
            FONEPRO,
            "fonepro",
        )
        self.assertEqual(listing.price, 13999)
        self.assertFalse(listing.in_stock)
        self.assertNotEqual(listing.price, 710)
        price, currency, stock = offer_price_and_stock(
            {
                "offers": {
                    "@type": "AggregateOffer",
                    "highPrice": "5000",
                    "lowPrice": "1200",
                    "priceCurrency": "PKR",
                    "availability": "https://schema.org/OutOfStock",
                }
            }
        )
        self.assertEqual(price, 1200)
        self.assertEqual(currency, "PKR")
        self.assertFalse(stock)

    def test_visible_text_does_not_treat_model_number_as_price(self):
        self.assertIsNone(parse_visible_price("JBL Tune 710BT", "JBL Tune 710BT Wireless Headphones"))
        self.assertIsNone(parse_visible_price("710", "JBL Tune 710BT"))
        self.assertEqual(parse_visible_price("Rs. 13,999", "JBL Tune 710BT"), 13999)
        listing = build_listing("https://dablew.pk/products/jbl-tune-710bt-headphones", TITLE_ONLY, "dablew")
        self.assertTrue(listing is None or listing.price_unknown or listing.price != 710)
        if listing is not None:
            self.assertNotEqual(listing.price, 710)

    def test_amazon_bot_page_never_uses_a_fake_price(self):
        asin_url = "https://www.amazon.com/dp/B0BTYCRJSS"
        self.assertTrue(is_blocked_html(AMAZON_BOT, "Amazon.com"))
        self.assertEqual(title_from_product_url(asin_url), "")
        self.assertIsNone(build_listing(asin_url, AMAZON_BOT, "amazon"))
        slug_url = "https://www.amazon.com/Soundcore-Anker-P20i-Earbuds/dp/B0BTYCRJSS"
        listing = build_listing(slug_url, AMAZON_BOT, "amazon")
        self.assertIsNotNone(listing)
        self.assertTrue(listing.price_unknown)
        self.assertEqual(listing.price, 0)
        self.assertNotEqual(listing.price, 1)
        self.assertNotEqual(listing.price, 0.01)
        self.assertTrue(is_generic_site_title("Amazon"))
        self.assertTrue(is_generic_site_title("Amazon.com"))
        self.assertFalse(is_generic_site_title(listing.title))
        self.assertNotEqual(listing.title.strip().lower(), "amazon")

    def test_amazon_product_price_is_the_buy_box(self):
        listing = build_listing("https://www.amazon.com/dp/B0BTYCRJSS", AMAZON_PRODUCT, "amazon")
        self.assertEqual(listing.price, 20.99)
        self.assertFalse(listing.price_unknown)
        self.assertIn("soundcore", listing.title.lower())
        self.assertNotEqual(listing.title.strip().lower(), "amazon")

    def test_out_of_stock_ranks_after_in_stock(self):
        self.assertLess(comparison_rank(True, 20000), comparison_rank(False, 710))

    def test_friendly_browser_crash(self):
        message = friendly_error("Page.goto: Page crashed")
        self.assertNotIn("Page.goto", message)
        self.assertNotIn("crashed", message.lower())
        self.assertIn("memory", message.lower())
        message = friendly_error("Page.eval_on_selector_all: Target crashed")
        self.assertNotIn("eval_on_selector", message)
        launch = friendly_error("BrowserType.launch: Executable doesn't exist at /ms-playwright/chrome")
        self.assertNotIn("Executable", launch)
        self.assertNotIn("BrowserType", launch)

    def test_forbidden_and_rate_limit_are_friendly(self):
        blocked = friendly_error(
            "Client error '403 Forbidden' for url 'https://shop.example/p' "
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403"
        )
        self.assertIn("blocked automated access", blocked.lower())
        self.assertNotIn("mozilla", blocked.lower())
        self.assertNotIn("403", blocked)
        limited = friendly_error("Client error '429 Too Many Requests' for url 'https://shop.example/p'")
        self.assertIn("blocked automated access", limited.lower())
        self.assertNotIn("429", limited)
        missing = friendly_error("Client error '404 Not Found' for url 'https://shop.example/missing'")
        self.assertNotIn("blocked automated access", missing.lower())

    def test_search_fallback_is_short(self):
        self.assertLessEqual(len(HTML_FALLBACKS), 2)
        called: list[str] = []

        def mark(name):
            def _inner(*_args, **_kwargs):
                called.append(name)
                return []

            return _inner

        settings = SimpleNamespace(
            SERPER_API_KEY="",
            BRAVE_SEARCH_API_KEY="",
            GOOGLE_CSE_ID="",
            GOOGLE_CSE_KEY="",
            SEARCH_HTTP_TIMEOUT_SECONDS=1,
            DISCOVERY_SEARCH_BUDGET_SECONDS=3,
        )
        with (
            patch("app.services.web_search.get_settings", return_value=settings),
            patch("app.services.web_search._ddg_lite", mark("ddg")),
            patch("app.services.web_search._bing_html", mark("bing")),
            patch("app.services.web_search._google_html", mark("google")),
            patch("app.services.web_search._duckduckgo_html", mark("ddghtml")),
            patch("app.services.web_search._ddgs", mark("ddgs")),
        ):
            search_web("jbl tune 710bt headphones", 5, None)
        self.assertEqual(called, ["ddg", "bing"])


if __name__ == "__main__":
    unittest.main()
