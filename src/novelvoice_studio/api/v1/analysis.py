"""章节/全书 AI 分析与审核状态 API。"""

from __future__ import annotations

from fastapi import APIRouter, status

from novelvoice_studio.api.deps import AnalysisServiceDep
from novelvoice_studio.schemas.analysis import (
    AnalysisJobOut,
    AnalysisRequest,
    AnalysisStatusResponse,
    ChapterAnalysisResponse,
)

router = APIRouter(tags=["analysis"])


@router.post(
    "/books/{book_id}/analysis",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_book_analysis(
    book_id: str,
    service: AnalysisServiceDep,
    payload: AnalysisRequest | None = None,
) -> AnalysisJobOut:
    chapter_ids = payload.chapter_ids if payload is not None else []
    return service.start_book_analysis(book_id, chapter_ids)


@router.get("/books/{book_id}/analysis/status", response_model=AnalysisStatusResponse)
def get_analysis_status(
    book_id: str, service: AnalysisServiceDep
) -> AnalysisStatusResponse:
    return service.analysis_status(book_id)


@router.post("/books/{book_id}/analysis/cancel", response_model=AnalysisJobOut)
def cancel_book_analysis(
    book_id: str, service: AnalysisServiceDep
) -> AnalysisJobOut:
    return service.cancel_book_analysis(book_id)


@router.post(
    "/chapters/{chapter_id}/analysis",
    response_model=ChapterAnalysisResponse,
)
def analyze_chapter(
    chapter_id: str, service: AnalysisServiceDep
) -> ChapterAnalysisResponse:
    service.assert_chapter_available(chapter_id)
    return service.analyze_chapter(chapter_id)