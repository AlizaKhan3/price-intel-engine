"""Parsing rules for prices, bot pages, and search fallbacks."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.scrapers.html_product import build_listing
from app.scrapers.parse import (
    amazon_price,
    comparison_rank,
    friendly_error,
    is_blocked_html,
    is_generic_site_title,
    offer_price_and_stock,
    parse_price,
    parse_visible_price,
    title_from_product_url,
)
from app.services.markets import can_convert_currency, convert_amount
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

SHOPABUNDA_FINANCE = """
<html><head>
<title>$7/mo - Finance PUMA Women's Plush Soft Nylon Tote | Buy Now, Pay Later</title>
<meta property="og:title" content="$7/mo - Finance PUMA Women's Plush Soft Nylon Tote | Buy Now, Pay Later">
<script type="application/ld+json">
{"@type":"Product","name":"PUMA Women's Plush Soft Nylon Tote with Padded Straps",
 "offers":{"@type":"Offer","price":"7","priceCurrency":"USD","availability":"https://schema.org/InStock"}}
</script>
</head><body>
<div class="product-price"><span id="productPrice">$29.48</span></div>
<span class="compare-at">was <s>$29.99</s></span>
<p>As low as $7 / mo</p>
<span class="price-badge-area">$14/mo*</span>
</body></html>
"""

AMAZON_TOTE = """
<html><head><title>Amazon.com: PUMA Women's Tote</title></head><body>
<span id="productTitle">PUMA Women's Plush Soft Nylon Tote with Padded Straps</span>
<div id="corePriceDisplay_desktop_feature_div">
  <div class="a-price priceToPay apex-pricetopay-value">
    <span class="a-offscreen"></span>
    <span class="a-price-whole">31</span>
    <span class="a-price-fraction">22</span>
  </div>
</div>
<span>$129.17 Shipping &amp; Import Charges to Pakistan</span>
<div class="sl-carousel-card">
  <span class="a-offscreen">$143.50</span>
</div>
</body></html>
"""

AMAZON_CAROUSEL_ONLY = """
<html><head><title>Amazon.com: PUMA Women's Tote</title></head><body>
<span id="productTitle">PUMA Women's Plush Soft Nylon Tote with Padded Straps</span>
<div id="corePriceDisplay_desktop_feature_div"></div>
<div class="sl-carousel-card">
  <span class="a-price" data-a-size="l"><span class="a-offscreen">$143.50</span>
  <span class="a-price-whole">143</span><span class="a-price-fraction">50</span></span>
</div>
<span class="a-color-secondary">$129.17 Shipping &amp; Import Charges to Pakistan</span>
</body></html>
"""

AMAZON_BUYBOX_WITHOUT_CLASS = """
<html><head><title>Amazon.com: PUMA Women's Tote</title></head><body>
<span id="productTitle">PUMA Women's Plush Soft Nylon Tote with Padded Straps</span>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price" data-a-size="xl"><span class="a-offscreen">$31.22</span>
    <span class="a-price-whole">31</span><span class="a-price-fraction">22</span>
  </span>
</div>
<div class="sl-carousel-card">
  <span class="a-price"><span class="a-offscreen">$143.50</span>
  <span class="a-price-whole">143</span><span class="a-price-fraction">50</span></span>
</div>
</body></html>
"""

PACIFIKO_GTQ = """
<html><head><title>Tote Bag</title>
<script type="application/ld+json">
{"@type":"Product","name":"PUMA Women's Plush Soft Nylon Tote",
 "offers":{"@type":"Offer","price":"400","priceCurrency":"GTQ","availability":"https://schema.org/InStock"}}
</script>
</head><body></body></html>
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

    def test_shopabunda_uses_cash_price_not_monthly_finance(self):
        listing = build_listing(
            "https://www.shopabunda.com/products/puma-womens-plush-soft-nylon-tote",
            SHOPABUNDA_FINANCE,
            "shopabunda",
        )
        self.assertEqual(listing.price, 29.48)
        self.assertNotEqual(listing.price, 7)
        self.assertNotEqual(listing.price, 29.99)
        self.assertFalse(listing.price_unknown)
        self.assertNotIn("/mo", listing.title.lower())
        self.assertIn("PUMA", listing.title)

    def test_amazon_buy_box_ignores_carousel_and_shipping(self):
        listing = build_listing(
            "https://www.amazon.com/PUMA-Womens-Padded-Closure-Outlook/dp/B0DTKJ8H36",
            AMAZON_TOTE,
            "amazon",
        )
        self.assertEqual(listing.price, 31.22)
        self.assertNotEqual(listing.price, 143.50)
        self.assertNotEqual(listing.price, 129.17)
        self.assertFalse(listing.price_unknown)
        self.assertIsNone(amazon_price(AMAZON_CAROUSEL_ONLY))
        missing = build_listing(
            "https://www.amazon.com/PUMA-Womens-Padded-Closure-Outlook/dp/B0DTKJ8H36",
            AMAZON_CAROUSEL_ONLY,
            "amazon",
        )
        self.assertTrue(missing.price_unknown)
        self.assertEqual(missing.price, 0)
        plain = build_listing(
            "https://www.amazon.com/PUMA-Womens-Padded-Closure-Outlook/dp/B0DTKJ8H36",
            AMAZON_BUYBOX_WITHOUT_CLASS,
            "amazon",
        )
        self.assertEqual(plain.price, 31.22)
        self.assertFalse(plain.price_unknown)

    def test_pacifiko_quetzales_are_not_labeled_as_dollars(self):
        listing = build_listing(
            "https://www.pacifiko.com/compras-en-linea/puma-tote&pid=abc",
            PACIFIKO_GTQ,
            "pacifiko",
        )
        self.assertEqual(listing.price, 400)
        self.assertEqual(listing.currency, "GTQ")
        self.assertEqual(convert_amount(400, "GTQ", "USD"), 51.61)
        self.assertTrue(can_convert_currency("GTQ", "USD"))
        self.assertFalse(can_convert_currency("XYZ", "USD"))
        self.assertEqual(convert_amount(400, "XYZ", "USD"), 400)

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


class AmazonFetchRetryTests(unittest.TestCase):
    def test_amazon_http_retries_until_the_buy_box_is_present(self):
        from app.services.scrape import fetch_competitor_listings

        pages = [AMAZON_CAROUSEL_ONLY, AMAZON_CAROUSEL_ONLY, AMAZON_TOTE]

        async def fake_fetch(_url, timeout=12):
            return pages.pop(0)

        async def run():
            with patch("app.scrapers.html_product.fetch_html", side_effect=fake_fetch):
                return await fetch_competitor_listings(
                    [
                        (
                            "amazon",
                            "https://www.amazon.com/PUMA-Womens-Padded-Closure-Outlook/dp/B0DTKJ8H36",
                        )
                    ],
                    budget_seconds=10,
                )

        rows = asyncio.run(run())
        listing = rows[0][1]
        self.assertEqual(listing.price, 31.22)
        self.assertFalse(listing.price_unknown)
        self.assertEqual(pages, [])


if __name__ == "__main__":
    unittest.main()
