from __future__ import annotations

"""
Registry of all competitor scrapers. Add a new competitor by writing a
new file next to daraz.py (same BaseScraper contract) and registering
it here — nothing else in the codebase needs to change.
"""
from app.scrapers.base import BaseScraper
from app.scrapers.daraz import DarazScraper
from app.scrapers.generic import GenericPageScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    "daraz": DarazScraper,
}


def get_scraper(competitor: str) -> BaseScraper:
    cls = SCRAPERS.get(competitor)
    if cls:
        return cls()
    scraper = GenericPageScraper()
    scraper.competitor_name = competitor or "web"
    return scraper
