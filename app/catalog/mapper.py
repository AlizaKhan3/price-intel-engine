from __future__ import annotations

"""
Generic catalog field mapper.

Every tenant has a different product schema. Sadiq uses `name` / `group_id` /
`marketplace`; the next customer might use `title` / `sku`. Mapping is data,
not code — stored on the tenant record so onboarding a new marketplace does
not require a deploy.
"""
from datetime import datetime
import re
from typing import Any

from bson import ObjectId

# Matches the live Sadiq `products` collection (see Compass screenshot).
DEFAULT_FIELD_MAP: dict[str, str | list[str]] = {
    "title": ["name", "title"],
    "price": ["after_discount", "price"],
    "original_price": ["originalPrice", "old_price"],
    "image_url": ["thumbnail", "imageUrl", "image"],
    "category_id": "category",
    "marketplace_id": "marketplace",
    "group_id": "group_id",
    "stock": "stock",
    "active": "active",
    "brand": "brand",
    "sku": ["sku", "modelNumber"],
    "barcode": ["barcode", "ean"],
    "description": "description",
    "condition": "condition",
}


def _get(raw: dict, spec: str | list[str] | None, *, skip_zero: bool = False) -> Any:
    if spec is None:
        return None
    keys = spec if isinstance(spec, list) else [spec]
    for key in keys:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if skip_zero and value in (0, 0.0, "0"):
            continue
        return value
    return None


def as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    text = str(value).strip()
    return text or None


def as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "product"


def _storefront_url(template: str, raw: dict, product_id: str, group_id: str | None) -> str:
    title = raw.get("name") or raw.get("title") or "product"
    slug = raw.get("slug") or _slugify(str(title))
    try:
        return template.format(
            id=product_id,
            slug=slug,
            group_id=group_id or product_id,
        )
    except KeyError:
        return f"https://www.sadiq.ai/product-details/{slug}/{group_id or product_id}-{product_id}"


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def project_product(
    raw: dict,
    *,
    tenant_id: str,
    field_map: dict | None = None,
    category_names: dict[str, str] | None = None,
    marketplace_names: dict[str, str] | None = None,
    group_marketplaces: dict[str, str] | None = None,
    product_url_template: str = "https://www.sadiq.ai/product/{id}",
) -> dict:
    """Normalize one vendor catalog document into PriceIntel's internal shape."""
    fmap = {**DEFAULT_FIELD_MAP, **(field_map or {})}
    category_names = category_names or {}
    marketplace_names = marketplace_names or {}
    group_marketplaces = group_marketplaces or {}

    product_id = str(raw["_id"])
    category_id = as_str(_get(raw, fmap.get("category_id")))
    marketplace_id = as_str(_get(raw, fmap.get("marketplace_id")))
    group_id = as_str(_get(raw, fmap.get("group_id")))
    if not marketplace_id and group_id:
        marketplace_id = group_marketplaces.get(group_id)
    stock = as_int(_get(raw, fmap.get("stock")))
    original = _get(raw, fmap.get("original_price"))
    sku = _get(raw, fmap.get("sku"))
    barcode = _get(raw, fmap.get("barcode"))

    return {
        "tenant_id": tenant_id,
        "id": product_id,
        "title": _get(raw, fmap.get("title")) or "",
        "description": _get(raw, fmap.get("description")),
        "brand": _get(raw, fmap.get("brand")),
        "model_number": as_str(sku),
        "barcode": as_str(barcode),
        "category_id": category_id,
        "category": category_names.get(category_id or "") or category_id or "uncategorized",
        "marketplace_id": marketplace_id,
        "marketplace": marketplace_names.get(marketplace_id or "") or marketplace_id,
        "group_id": group_id,
        "price": as_float(_get(raw, fmap.get("price"), skip_zero=True)),
        "original_price": as_float(original) if original is not None else None,
        "currency": "PKR",
        "image_url": _get(raw, fmap.get("image_url")),
        "url": _storefront_url(product_url_template, raw, product_id, group_id),
        "stock": stock,
        "in_stock": stock > 0,
        "active": bool(_get(raw, fmap.get("active"))),
        "condition": _get(raw, fmap.get("condition")),
        "synced_at": datetime.utcnow(),
    }
