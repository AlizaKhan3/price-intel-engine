from __future__ import annotations

"""Parse storefront URLs and competitor hostnames."""
import re
from urllib.parse import urlparse

OBJECT_ID = re.compile(r"[a-fA-F0-9]{24}")
SADIQ_PAIR = re.compile(
    r"/product-details/[^/]+/([a-fA-F0-9]{24})-([a-fA-F0-9]{24})",
    re.I,
)
SADIQ_SLUG = re.compile(r"/product-details/([^/]+)/", re.I)
SADIQ_SINGLE = re.compile(r"/product(?:-details)?/([a-fA-F0-9]{24})/?$", re.I)

HOST_ALIASES = {
    "daraz.pk": "daraz",
    "www.daraz.pk": "daraz",
    "telemart.pk": "telemart",
    "www.telemart.pk": "telemart",
    "ishopping.pk": "ishopping",
    "www.ishopping.pk": "ishopping",
    "amazon.com": "amazon",
    "www.amazon.com": "amazon",
    "alfatah.pk": "alfatah",
    "www.alfatah.pk": "alfatah",
    "priceoye.pk": "priceoye",
    "www.priceoye.pk": "priceoye",
    "shophive.com": "shophive",
    "www.shophive.com": "shophive",
    "goto.com.pk": "goto",
    "www.goto.com.pk": "goto",
    "yayvo.com": "yayvo",
    "www.yayvo.com": "yayvo",
    "homegadgets.pk": "homegadgets",
    "www.homegadgets.pk": "homegadgets",
    "metrocity.pk": "metrocity",
    "www.metrocity.pk": "metrocity",
    "thegadgetsgallery.com": "gadgetsgallery",
    "www.thegadgetsgallery.com": "gadgetsgallery",
    "esentiments.pk": "esentiments",
    "www.esentiments.pk": "esentiments",
    "smartaccessories.pk": "smartaccessories",
    "www.smartaccessories.pk": "smartaccessories",
    "apricot.com.pk": "apricot",
    "www.apricot.com.pk": "apricot",
    "shopperspk.com": "shopperspk",
    "www.shopperspk.com": "shopperspk",
    "homducts.pk": "homducts",
    "www.homducts.pk": "homducts",
    "kiswa.pk": "kiswa",
    "www.kiswa.pk": "kiswa",
}


def product_id_from_storefront_url(url: str) -> str:
    text = (url or "").strip()
    match = SADIQ_PAIR.search(text)
    if match:
        return match.group(2)
    match = SADIQ_SINGLE.search(text)
    if match:
        return match.group(1)
    ids = OBJECT_ID.findall(text)
    if ids:
        return ids[-1]
    raise ValueError(
        "Could not read a product id from that storefront URL. "
        "Use a product-details link like .../product-details/name/{groupId}-{productId}"
    )


def slug_from_storefront_url(url: str) -> str | None:
    match = SADIQ_SLUG.search(url or "")
    return match.group(1) if match else None


def is_catalog_storefront_url(url: str) -> bool:
    """True when the URL looks like our tenant catalog storefront (Sadiq-style)."""
    text = (url or "").strip()
    host = (urlparse(text).hostname or "").lower()
    if "sadiq.ai" in host:
        return True
    return bool(SADIQ_PAIR.search(text) or SADIQ_SINGLE.search(text))


def external_product_id(url: str) -> str:
    """Stable id for a scraped external product page (24 hex chars)."""
    import hashlib

    digest = hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()
    return digest[:24]


def slug_from_any_url(url: str) -> str | None:
    """Best-effort product slug from path (works for Shopify, Daraz, generic shops)."""
    sadiq = slug_from_storefront_url(url)
    if sadiq:
        return sadiq
    path = (urlparse(url or "").path or "").rstrip("/")
    if not path or path == "/":
        return None
    segment = path.split("/")[-1]
    # Daraz: slug-i123.html → slug
    segment = re.sub(r"-i\d+.*$", "", segment, flags=re.I)
    segment = re.sub(r"\.html?$", "", segment, flags=re.I)
    return segment or None


def competitor_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in HOST_ALIASES:
        return HOST_ALIASES[host]
    if host.startswith("www."):
        host = host[4:]
    if host in HOST_ALIASES:
        return HOST_ALIASES[host]
    return host.split(".")[0] if host else "competitor"


def competitor_label(slug: str) -> str:
    names = {
        "daraz": "Daraz",
        "telemart": "Telemart",
        "ishopping": "iShopping",
        "amazon": "Amazon",
        "alfatah": "Alfatah",
        "priceoye": "PriceOye",
        "shophive": "Shophive",
        "goto": "Goto",
        "yayvo": "Yayvo",
        "homegadgets": "HomeGadgets",
        "metrocity": "MetroCity",
        "gadgetsgallery": "Gadgets Gallery",
        "esentiments": "Esentiments",
        "smartaccessories": "Smart Accessories",
        "apricot": "Apricot",
        "shopperspk": "ShoppersPk",
        "homducts": "Homducts",
        "kiswa": "Kiswa",
    }
    return names.get(slug, slug.replace("-", " ").title())
