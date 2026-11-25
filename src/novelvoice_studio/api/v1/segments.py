"""片段读取与人工审核 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from novelvoice_studio.api.deps import SegmentServiceDep
from novelvoice_studio.schemas.analysis import (
    SegmentBatchUpdate,
    SegmentBatchUpdateResponse,
    SegmentListResponse,
    SegmentOut,
    SegmentUpdate,
)

router = APIRouter(tags=["segments"])


@router.get("/chapters/{chapter_id}/segments", response_model=SegmentListResponse)
def list_segments(
    chapter_id: str,
    service: SegmentServiceDep,
    review_status: str | None = Query(default=None),
    segment_type: str | None = Query(default=None),
    speaker_type: str | None = Query(default=None),
    low_confidence: bool = Query(default=False),
) -> SegmentListResponse:
    return service.list_for_chapter(
        chapter_id,
        review_status=review_status,
        segment_type=segment_type,
        speaker_type=speaker_type,
        low_confidence=low_confidence,
    )


@router.post("/segments/batch-update", response_model=SegmentBatchUpdateResponse)
def batch_update_segments(
    payload: SegmentBatchUpdate, service: SegmentServiceDep
) -> SegmentBatchUpdateResponse:
    return service.batch_update(payload)


@router.get("/segments/{segment_id}", response_model=SegmentOut)
def get_segment(segment_id: str, service: SegmentServiceDep) -> SegmentOut:
    return service.get_segment(segment_id)


@router.patch("/segments/{segment_id}", response_model=SegmentOut)
def update_segment(
    segment_id: str, payload: SegmentUpdate, service: SegmentServiceDep
) -> SegmentOut:
    return service.update_segment(segment_id, payload)