from __future__ import annotations

"""
Standalone demo — proves the matching pipeline works, with no database
or network required. Run with:

    python scripts/demo_matching.py

Uses sample data shaped like what's actually on sadiq.ai: a branded
electronics item (clean Tier A match) and an unbranded fashion item
(fuzzy Tier B match with a deliberately imperfect competitor title, the
kind you'd actually see across two different marketplaces).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.matching.pipeline import find_best_match

# --- Case 1: Electronics — has brand + model number -> Tier A ---
our_earbuds = {
    "id": "sadiq-001",
    "title": "Ronin Dominator R-7035 Gaming Earbuds",
    "brand": "Ronin",
    "model_number": "R-7035",
    "barcode": None,
    "category": "electronics",
}
competitor_candidates_electronics = [
    {
        "id": "daraz-101",
        "title": "Ronin R-7035 Dominator Wireless Gaming Earbuds Bluetooth 5.3",
        "brand": "Ronin",
        "model_number": "R-7035",
        "barcode": None,
        "category": "electronics",
        "price": 5990,
    },
    {
        "id": "daraz-102",
        "title": "Generic Bluetooth Earbuds Black",
        "brand": "Generic",
        "model_number": None,
        "barcode": None,
        "category": "electronics",
        "price": 1200,
    },
]

# --- Case 2: Fashion — unbranded, no model number -> Tier B (fuzzy) ---
our_suit = {
    "id": "sadiq-002",
    "title": "Crimson Petals: Sapphire Digital Printed Lawn 3-Piece Suit",
    "brand": None,
    "model_number": None,
    "barcode": None,
    "category": "fashion",
}
competitor_candidates_fashion = [
    {
        "id": "daraz-201",
        "title": "Sapphire Printed Lawn 3Pc Suit - Crimson Petals Deep Red",
        "brand": None,
        "model_number": None,
        "barcode": None,
        "category": "fashion",
        "price": 2400,
    },
    {
        "id": "daraz-202",
        "title": "Men's Leather Formal Shoes Black",
        "brand": None,
        "model_number": None,
        "barcode": None,
        "category": "fashion",
        "price": 3500,
    },
]


def run(label: str, product: dict, candidates: list[dict]):
    print(f"\n=== {label} ===")
    print(f"Our product: {product['title']!r}")
    decision = find_best_match(product, candidates)
    if not decision:
        print("-> No match found above the minimum confidence threshold.")
        return
    matched_candidate = next(c for c in candidates if c["id"] == decision["competitor_listing_id"])
    print(f"-> Matched: {matched_candidate['title']!r}")
    print(f"   competitor price: Rs.{matched_candidate['price']:,}")
    print(f"   tier: {decision['tier']}, confidence: {decision['confidence']}, status: {decision['status']}")


if __name__ == "__main__":
    run("Electronics (Tier A: brand + model)", our_earbuds, competitor_candidates_electronics)
    run("Fashion (Tier B: fuzzy title match)", our_suit, competitor_candidates_fashion)
