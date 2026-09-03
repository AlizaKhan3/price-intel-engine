from __future__ import annotations

#!/usr/bin/env python3
"""
Read-only peek at the tenant catalog MongoDB.

Usage (from repo root, with .env filled in):

    pip install pymongo pydantic-settings
    python scripts/inspect_catalog.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    client = MongoClient(settings.CATALOG_MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[settings.CATALOG_DB_NAME]
    print(f"connected → {settings.CATALOG_DB_NAME}")
    print("collections:")
    for name in sorted(db.list_collection_names()):
        if name.startswith("system."):
            continue
        try:
            count = db[name].estimated_document_count()
        except Exception as exc:
            print(f"  {name:40s} (count skipped: {exc.__class__.__name__})")
            continue
        print(f"  {name:40s} ~{count}")

    products = db[settings.CATALOG_PRODUCTS_COLLECTION]
    sample = products.find_one({"active": True}) or products.find_one()
    if not sample:
        print("no products found")
        return

    print("\nproduct keys:", sorted(sample.keys()))
    interesting = [
        "name",
        "title",
        "price",
        "originalPrice",
        "discountedPrice",
        "after_discount",
        "old_price",
        "category",
        "marketplace",
        "group_id",
        "brand",
        "sku",
        "stock",
        "active",
        "thumbnail",
    ]
    print("\ninteresting fields:")
    for key in interesting:
        if key in sample:
            print(f"  {key}: {sample[key]!r}"[:120])

    print("\ncounts:")
    print("  total     ", products.estimated_document_count())
    print("  active    ", products.count_documents({"active": True}))
    print("  stock > 0 ", products.count_documents({"stock": {"$gt": 0}}))
    print("  with group", products.count_documents({"group_id": {"$ne": None}}))

    for col in (
        settings.CATALOG_CATEGORIES_COLLECTION,
        settings.CATALOG_MARKETPLACES_COLLECTION,
        settings.CATALOG_PRODUCT_GROUPS_COLLECTION,
    ):
        if col in db.list_collection_names():
            one = db[col].find_one()
            print(f"\n{col} sample keys:", sorted(one.keys()) if one else "(empty)")


if __name__ == "__main__":
    main()
