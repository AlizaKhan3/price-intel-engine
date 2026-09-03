from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from app.api.deps import get_current_tenant
from app.api.serialize import dump_docs
from app.db import get_priceintel_db
from app.services.scrape import scrape_product_url
from app.services.tenants import tenant_id as tid

router = APIRouter(prefix="/scrape", tags=["scrape"])


class ScrapeOneBody(BaseModel):
    product_id: str = Field(..., description="Your catalog product _id, e.g. 64e4a35038023b2b950bf30c")
    competitor_url: HttpUrl = Field(..., description="Daraz product page, https://www.daraz.pk/products/...")
    competitor: str = "daraz"
    auto_approve: bool = Field(
        default=True,
        description="If true, treat this URL as the correct match and compute the price gap immediately.",
    )


@router.post(
    "/one",
    summary="Scrape one competitor product URL",
    description=(
        "Daraz blocks catalog search in robots.txt, so you paste a Daraz **product** URL. "
        "We open that page, read title + price, attach it to your product, and return the gap."
    ),
)
async def scrape_one(body: ScrapeOneBody, tenant: dict = Depends(get_current_tenant)):
    try:
        return await scrape_product_url(
            tenant,
            product_id=body.product_id,
            competitor=body.competitor,
            competitor_url=str(body.competitor_url),
            auto_approve=body.auto_approve,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
