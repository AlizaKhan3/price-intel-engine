"""Summary ranking, model codes, accessories, and non-shop links."""
from __future__ import annotations

import unittest

from app.api.routes_ui import _leaderboard_html, _page
from app.services.discovery import (
    _is_product_url,
    _missing_required,
    _model_numbers,
    _pack,
    _url_slug_matches,
)
from app.services.scrape import usable_typed_name


def _row(shop: str, price: float, *, in_stock: bool = True, title: str = "JBL Tune 710BT") -> dict:
    return {
        "headline": f"{shop} row",
        "cheaper": "competitor",
        "competitor_listing": {
            "competitor": shop,
            "title": title,
            "price": price,
            "in_stock": in_stock,
            "url": f"https://{shop}.example/p",
        },
    }


class SummaryTests(unittest.TestCase):
    def _product(self, price: float, *, in_stock: bool = True) -> dict:
        return {
            "id": "jbl-710",
            "title": "JBL Tune 710BT",
            "price": price,
            "currency": "PKR",
            "marketplace": "Sadiq",
            "in_stock": in_stock,
            "market_code": "pk",
        }

    def test_user_is_named_when_their_price_is_lowest(self):
        packed = _pack(
            self._product(3040),
            [_row("techmen", 14990), _row("vmart", 500, in_stock=False)],
            [],
            "https://www.sadiq.ai/product-details/jbl",
            [],
        )
        detail = packed["detail"]
        self.assertIn("Sadiq (your price)", detail)
        self.assertNotIn("Cheapest is Techmen", detail)
        self.assertNotIn("Cheapest is Vmart", detail)
        self.assertIn("cheapest", packed["headline"].lower())
        self.assertIn("Sadiq", packed["headline"])
        self.assertEqual(packed["cheaper"], "us")
        self.assertTrue(packed["our_product"]["in_stock"])

    def test_out_of_stock_shop_is_not_called_cheapest(self):
        packed = _pack(
            self._product(23999),
            [_row("vmart", 500, in_stock=False), _row("techmen", 14990)],
            [],
            "https://www.sadiq.ai/product-details/jbl",
            [],
        )
        self.assertIn("Cheapest is Techmen", packed["detail"])
        self.assertNotIn("Cheapest is Vmart", packed["detail"])
        self.assertNotIn("(out of stock)", packed["headline"].lower())

    def test_no_matches_does_not_call_the_user_cheapest(self):
        packed = _pack(self._product(46999), [], [], "https://priceoye.pk/mobiles/samsung/samsung-galaxy-a16", [])
        self.assertIn("No matching product pages", packed["headline"])
        self.assertNotIn("Cheapest is", packed["detail"])

    def test_leaderboard_ranks_in_stock_first(self):
        product = self._product(3040)
        matches = [_row("techmen", 14990), _row("vmart", 500, in_stock=False)]
        packed = _pack(product, matches, [], "https://www.sadiq.ai/p", [])
        html = _leaderboard_html(packed["our_product"], matches, currency="PKR")
        first = html.split("1st</span>", 1)[1].split("</div>", 1)[0]
        self.assertIn("Sadiq", first)
        self.assertNotIn("Vmart", first)
        self.assertNotIn("Out of stock", first)
        self.assertIn("Out of stock", html)
        self.assertIn("In-stock shops rank first", html)
        self.assertLess(html.find(">Sadiq<"), html.find(">Vmart<"))


