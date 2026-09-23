from __future__ import annotations

"""Human-readable price comparison (market-aware currency)."""

from app.services.markets import Market, format_money


def explain_prices(
    our_price: float,
    competitor_price: float,
    *,
    our_label: str = "Your store",
    competitor_label: str = "Competitor",
    market: Market | None = None,
    currency: str | None = None,
) -> dict:
    our = float(our_price or 0)
    theirs = float(competitor_price or 0)
    difference = round(our - theirs, 2)
    cur = currency or (market.currency if market else "USD")

    def money(amount: float) -> str:
        return format_money(amount, market, currency=cur)

    if our <= 0 or theirs <= 0:
        return {
            "cheaper": None,
            "difference_rs": None,
            "difference": None,
            "currency": cur,
            "gap_pct": 0,
            "headline": "Cannot compare — one of the prices is missing or zero.",
            "detail": f"{our_label}: {money(our)}. {competitor_label}: {money(theirs)}.",
        }

    if abs(difference) < 0.01 if cur != "PKR" else abs(difference) < 1:
        return {
            "cheaper": "tie",
            "difference_rs": 0,
            "difference": 0,
            "currency": cur,
            "gap_pct": 0,
            "headline": f"Same price. Both charge {money(our)}.",
            "detail": f"{our_label} and {competitor_label} are even.",
        }

    if difference > 0:
        gap_pct = round(difference / our * 100, 2)
        return {
            "cheaper": "competitor",
            "difference_rs": difference,
            "difference": difference,
            "currency": cur,
            "gap_pct": gap_pct,
            "headline": (
                f"{competitor_label} is cheaper by {money(difference)} ({gap_pct}%)."
            ),
            "detail": (
                f"{our_label} sells at {money(our)}. "
                f"{competitor_label} sells at {money(theirs)}."
            ),
        }

    save = round(theirs - our, 2)
    gap_pct = round(save / theirs * 100, 2) if theirs else 0
    return {
        "cheaper": "us",
        "difference_rs": save,
        "difference": save,
        "currency": cur,
        "gap_pct": gap_pct,
        "headline": f"{our_label} is cheaper by {money(save)} ({gap_pct}%).",
        "detail": (
            f"{our_label} sells at {money(our)}. "
            f"{competitor_label} sells at {money(theirs)}."
        ),
    }
