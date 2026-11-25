"""音色库 API：候选设计、试听、绑定与维护。"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from novelvoice_studio.api.deps import VoiceServiceDep
from novelvoice_studio.schemas.voice import (
    VoiceDesignRequest,
    VoiceDesignResponse,
    VoiceLibraryResponse,
    VoicePreviewRequest,
    VoiceProfileOut,
    VoiceUpdateRequest,
)

router = APIRouter(tags=["voices"])


@router.get("/books/{book_id}/voices", response_model=VoiceLibraryResponse)
def list_voices(book_id: str, service: VoiceServiceDep) -> VoiceLibraryResponse:
    return service.library(book_id)


@router.post("/books/{book_id}/voices/design", response_model=VoiceDesignResponse)
def design_voices(
    book_id: str, payload: VoiceDesignRequest, service: VoiceServiceDep
) -> VoiceDesignResponse:
    return service.design(book_id, payload)


@router.get("/voices/{voice_id}/audio")
def voice_audio(voice_id: str, service: VoiceServiceDep) -> FileResponse:
    path = service.reference_audio(voice_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{voice_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/voices/{voice_id}/select", response_model=VoiceProfileOut)
def select_voice(voice_id: str, service: VoiceServiceDep) -> VoiceProfileOut:
    return service.select(voice_id)


@router.patch("/voices/{voice_id}", response_model=VoiceProfileOut)
def update_voice(
    voice_id: str, payload: VoiceUpdateRequest, service: VoiceServiceDep
) -> VoiceProfileOut:
    return service.update(voice_id, payload)


@router.delete("/voices/{voice_id}", status_code=204)
def delete_voice(voice_id: str, service: VoiceServiceDep) -> Response:
    service.delete(voice_id)
    return Response(status_code=204)


@router.post("/voices/{voice_id}/preview")
def preview_voice(
    voice_id: str, payload: VoicePreviewRequest, service: VoiceServiceDep
) -> Response:
    audio = service.preview(voice_id, payload)
    return Response(
        content=audio.audio_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-NVS-Duration-Ms": str(audio.duration_ms),
            "X-NVS-Sample-Rate": str(audio.sample_rate),
            "X-NVS-Provider": audio.provider,
            "X-NVS-Is-Mock": "true" if audio.is_mock else "false",
        },
    )