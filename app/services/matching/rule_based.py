from __future__ import annotations

"""
Tier A matching — deterministic, near-zero false positives.

Use for categories where products have real identifiers: Electronics,
branded Beauty/Health, packaged Groceries. If two listings share a
barcode, or share a normalized brand + model number, they are almost
certainly the same product.

This tier needs no ML, no thresholds to tune, and is cheap to run on
every catalog sync. It should be tried FIRST for every product, before
falling back to fuzzy/embedding matching.
"""
import re


def _normalize(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[^a-z0-9]", "", value.lower())


def rule_based_match(sadiq_product: dict, candidate: dict) -> float | None:
    """
    Returns a confidence score (0-100) if this is a deterministic match,
    or None if the rule doesn't apply (not a rejection — just "no opinion",
    so the caller should fall through to fuzzy/embedding matching).
    """
    # Strongest signal: identical barcode (EAN/UPC).
    b1, b2 = _normalize(sadiq_product.get("barcode")), _normalize(candidate.get("barcode"))
    if b1 and b2 and b1 == b2:
        return 100.0

    # Next best: same brand + same model number.
    brand1, brand2 = _normalize(sadiq_product.get("brand")), _normalize(candidate.get("brand"))
    model1, model2 = _normalize(sadiq_product.get("model_number")), _normalize(candidate.get("model_number"))

    if brand1 and brand2 and model1 and model2 and brand1 == brand2 and model1 == model2:
        return 98.0

    return None
