from __future__ import annotations

"""Tenant lookup, API-key hashing, and first-tenant seed."""
import hashlib
import logging
import secrets
from datetime import datetime

from app.catalog.mapper import DEFAULT_FIELD_MAP
from app.config import get_settings
from app.db import get_priceintel_db

logger = logging.getLogger(__name__)
settings = get_settings()


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def new_api_key(slug: str) -> str:
    return f"pi_live_{slug}_{secrets.token_urlsafe(24)}"


async def find_tenant_by_api_key(raw_key: str) -> dict | None:
    db = get_priceintel_db()
    return await db.tenants.find_one(
        {"api_key_hash": hash_api_key(raw_key), "status": "active"}
    )


async def find_tenant_by_slug(slug: str) -> dict | None:
    db = get_priceintel_db()
    return await db.tenants.find_one({"slug": slug})


async def list_active_tenants() -> list[dict]:
    db = get_priceintel_db()
    return await db.tenants.find({"status": "active"}).to_list(length=None)


async def create_tenant(
    *,
    slug: str,
    name: str,
    catalog: dict,
    competitors: list[str] | None = None,
    matching: dict | None = None,
    alerts: dict | None = None,
    storefront_base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[dict, str]:
    db = get_priceintel_db()
    raw_key = api_key or new_api_key(slug)
    doc = {
        "slug": slug,
        "name": name,
        "status": "active",
        "api_key_hash": hash_api_key(raw_key),
        "api_key_prefix": raw_key[:16],
        "storefront_base_url": storefront_base_url,
        "catalog": catalog,
        "competitors": competitors or [],
        "matching": matching
        or {
            "min_score": settings.MATCH_MIN_SCORE,
            "auto_approve_score": settings.MATCH_AUTO_APPROVE_SCORE,
        },
        "alerts": alerts
        or {
            "email_from": settings.ALERT_EMAIL_FROM,
            "email_to": settings.ALERT_EMAIL_TO,
            "slack_webhook_url": settings.SLACK_WEBHOOK_URL,
            "price_gap_pct": settings.ALERT_PRICE_GAP_PCT,
        },
        "created_at": datetime.utcnow(),
    }
    await db.tenants.insert_one(doc)
    return doc, raw_key


async def seed_default_tenant() -> None:
    """
    Ensure the Sadiq tenant exists so local/dev works from .env alone.
    Safe to call on every startup.
    """
    existing = await find_tenant_by_slug(settings.TENANT_SLUG)
    if existing:
        if (
            settings.TENANT_API_KEY
            and settings.TENANT_API_KEY != "change-me"
            and existing.get("api_key_hash") != hash_api_key(settings.TENANT_API_KEY)
        ):
            db = get_priceintel_db()
            await db.tenants.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "api_key_hash": hash_api_key(settings.TENANT_API_KEY),
                        "api_key_prefix": settings.TENANT_API_KEY[:16],
                    }
                },
            )
            logger.info("Rotated API key hash for tenant slug=%s", settings.TENANT_SLUG)
        return

    catalog = {
        "source": "mongodb",
        "mongo_uri": None,  # fall back to CATALOG_MONGO_URI env
        "db_name": settings.CATALOG_DB_NAME,
        "products_collection": settings.CATALOG_PRODUCTS_COLLECTION,
        "categories_collection": settings.CATALOG_CATEGORIES_COLLECTION,
        "marketplaces_collection": settings.CATALOG_MARKETPLACES_COLLECTION,
        "product_groups_collection": settings.CATALOG_PRODUCT_GROUPS_COLLECTION,
        "field_map": DEFAULT_FIELD_MAP,
        "product_url_template": settings.STOREFRONT_PRODUCT_URL_TEMPLATE,
        "sync_active_only": settings.CATALOG_SYNC_ACTIVE_ONLY,
    }
    _, key = await create_tenant(
        slug=settings.TENANT_SLUG,
        name=settings.TENANT_NAME,
        catalog=catalog,
        competitors=settings.enabled_competitors,
        storefront_base_url=settings.STOREFRONT_BASE_URL,
        api_key=settings.TENANT_API_KEY,
    )
    logger.info(
        "Seeded default tenant slug=%s api_key_prefix=%s",
        settings.TENANT_SLUG,
        key[:16],
    )


def tenant_id(tenant: dict) -> str:
    return tenant.get("slug") or str(tenant["_id"])
