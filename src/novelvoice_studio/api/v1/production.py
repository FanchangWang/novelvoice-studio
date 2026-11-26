"""v0.5.0 P6：制作台总览与异常收件箱 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from novelvoice_studio.api.deps import CastingServiceDep, ProductionServiceDep
from novelvoice_studio.schemas.production import (
    ProductionOverviewOut,
    ReviewBatchResolveRequest,
    ReviewBatchResolveResponse,
    ReviewListResponse,
    ReviewItemOut,
    ReviewResolveRequest,
)

router = APIRouter(tags=["production"])


@router.get(
    "/books/{book_id}/production", response_model=ProductionOverviewOut
)
def get_production_overview(
    book_id: str, service: ProductionServiceDep
) -> ProductionOverviewOut:
    return service.overview(book_id)


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    service: ProductionServiceDep,
    book_id: str | None = Query(default=None, max_length=32),
    review_status: str | None = Query(
        default="open", alias="status", pattern="^(open|resolved|ignored)$"
    ),
    severity: str | None = Query(
        default=None, pattern="^(blocking|warning|info)$"
    ),
    code: str | None = Query(default=None, max_length=64),
    entity_type: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> ReviewListResponse:
    return service.list_reviews(
        book_id=book_id,
        status=review_status,
        severity=severity,
        code=code,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )


@router.post("/reviews/{review_id}/resolve", response_model=ReviewItemOut)
def resolve_review(
    review_id: str,
    payload: ReviewResolveRequest,
    service: ProductionServiceDep,
    casting: CastingServiceDep,
) -> ReviewItemOut:
    item, book_id, code = service.resolve_review(review_id, payload)
    if code.startswith("cast_"):
        casting.refresh_status(book_id)
    return item


@router.post(
    "/reviews/batch-resolve", response_model=ReviewBatchResolveResponse
)
def batch_resolve_reviews(
    payload: ReviewBatchResolveRequest,
    service: ProductionServiceDep,
    casting: CastingServiceDep,
) -> ReviewBatchResolveResponse:
    result = service.resolve_reviews(payload)
    if any(code.startswith("cast_") for code in result.codes):
        for book_id in result.book_ids:
            casting.refresh_status(book_id)
    return result