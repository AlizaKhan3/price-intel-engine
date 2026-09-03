from __future__ import annotations

"""
Registry of all competitor scrapers. Add a new competitor by writing a
new file next to daraz.py (same BaseScraper contract) and registering
it here — nothing else in the codebase needs to change.
"""
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import DarazScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    "daraz": DarazScraper,
    # "telemart": TelemartScraper,
    # "ishopping": IShoppingScraper,
}


def get_scraper(competitor: str) -> BaseScraper:
    if competitor not in SCRAPERS:
        raise ValueError(f"No scraper registered for competitor={competitor!r}")
    return SCRAPERS[competitor]()
