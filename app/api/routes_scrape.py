from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from app.api.deps import get_current_tenant
from app.api.serialize import dump_docs
from app.db import get_priceintel_db
from app.services.scrape import compare_storefront_and_competitor, scrape_product_url
from app.services.tenants import tenant_id as tid
from app.services.urls import competitor_from_url

router = APIRouter(prefix="/scrape", tags=["scrape"])


class ScrapeOneBody(BaseModel):
    product_id: str | None = Field(default=None, description="Catalog product _id")
    storefront_url: HttpUrl | None = Field(
        default=None,
        description="Your product page, e.g. https://www.sadiq.ai/product-details/.../{group}-{id}",
    )
    competitor_url: HttpUrl
    competitor: str | None = Field(
        default=None,
        description="Optional. If omitted, taken from the competitor URL hostname (daraz, telemart, …).",
    )
    auto_approve: bool = True


class CompareLinksBody(BaseModel):
    storefront_url: HttpUrl
    competitor_url: HttpUrl


@router.post(
    "/one",
    summary="Scrape one competitor product URL",
    description="Paste your product id or storefront URL plus any competitor product URL.",
)
async def scrape_one(body: ScrapeOneBody, tenant: dict = Depends(get_current_tenant)):
    try:
        if body.storefront_url:
            return await compare_storefront_and_competitor(
                tenant,
                storefront_url=str(body.storefront_url),
                competitor_url=str(body.competitor_url),
                auto_approve=body.auto_approve,
            )
        if not body.product_id:
            raise ValueError("Provide product_id or storefront_url.")
        return await scrape_product_url(
            tenant,
            product_id=body.product_id,
            competitor=body.competitor or competitor_from_url(str(body.competitor_url)),
            competitor_url=str(body.competitor_url),
            auto_approve=body.auto_approve,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/compare-links",
    summary="Compare two product links",
    description="Your storefront URL + any competitor product URL. Returns a plain-language result.",
)
async def compare_links(body: CompareLinksBody, tenant: dict = Depends(get_current_tenant)):
    try:
        return await compare_storefront_and_competitor(
            tenant,
            storefront_url=str(body.storefront_url),
            competitor_url=str(body.competitor_url),
            auto_approve=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/listings", summary="Competitor listings already scraped")
async def list_listings(
    tenant: dict = Depends(get_current_tenant),
    competitor: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
):
    db = get_priceintel_db()
    query: dict = {"tenant_id": tid(tenant)}
    if competitor:
        query["competitor"] = competitor
    items = await db.competitor_listings.find(query).sort("last_scraped_at", -1).limit(limit).to_list(length=limit)
    return {"items": dump_docs(items)}
