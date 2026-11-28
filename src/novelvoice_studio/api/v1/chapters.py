"""章节 REST API。"""

from __future__ import annotations

from fastapi import APIRouter

from novelvoice_studio.api.deps import ChapterServiceDep
from novelvoice_studio.schemas.chapters import (
    ChapterDetail,
    ChapterMergeRequest,
    ChapterMutationResponse,
    ChapterSplitRequest,
    ChapterSummary,
    ChapterUpdate,
)
from novelvoice_studio.services.serializers import to_chapter_summary

router = APIRouter(prefix="/chapters", tags=["chapters"])


@router.post("/merge", response_model=ChapterMutationResponse)
def merge_chapters(
    payload: ChapterMergeRequest,
    service: ChapterServiceDep,
) -> ChapterMutationResponse:
    _first, book = service.get_with_book(payload.chapter_ids[0])
    target, removed_ids, summaries = service.merge(book, payload.chapter_ids, payload.new_title)
    return ChapterMutationResponse(
        chapter=to_chapter_summary(target),
        affected_chapter_ids=[target.id, *removed_ids],
        items=summaries,
    )


@router.get("/{chapter_id}", response_model=ChapterDetail)
def get_chapter(chapter_id: str, service: ChapterServiceDep) -> ChapterDetail:
    chapter, book = service.get_with_book(chapter_id)
    return service.detail(chapter, book)


@router.patch("/{chapter_id}", response_model=ChapterSummary)
def update_chapter(
    chapter_id: str,
    payload: ChapterUpdate,
    service: ChapterServiceDep,
) -> ChapterSummary:
    return service.update_title(
        chapter_id,
        title=payload.title,
        chapter_kind=payload.chapter_kind,
        analysis_included=payload.analysis_included,
        classification_status=payload.classification_status,
    )


@router.post("/{chapter_id}/split", response_model=ChapterMutationResponse)
def split_chapter(
    chapter_id: str,
    payload: ChapterSplitRequest,
    service: ChapterServiceDep,
) -> ChapterMutationResponse:
    created, summaries = service.split(chapter_id, payload.paragraph_index, payload.new_title)
    return ChapterMutationResponse(
        chapter=to_chapter_summary(created),
        affected_chapter_ids=[chapter_id, created.id],
        items=summaries,
    )