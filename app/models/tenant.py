from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CatalogSource(str, Enum):
    MONGODB = "mongodb"
    # REST_API / CSV can be added later without changing tenant identity.


class CatalogConfig(BaseModel):
    source: CatalogSource = CatalogSource.MONGODB
    mongo_uri: str | None = None
    db_name: str
    products_collection: str = "products"
    categories_collection: str = "categories"
    marketplaces_collection: str = "marketplaces"
    product_groups_collection: str = "product_groups"
    field_map: dict = Field(default_factory=dict)
    product_url_template: str = "https://www.sadiq.ai/product/{id}"
    sync_active_only: bool = False


class MatchingConfig(BaseModel):
    min_score: int = 60
    auto_approve_score: int = 92


class AlertConfig(BaseModel):
    email_from: str | None = None
    email_to: str | None = None
    slack_webhook_url: str | None = None
    price_gap_pct: float = 5.0


class TenantCreate(BaseModel):
    """Payload a platform admin sends to onboard a new marketplace customer."""
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    name: str
    storefront_base_url: str | None = None
    catalog: CatalogConfig
    competitors: list[str] = Field(default_factory=list)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)


class TenantPublic(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    storefront_base_url: str | None = None
    competitors: list[str] = []
    matching: MatchingConfig
    created_at: datetime
