"""v0.5.0 P6：制作台总览与异常收件箱 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from novelvoice_studio.api.deps import (
    AutoProductionServiceDep,
    CastingServiceDep,
    ProductionBatchServiceDep,
    ProductionServiceDep,
)
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.production import (
    AutoProductionRequest,
    ProductionBatchRequest,
    ProductionOverviewOut,
    ReviewBatchResolveRequest,
    ReviewBatchResolveResponse,
    ReviewListResponse,
    ReviewItemOut,
    ReviewResolveRequest,
)

router = APIRouter(tags=["production"])


@router.post(
    "/books/{book_id}/production/auto",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_auto_production(
    book_id: str,
    payload: AutoProductionRequest,
    service: AutoProductionServiceDep,
) -> AnalysisJobOut:
    return service.start(book_id, refresh_bible=payload.refresh_bible)


@router.get("/auto-production/{job_id}", response_model=AnalysisJobOut)
def get_auto_production(job_id: str, service: AutoProductionServiceDep) -> AnalysisJobOut:
    return service.get_job(job_id)


@router.post("/auto-production/{job_id}/cancel", response_model=AnalysisJobOut)
def cancel_auto_production(job_id: str, service: AutoProductionServiceDep) -> AnalysisJobOut:
    return service.cancel(job_id)


@router.post(
    "/books/{book_id}/production/batch",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_production_batch(
    book_id: str,
    payload: ProductionBatchRequest,
    service: ProductionBatchServiceDep,
) -> AnalysisJobOut:
    return service.start(
        book_id,
        stages=payload.stages,
        volume_number=payload.volume_number,
        chapter_ids=payload.chapter_ids,
        only_missing=payload.only_missing,
        adaptation_level=payload.adaptation_level,
    )


@router.get("/production-batches/{job_id}", response_model=AnalysisJobOut)
def get_production_batch(
    job_id: str, service: ProductionBatchServiceDep
) -> AnalysisJobOut:
    return service.get_job(job_id)


@router.post("/production-batches/{job_id}/cancel", response_model=AnalysisJobOut)
def cancel_production_batch(
    job_id: str, service: ProductionBatchServiceDep
) -> AnalysisJobOut:
    return service.cancel(job_id)


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
    chapter_id: str | None = Query(default=None, max_length=32),
    category: str | None = Query(
        default="content", pattern="^(content|system|all)$"
    ),
    limit: int = Query(default=100, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> ReviewListResponse:
    return service.list_reviews(
        book_id=book_id,
        status=review_status,
        severity=severity,
        code=code,
        entity_type=entity_type,
        chapter_id=chapter_id,
        category=category,
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