class ModelMatchTests(unittest.TestCase):
    def test_alphanumeric_codes_stay_intact(self):
        self.assertIn("a16", _model_numbers("Samsung Galaxy A16"))
        self.assertNotIn("16", _model_numbers("Samsung Galaxy A16"))
        self.assertIn("a16", _model_numbers("Samsung Galaxy A 16"))
        self.assertIn("s24", _model_numbers("Galaxy S24"))
        self.assertIn("p20i", _model_numbers("soundcore by Anker P20i"))
        self.assertIn("710bt", _model_numbers("JBL Tune 710BT"))
        self.assertIn("5", _model_numbers("Galaxy Watch 5 40mm"))
        self.assertNotIn("40", _model_numbers("Galaxy Watch 5 40mm"))

    def test_model_code_mismatch_and_accessories(self):
        a17 = _missing_required("Samsung Galaxy A16", "Samsung Galaxy A17", None)
        self.assertIn("model mismatch", a17)
        cover = _missing_required(
            "Samsung Galaxy A16",
            "Samsung Galaxy A16 Silicone Back Cover",
            None,
        )
        self.assertIn("accessory", cover)
        glass = _missing_required(
            "Samsung Galaxy A16",
            "Tempered Glass Screen Protector for Samsung Galaxy A16",
            None,
        )
        self.assertIn("accessory", glass)
        charger_for = _missing_required(
            "Samsung Galaxy A16",
            "Charger for Samsung Galaxy A16",
            None,
        )
        self.assertIn("accessory", charger_for)
        same = _missing_required(
            "Samsung Galaxy A16",
            "Samsung Galaxy A16 6GB 128GB",
            None,
        )
        self.assertIsNone(same)
        earbuds = _missing_required("soundcore P20i Earbuds", "soundcore P20 Earbuds", None)
        self.assertIn("model mismatch", earbuds)
        other_tune = _missing_required("JBL Tune 710BT", "JBL Tune 510BT", None)
        self.assertIn("model mismatch", other_tune)
        same_tune = _missing_required(
            "JBL Tune 710 Headphones",
            "JBL Tune 710BT Wireless Over-Ear Headphones",
            None,
        )
        self.assertIsNone(same_tune)

    def test_source_accessory_can_match_an_accessory(self):
        reason = _missing_required(
            "OnePlus 65W SuperVOOC Charger",
            "OnePlus 65W Supervooc Charger",
            None,
        )
        self.assertTrue(reason is None or "accessory" not in reason)

    def test_slug_rejects_cover_and_wrong_model(self):
        self.assertFalse(
            _url_slug_matches(
                "Samsung Galaxy A16",
                "https://www.daraz.pk/products/samsung-galaxy-a16-back-cover-i123.html",
            )
        )
        self.assertFalse(
            _url_slug_matches(
                "Samsung Galaxy A16",
                "https://www.daraz.pk/products/samsung-galaxy-a17-i999.html",
            )
        )
        self.assertTrue(
            _url_slug_matches(
                "Samsung Galaxy A16",
                "https://www.daraz.pk/products/samsung-galaxy-a16-i100.html",
            )
        )

    def test_price_guides_are_not_product_candidates(self):
        self.assertFalse(_is_product_url("https://qeemat.com.pk/samsung-galaxy-a16-price-in-pakistan"))
        self.assertFalse(_is_product_url("https://www.dablew.pk/products/jbl-tune-710bt-headphones"))
        self.assertFalse(_is_product_url("https://www.whatmobile.com.pk/Samsung_Galaxy_A16"))
        self.assertTrue(_is_product_url("https://priceoye.pk/mobiles/samsung/samsung-galaxy-a16"))
        self.assertTrue(
            _is_product_url("https://www.daraz.pk/products/samsung-galaxy-a16-i100.html")
        )


class ProductNameFieldTests(unittest.TestCase):
    def test_compare_form_keeps_a_typed_product_name(self):
        html = _page(product_name="soundcore P20i earbuds")
        self.assertIn('name="product_name"', html)
        self.assertIn("soundcore P20i earbuds", html)
        self.assertIn("blocks the page", html)

    def test_typed_name_ignores_a_site_name(self):
        self.assertEqual(usable_typed_name("Amazon"), "")
        self.assertEqual(usable_typed_name("  soundcore P20i  "), "soundcore P20i")


if __name__ == "__main__":
    unittest.main()
