"""v0.5.0 P2：全书角色圣经与剧情时间线 API。"""

from __future__ import annotations

from fastapi import APIRouter, status

from novelvoice_studio.api.deps import BibleServiceDep
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.bible import (
    BibleAnalysisRequest,
    BibleStateResponse,
    BibleStatusResponse,
)

router = APIRouter(tags=["bible"])


@router.post(
    "/books/{book_id}/bible/analysis",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_book_bible(
    book_id: str,
    service: BibleServiceDep,
    payload: BibleAnalysisRequest | None = None,
) -> AnalysisJobOut:
    chapter_ids = payload.chapter_ids if payload is not None else []
    return service.start_book_bible(book_id, chapter_ids)


@router.get("/books/{book_id}/bible/status", response_model=BibleStatusResponse)
def get_bible_status(
    book_id: str, service: BibleServiceDep
) -> BibleStatusResponse:
    return service.bible_status(book_id)


@router.get("/books/{book_id}/bible", response_model=BibleStateResponse)
def get_bible(book_id: str, service: BibleServiceDep) -> BibleStateResponse:
    return service.get_bible(book_id)


@router.post("/books/{book_id}/bible/analysis/cancel", response_model=AnalysisJobOut)
def cancel_book_bible(
    book_id: str, service: BibleServiceDep
) -> AnalysisJobOut:
    return service.cancel_book_bible(book_id)