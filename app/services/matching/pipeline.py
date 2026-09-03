from __future__ import annotations

"""
Matching pipeline — ties Tier A (rule-based) and Tier B (fuzzy/embedding)
together into one decision per (your product, competitor listing) pair.

Why tiered, and why this matters for "all categories at once":

  Electronics, branded Health/Beauty, packaged Groceries
      -> usually have a brand + model number or barcode
      -> Tier A fires, confidence 98-100, auto-approved, ~zero manual work

  Fashion, Home Decor, generic/unbranded accessories
      -> no reliable identifier, sellers write their own descriptions
      -> Tier A never fires, falls through to Tier B (fuzzy, +embeddings later)
      -> confidence is inherently lower and noisier
      -> these go to a review queue instead of being auto-trusted

This means you can turn the pipeline on for the WHOLE catalog on day one:
structured categories start delivering trustworthy comparisons almost
immediately, while unstructured categories accumulate a review queue
that a human clears in minutes per day — instead of blocking launch on
building perfect fashion-matching first.
"""
import logging

from app.config import get_settings
from app.models.product import MatchStatus, MatchTier
from app.services.matching.fuzzy_text import top_candidates
from app.services.matching.rule_based import rule_based_match

logger = logging.getLogger(__name__)
settings = get_settings()


def find_best_match(product: dict, candidates: list[dict], min_score: int | None = None) -> dict | None:
    """
    Given one catalog product and a pool of competitor listing candidates
    (already pre-filtered by competitor + roughly the same category, to
    keep this cheap), return the best match decision or None if nothing
    clears the minimum bar.
    """
    floor = min_score if min_score is not None else settings.MATCH_MIN_SCORE

    for candidate in candidates:
        score = rule_based_match(product, candidate)
        if score is not None:
            return _decision(product, candidate, MatchTier.RULE_BASED, score)

    ranked = top_candidates(product, candidates, k=1)
    if not ranked:
        return None

    best_candidate, score = ranked[0]
    if score < floor:
        return None

    # Embeddings extension: if `score` lands in an ambiguous middle band
    # (say 60-85), re-rank the top-5 fuzzy candidates with
    # embedding_match_scores() from embeddings.py.
    return _decision(product, best_candidate, MatchTier.FUZZY_TEXT, score)


def _decision(product: dict, candidate: dict, tier: MatchTier, score: float, auto_approve: int | None = None) -> dict:
    bar = auto_approve if auto_approve is not None else settings.MATCH_AUTO_APPROVE_SCORE
    status = MatchStatus.APPROVED if score >= bar else MatchStatus.PENDING
    return {
        "product_id": product["id"],
        "competitor_listing_id": candidate["id"],
        "tier": tier.value if hasattr(tier, "value") else tier,
        "confidence": score,
        "status": status.value if hasattr(status, "value") else status,
    }
