from __future__ import annotations

"""
Internal documents stored in the PriceIntel database.

Catalog fields are a *normalized projection* of each tenant's own products
collection — never the raw vendor schema.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MatchTier(str, Enum):
    CATALOG_GROUP = "catalog_group"  # same group_id already in the tenant catalog
    RULE_BASED = "rule_based"
    FUZZY_TEXT = "fuzzy_text"
    EMBEDDING = "embedding"
    MANUAL = "manual"


class MatchStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CatalogProduct(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    tenant_id: str
    id: str
    title: str
    brand: str | None = None
    model_number: str | None = None
    barcode: str | None = None
    category: str
    category_id: str | None = None
    marketplace: str | None = None
    marketplace_id: str | None = None
    group_id: str | None = None
    price: float
    original_price: float | None = None
    currency: str = "PKR"
    image_url: str | None = None
    url: str
    stock: int = 0
    in_stock: bool = False
    active: bool = True
    condition: str | None = None


class CompetitorListing(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    tenant_id: str = ""
    id: str | None = None
    competitor: str
    competitor_product_id: str
    title: str
    brand: str | None = None
    model_number: str | None = None
    barcode: str | None = None
    category: str | None = None
    price: float
    currency: str = "PKR"
    in_stock: bool = True
    image_url: str | None = None
    url: str
    source: str = "scrape"  # scrape | catalog
    last_scraped_at: datetime = Field(default_factory=datetime.utcnow)


class ProductMatch(BaseModel):
    tenant_id: str
    id: str | None = None
    product_id: str
    competitor_listing_id: str
    tier: MatchTier
    confidence: float
    status: MatchStatus = MatchStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class PriceSnapshot(BaseModel):
    tenant_id: str
    listing_id: str
    price: float
    currency: str = "PKR"
    in_stock: bool = True
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class Alert(BaseModel):
    tenant_id: str
    id: str | None = None
    product_id: str
    competitor: str
    our_price: float
    competitor_price: float
    gap_pct: float
    created_at: datetime = Field(default_factory=datetime.utcnow)
    acknowledged: bool = False
