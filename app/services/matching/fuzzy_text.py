from __future__ import annotations

"""
Tier B matching — fuzzy text similarity.

Use when there's no barcode/model number to key on (this covers most
Fashion, Home Decor, and other private-label products on a marketplace
like sadiq.ai — a "Crimson Petals lawn 3-piece suit" has no SKU that
any competitor site will also use).

rapidfuzz is pure Python/C, needs no model download, and runs fast
enough to score thousands of candidates per second — good for an MVP
and cheap enough to keep running in production.

Because text similarity alone produces false positives (two unrelated
red shirts will score high), matches from this tier should default to
`MatchStatus.PENDING` and go through human review before being trusted
in price comparisons — see pipeline.py.
"""
from rapidfuzz import fuzz


def fuzzy_text_match(sadiq_product: dict, candidate: dict) -> float:
    """
    Returns a 0-100 similarity score combining:
      - token_sort_ratio on the full title (handles word-order differences)
      - a same-category bonus (products in different categories are
        never the same item, even if titles happen to overlap)
    """
    title_score = fuzz.token_sort_ratio(
        sadiq_product.get("title", ""), candidate.get("title", "")
    )

    same_category = (
        sadiq_product.get("category")
        and candidate.get("category")
        and sadiq_product["category"].lower() == candidate["category"].lower()
    )

    score = title_score
    if not same_category:
        score *= 0.6  # heavily penalize cross-category matches, don't rule out entirely

    return round(score, 1)


def top_candidates(sadiq_product: dict, candidates: list[dict], k: int = 5) -> list[tuple[dict, float]]:
    """Score every candidate and return the top-k, sorted best-first."""
    scored = [(c, fuzzy_text_match(sadiq_product, c)) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
