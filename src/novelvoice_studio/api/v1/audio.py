"""单段语音生成与音频版本 API。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from novelvoice_studio.api.deps import AudioServiceDep
from novelvoice_studio.schemas.voice import (
    AudioVersionListResponse,
    SegmentAudioOut,
)

router = APIRouter(tags=["audio"])


@router.post("/segments/{segment_id}/generate", response_model=SegmentAudioOut)
def generate_segment_audio(
    segment_id: str, service: AudioServiceDep
) -> SegmentAudioOut:
    return service.generate(segment_id)


@router.get("/segments/{segment_id}/audio-versions", response_model=AudioVersionListResponse)
def list_audio_versions(
    segment_id: str, service: AudioServiceDep
) -> AudioVersionListResponse:
    return service.versions(segment_id)


@router.get("/segments/{segment_id}/audio")
def current_segment_audio(segment_id: str, service: AudioServiceDep) -> FileResponse:
    path = service.current_audio_path(segment_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{segment_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/audio-assets/{asset_id}/audio")
def asset_audio(asset_id: str, service: AudioServiceDep) -> FileResponse:
    path = service.asset_audio_path(asset_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{asset_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/audio-assets/{asset_id}/restore", response_model=SegmentAudioOut)
def restore_audio_version(asset_id: str, service: AudioServiceDep) -> SegmentAudioOut:
    return service.restore(asset_id)