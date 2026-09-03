from __future__ import annotations

"""Human-readable price comparison."""


def explain_prices(
    our_price: float,
    competitor_price: float,
    *,
    our_label: str = "Your store",
    competitor_label: str = "Competitor",
) -> dict:
    our = float(our_price or 0)
    theirs = float(competitor_price or 0)
    difference = round(our - theirs, 2)

    if our <= 0 or theirs <= 0:
        return {
            "cheaper": None,
            "difference_rs": None,
            "gap_pct": 0,
            "headline": "Cannot compare — one of the prices is missing or zero.",
            "detail": f"{our_label}: Rs. {our:,.0f}. {competitor_label}: Rs. {theirs:,.0f}.",
        }

    if abs(difference) < 1:
        return {
            "cheaper": "tie",
            "difference_rs": 0,
            "gap_pct": 0,
            "headline": f"Same price. Both charge Rs. {our:,.0f}.",
            "detail": f"{our_label} and {competitor_label} are even.",
        }

    if difference > 0:
        gap_pct = round(difference / our * 100, 2)
        return {
            "cheaper": "competitor",
            "difference_rs": difference,
            "gap_pct": gap_pct,
            "headline": (
                f"{competitor_label} is cheaper by Rs. {difference:,.0f} ({gap_pct}%)."
            ),
            "detail": (
                f"{our_label} sells at Rs. {our:,.0f}. "
                f"{competitor_label} sells at Rs. {theirs:,.0f}."
            ),
        }

    save = round(theirs - our, 2)
    gap_pct = round(save / theirs * 100, 2) if theirs else 0
    return {
        "cheaper": "us",
        "difference_rs": save,
        "gap_pct": gap_pct,
        "headline": f"{our_label} is cheaper by Rs. {save:,.0f} ({gap_pct}%).",
        "detail": (
            f"{our_label} sells at Rs. {our:,.0f}. "
            f"{competitor_label} sells at Rs. {theirs:,.0f}."
        ),
    }
