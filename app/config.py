from __future__ import annotations

"""
Platform configuration. Tenant-specific catalog credentials, field maps,
competitors, and alert recipients live on the `tenants` document — not here —
so a second marketplace can be onboarded without a code change.
"""
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- PriceIntel's own database (matches, history, tenants, API keys) ---
    PRICEINTEL_MONGO_URI: str = "mongodb://localhost:27017"
    PRICEINTEL_DB_NAME: str = "price_intel"

    # --- Default tenant catalog (Sadiq today). New customers store this on
    # --- their tenant record instead of sharing these env vars. ---
    CATALOG_MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        validation_alias=AliasChoices("CATALOG_MONGO_URI", "SADIQ_MONGO_URI"),
    )
    CATALOG_DB_NAME: str = Field(
        default="Sadiq-DB",
        validation_alias=AliasChoices("CATALOG_DB_NAME", "SADIQ_DB_NAME"),
    )
    CATALOG_PRODUCTS_COLLECTION: str = Field(
        default="products",
        validation_alias=AliasChoices(
            "CATALOG_PRODUCTS_COLLECTION", "SADIQ_PRODUCTS_COLLECTION"
        ),
    )
    CATALOG_CATEGORIES_COLLECTION: str = Field(
        default="categories",
        validation_alias=AliasChoices(
            "CATALOG_CATEGORIES_COLLECTION", "SADIQ_CATEGORIES_COLLECTION"
        ),
    )
    CATALOG_MARKETPLACES_COLLECTION: str = "marketplaces"
    CATALOG_PRODUCT_GROUPS_COLLECTION: str = "product_groups"

    # --- First tenant, seeded on startup ---
    TENANT_SLUG: str = "sadiq"
    TENANT_NAME: str = "Sadiq.ai"
    TENANT_API_KEY: str = "change-me"
    STOREFRONT_BASE_URL: str = "https://www.sadiq.ai"
    STOREFRONT_PRODUCT_URL_TEMPLATE: str = "https://www.sadiq.ai/product/{id}"

    # Platform admin key — used only to create/list tenants, never given to customers.
    ADMIN_API_KEY: str = "change-me-admin"

    # --- Task queue ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Matching defaults (overridable per tenant) ---
    MATCH_MIN_SCORE: int = 60
    MATCH_AUTO_APPROVE_SCORE: int = 92

    # --- Alerts / email (Gmail SMTP for the default tenant) ---
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "dev@sadiq.ai"
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    ALERT_EMAIL_FROM: str = "dev@sadiq.ai"
    ALERT_EMAIL_TO: str = "dev@sadiq.ai"
    SLACK_WEBHOOK_URL: str | None = None
    ALERT_PRICE_GAP_PCT: float = 5.0

    # --- Scraping defaults ---
    SCRAPER_REQUEST_DELAY_SECONDS: float = 2.0
    SCRAPER_USER_AGENT: str = "PriceIntelBot/1.0 (+mailto:info@sadiq.ai)"
    ENABLED_COMPETITORS: str = "daraz"

    # --- API ---
    API_CORS_ORIGINS: str = "*"
    CATALOG_SYNC_ACTIVE_ONLY: bool = False
    COMPARE_ACTIVE_ONLY: bool = True
    COMPARE_IN_STOCK_ONLY: bool = False

    @property
    def cors_origins(self) -> list[str]:
        raw = self.API_CORS_ORIGINS.strip()
        if raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def enabled_competitors(self) -> list[str]:
        return [item.strip() for item in self.ENABLED_COMPETITORS.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
