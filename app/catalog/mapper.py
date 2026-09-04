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
# Selling price is computed in project_product — prefer after_discount when it
# is below list price; otherwise apply `discount` % (storefront does this too).
DEFAULT_FIELD_MAP: dict[str, str | list[str]] = {
    "title": ["name", "title"],
    "price": ["after_discount", "price"],
    "original_price": ["old_price", "originalPrice", "price"],
    "discount_pct": "discount",
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


def selling_price(raw: dict, fmap: dict | None = None) -> tuple[float, float | None]:
    """
    Return (customer-facing price, list/original price).

    Sadiq storefront shows the discounted amount. Catalog sometimes leaves
    `after_discount` equal to list price while `discount` still holds the %.
    Prefer a real after_discount; otherwise apply the percent.
    """
    fmap = {**DEFAULT_FIELD_MAP, **(fmap or {})}
    listed = as_float(_get(raw, fmap.get("price"), skip_zero=True))
    after_keys = fmap.get("price")
    # First key in price map is the sale field (after_discount for Sadiq).
    after_spec = after_keys[0] if isinstance(after_keys, list) and after_keys else after_keys
    after = as_float(_get(raw, after_spec, skip_zero=True)) if after_spec else 0.0
    original = as_float(_get(raw, fmap.get("original_price"), skip_zero=True))
    discount_pct = as_float(_get(raw, fmap.get("discount_pct")))
    list_price = max(original, listed, after)

    sale = after if after > 0 else listed
    if discount_pct > 0 and list_price > 0:
        from_pct = round(list_price * (1 - min(discount_pct, 100) / 100), 2)
        # after_discount not applied (still ~list) → use % like the storefront.
        if sale <= 0 or sale >= list_price * 0.99:
            sale = from_pct
        else:
            sale = min(sale, from_pct)

    if sale <= 0:
        sale = listed or after or list_price
    original_out = list_price if list_price > sale > 0 else (original or None)
    if original_out is not None and original_out <= 0:
        original_out = None
    return sale, original_out


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
    price, original_price = selling_price(raw, fmap)
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
        "price": price,
        "original_price": original_price,
        "currency": "PKR",
        "image_url": _get(raw, fmap.get("image_url")),
        "url": _storefront_url(product_url_template, raw, product_id, group_id),
        "stock": stock,
        "in_stock": stock > 0,
        "active": bool(_get(raw, fmap.get("active"))),
        "condition": _get(raw, fmap.get("condition")),
        "synced_at": datetime.utcnow(),
    }
